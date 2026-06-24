from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from .models import OptionSide, StrategySettings, Trade, TradeResult
from .oi_analysis import classify_regime, gamma_context
from .signal_engine import LOT_SIZE, MarketContext, Signal, capital_lots, spread_aware_slippage


IST = ZoneInfo("Asia/Kolkata")
BROKERAGE_PER_LOT = 40.0


@dataclass
class OpenPosition:
    id: str
    contract: str
    side: OptionSide
    strike: int
    quantity: int
    lots: int
    entry_price: float
    target_price: float
    stop_price: float
    entry_time: datetime
    expiry: str
    time_stop_at: datetime
    regime_at_entry: str
    base_stop_price: float
    peak_ltp: float
    trail_armed: bool
    trail_stop_price: Optional[float]
    trail_enabled: bool


@dataclass
class DaySession:
    trading_date: date
    trades_today: int = 0
    consecutive_losses: int = 0
    realized_pnl: float = 0.0
    cooldown_until: Optional[datetime] = None
    halted: bool = False
    halt_reason: str = ""


def timeframe_minutes(timeframe: str) -> int:
    return int(timeframe.replace("m", ""))


def quote_for_strike(context: MarketContext, strike: int, side: OptionSide):
    from .signal_engine import OptionQuote, quote_from_chain_row

    key = f"{strike:.6f}"
    row = (context.chain_oc or {}).get(key)
    if not row:
        return context.atm_ce if side == "CE" else context.atm_pe
    payload = row.get("ce") if side == "CE" else row.get("pe")
    if not payload:
        return OptionQuote(0, 0, 0, 0, 0, 0)
    return quote_from_chain_row(payload)


def estimate_brokerage(lots: int) -> float:
    return BROKERAGE_PER_LOT * lots


def _snap_exit_levels(
    entry_price: float,
    settings: StrategySettings,
    context: MarketContext,
) -> tuple[float, float, str, bool]:
    ema_gap = abs(context.ema_9 - context.ema_15)
    regime = classify_regime(
        ema_gap,
        context.session_high,
        context.session_low,
        context.atr_14,
        settings.strong_trend_gap,
        settings.gamma_range_atr_ratio,
    )
    gamma = gamma_context(context.spot, context.gamma_flip)
    regime_label = f"{regime}/{gamma}"
    use_trend_exits = settings.dynamic_exits_enabled and (regime == "TRENDING" or gamma == "NEG_GAMMA")

    if use_trend_exits:
        target_delta = settings.target_rupees * settings.target_trend_multiplier
        stop_delta = settings.stop_loss_rupees * settings.stop_trend_multiplier
        trail_on = settings.trail_enabled
    else:
        target_delta = settings.target_rupees
        stop_delta = settings.stop_loss_rupees
        trail_on = False

    target_price = entry_price + target_delta
    base_stop_price = entry_price - stop_delta
    return target_price, base_stop_price, regime_label, trail_on


@dataclass
class PaperBroker:
    open_position: Optional[OpenPosition] = None
    trades: list[Trade] = field(default_factory=list)
    day: Optional[DaySession] = None
    _last_entry_signal_id: Optional[str] = None

    def _ensure_day(self, now: datetime, settings: StrategySettings) -> DaySession:
        today = now.date()
        if self.day is None or self.day.trading_date != today:
            self.day = DaySession(trading_date=today)
            self.trades = []
            self.open_position = None
            self._last_entry_signal_id = None
        return self.day

    def remaining_budget(self, settings: StrategySettings) -> float:
        if self.day is None:
            return settings.daily_risk
        return max(0.0, settings.daily_risk + self.day.realized_pnl)

    def can_enter(self, now: datetime, settings: StrategySettings) -> tuple[bool, str]:
        day = self.day
        if day is None:
            return True, ""
        if self.open_position is not None:
            return False, "Open position — one at a time"
        if day.halted:
            return False, day.halt_reason or "Daily halt active"
        # Re-entry cooldown check disabled
        # if day.cooldown_until and now < day.cooldown_until:
        #     return False, "Re-entry cooldown after stop-out"
        if not settings.use_full_capital:
            stop_risk = settings.stop_loss_rupees * LOT_SIZE
            if self.remaining_budget(settings) < stop_risk:
                return False, "Daily risk budget exhausted"
        return True, ""

    def _apply_trade_result(self, settings: StrategySettings, pnl: float, result: TradeResult) -> None:
        if self.day is None:
            return
        self.day.trades_today += 1
        self.day.realized_pnl += pnl
        if pnl < 0:
            self.day.consecutive_losses += 1
        else:
            self.day.consecutive_losses = 0
        if self.day.consecutive_losses >= settings.max_consecutive_losses:
            self.day.halted = True
            self.day.halt_reason = f"{settings.max_consecutive_losses} consecutive losses — halted"
        if not settings.use_full_capital and self.remaining_budget(settings) <= 0:
            self.day.halted = True
            self.day.halt_reason = "Daily risk budget exhausted"

    def _cooldown_until(self, now: datetime, settings: StrategySettings) -> datetime:
        minutes = timeframe_minutes(settings.timeframe) * settings.reentry_cooldown_candles
        return now + timedelta(minutes=minutes)

    def _time_stop_at(self, entry_time: datetime, settings: StrategySettings, expiry: str) -> datetime:
        candles = settings.time_stop_candles
        return entry_time + timedelta(minutes=timeframe_minutes(settings.timeframe) * candles)

    def _update_trailing(self, position: OpenPosition, ltp: float, settings: StrategySettings) -> None:
        position.peak_ltp = max(position.peak_ltp, ltp)
        if not position.trail_enabled or not settings.trail_enabled:
            return
        if position.peak_ltp - position.entry_price >= settings.trail_trigger_rupees:
            position.trail_armed = True
        if position.trail_armed:
            trail = position.peak_ltp - settings.trail_distance_rupees
            position.trail_stop_price = max(position.base_stop_price, trail)

    def try_enter(
        self,
        now: datetime,
        settings: StrategySettings,
        context: MarketContext,
        signal: Signal,
        session_running: bool,
    ) -> Optional[OpenPosition]:
        self._ensure_day(now, settings)
        if not session_running:
            return None
        if signal.status != "Taken" or signal.side is None:
            return None
        if signal.id == self._last_entry_signal_id:
            return self.open_position
        can, reason = self.can_enter(now, settings)
        if not can:
            return None

        quote = context.atm_ce if signal.side == "CE" else context.atm_pe
        lots = capital_lots(settings, quote.ltp)
        if lots < 1:
            return None

        entry_slip = spread_aware_slippage(quote.bid, quote.ask, settings.fill_slippage_rupees)
        entry_price = quote.ltp + entry_slip
        target_price, base_stop_price, regime_label, trail_on = _snap_exit_levels(
            entry_price,
            settings,
            context,
        )
        quantity = lots * LOT_SIZE
        contract = f"NIFTY {context.atm_strike} {signal.side}"

        position = OpenPosition(
            id=str(uuid4()),
            contract=contract,
            side=signal.side,
            strike=context.atm_strike,
            quantity=quantity,
            lots=lots,
            entry_price=entry_price,
            target_price=target_price,
            stop_price=base_stop_price,
            entry_time=now,
            expiry=context.expiry,
            time_stop_at=self._time_stop_at(now, settings, context.expiry),
            regime_at_entry=regime_label,
            base_stop_price=base_stop_price,
            peak_ltp=entry_price,
            trail_armed=False,
            trail_stop_price=None,
            trail_enabled=trail_on,
        )
        self.open_position = position
        self._last_entry_signal_id = signal.id
        return position

    def _close_position(
        self,
        now: datetime,
        settings: StrategySettings,
        context: MarketContext,
        result: TradeResult,
        exit_ltp: float,
    ) -> Optional[Trade]:
        position = self.open_position
        if position is None:
            return None

        quote = quote_for_strike(context, position.strike, position.side)
        exit_slip = spread_aware_slippage(quote.bid, quote.ask, settings.fill_slippage_rupees)
        exit_price = max(0.05, exit_ltp - exit_slip)
        gross = (exit_price - position.entry_price) * position.quantity
        brokerage = estimate_brokerage(position.lots)
        pnl = gross - brokerage

        trade = Trade(
            id=position.id,
            entry_time=position.entry_time.strftime("%H:%M"),
            exit_time=now.strftime("%H:%M"),
            contract=position.contract,
            side=position.side,
            quantity=position.quantity,
            entry_price=round(position.entry_price, 2),
            exit_price=round(exit_price, 2),
            result=result,
            pnl=round(pnl, 2),
        )
        self.trades.append(trade)
        self.open_position = None
        self._apply_trade_result(settings, pnl, result)

        if result in ("Stop", "Trail"):
            if self.day is not None:
                self.day.cooldown_until = self._cooldown_until(now, settings)

        return trade

    def manage_exits(self, now: datetime, settings: StrategySettings, context: MarketContext) -> Optional[Trade]:
        position = self.open_position
        if position is None:
            return None

        quote = quote_for_strike(context, position.strike, position.side)
        ltp = quote.ltp
        if ltp <= 0:
            return None

        self._update_trailing(position, ltp, settings)

        if ltp >= position.target_price:
            return self._close_position(now, settings, context, "Target", ltp)
        if ltp <= position.base_stop_price:
            return self._close_position(now, settings, context, "Stop", ltp)
        if (
            position.trail_armed
            and position.trail_stop_price is not None
            and ltp <= position.trail_stop_price
        ):
            return self._close_position(now, settings, context, "Trail", ltp)
        if now >= position.time_stop_at:
            return self._close_position(now, settings, context, "Time Exit", ltp)

        return None

    def open_position_label(self) -> Optional[str]:
        if self.open_position is None:
            return None
        pos = self.open_position
        trail_part = ""
        if pos.trail_enabled:
            if pos.trail_armed and pos.trail_stop_price is not None:
                trail_part = f" · trail {pos.trail_stop_price:.2f} (armed)"
            else:
                trail_part = " · trail pending"
        return (
            f"{pos.contract} @ Rs {pos.entry_price:.2f} · TP {pos.target_price:.2f} · "
            f"SL {pos.base_stop_price:.2f}{trail_part} · {pos.lots} lot · {pos.regime_at_entry}"
        )

    def open_position_trade(self, context: MarketContext, settings: StrategySettings) -> Optional[Trade]:
        position = self.open_position
        if position is None:
            return None

        quote = quote_for_strike(context, position.strike, position.side)
        ltp = quote.ltp
        if ltp > 0:
            self._update_trailing(position, ltp, settings)

        unrealized = 0.0
        mark_price: Optional[float] = None
        if ltp > 0:
            exit_slip = spread_aware_slippage(quote.bid, quote.ask, settings.fill_slippage_rupees)
            mark_price = max(0.05, ltp - exit_slip)
            gross = (mark_price - position.entry_price) * position.quantity
            unrealized = gross - estimate_brokerage(position.lots)

        return Trade(
            id=position.id,
            entry_time=position.entry_time.strftime("%H:%M"),
            exit_time=None,
            contract=position.contract,
            side=position.side,
            quantity=position.quantity,
            entry_price=round(position.entry_price, 2),
            exit_price=round(mark_price, 2) if ltp > 0 else None,
            result="Open",
            pnl=round(unrealized, 2),
            target_price=round(position.target_price, 2),
            stop_price=round(position.base_stop_price, 2),
            trail_stop_price=round(position.trail_stop_price, 2) if position.trail_stop_price is not None else None,
            trail_armed=position.trail_armed if position.trail_enabled else False,
        )

