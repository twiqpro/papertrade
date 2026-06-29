from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from .models import OptionSide, StrategySettings, Trade, TradeResult
from .signal_engine import LOT_SIZE, MarketContext, Signal, capital_lots, spread_aware_slippage
from .strategy import is_market_close, is_square_off_time, parse_hhmm
from .strategy_module.ema_macd_cross import build_indicator_bars, signal_exit_hit


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


def _premium_exit_levels(entry_price: float, settings: StrategySettings) -> tuple[float, float]:
    target_price = entry_price + settings.target_rupees
    stop_price = max(0.05, entry_price - settings.stop_loss_rupees)
    return target_price, stop_price


def _snap_exit_levels(
    entry_price: float,
    settings: StrategySettings,
    context: MarketContext,
) -> tuple[float, float, str, bool]:
    """Legacy helper for v1 backtest replay."""
    target_price, stop_price = _premium_exit_levels(entry_price, settings)
    return target_price, stop_price, "ema_macd_cross", True


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
            from .signal_engine import reset_entry_bar_tracking

            reset_entry_bar_tracking()
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
        if settings.cooldown_enabled and day.cooldown_until and now < day.cooldown_until:
            return False, "Re-entry cooldown after exit"
        if day.trades_today >= settings.max_trades_per_day:
            return False, f"Max trades per day ({settings.max_trades_per_day}) reached"
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

    def _cooldown_until(self, now: datetime, settings: StrategySettings) -> datetime:
        minutes = timeframe_minutes(settings.timeframe) * settings.reentry_cooldown_candles
        return now + timedelta(minutes=minutes)

    def _time_stop_at(self, entry_time: datetime, settings: StrategySettings, expiry: str) -> datetime:
        square_off = parse_hhmm(settings.square_off_time)
        return entry_time.replace(hour=square_off.hour, minute=square_off.minute, second=0, microsecond=0)

    def _update_premium_trail(self, position: OpenPosition, ltp: float, settings: StrategySettings) -> None:
        position.peak_ltp = max(position.peak_ltp, ltp)
        entry = position.entry_price
        if not position.trail_armed and ltp >= entry * (1.0 + settings.trail_trigger_pct):
            position.trail_armed = True
        if position.trail_armed:
            trail = position.peak_ltp * (1.0 - settings.trail_gap_pct)
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

        quote = quote_for_strike(context, signal.strike, signal.side)
        entry_ltp = signal.option_ltp if signal.option_ltp is not None else quote.ltp
        if settings.use_full_capital:
            effective_capital = settings.capital_budget
            if self.day is not None:
                effective_capital = max(0.0, settings.capital_budget + self.day.realized_pnl)
            sizing_settings = settings.model_copy(update={"capital_budget": effective_capital})
            lots = capital_lots(sizing_settings, entry_ltp)
        else:
            lots = settings.lots_per_trade
        if lots < 1:
            return None

        # Enter at the current price (no slippage adjustment)
        entry_price = entry_ltp
        target_price, base_stop_price = _premium_exit_levels(entry_price, settings)
        quantity = lots * LOT_SIZE
        contract = f"NIFTY {signal.strike} {signal.side}"

        position = OpenPosition(
            id=str(uuid4()),
            contract=contract,
            side=signal.side,
            strike=signal.strike,
            quantity=quantity,
            lots=lots,
            entry_price=entry_price,
            target_price=target_price,
            stop_price=base_stop_price,
            entry_time=now,
            expiry=context.expiry,
            time_stop_at=self._time_stop_at(now, settings, context.expiry),
            regime_at_entry="ema_atm_limit_forward",
            base_stop_price=base_stop_price,
            peak_ltp=entry_price,
            trail_armed=False,
            trail_stop_price=None,
            trail_enabled=False,
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

        if self.day is not None and settings.cooldown_enabled:
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

        entry = position.entry_price

        if is_square_off_time(now, settings) or is_market_close(now):
            return self._close_position(now, settings, context, "Time Exit", ltp)

        if ltp <= position.base_stop_price:
            return self._close_position(now, settings, context, "Stop", position.base_stop_price)

        if ltp >= position.target_price:
            return self._close_position(now, settings, context, "Target", position.target_price)

        if (
            position.trail_armed
            and position.trail_stop_price is not None
            and ltp <= position.trail_stop_price
        ):
            return self._close_position(now, settings, context, "Trail", ltp)

        if settings.use_signal_exit and context.candles:
            rows = build_indicator_bars(context.candles, settings)
            if rows and signal_exit_hit(rows[-1], position.side):
                return self._close_position(now, settings, context, "Time Exit", ltp)

        return None

    def open_position_label(self) -> Optional[str]:
        if self.open_position is None:
            return None
        pos = self.open_position
        trail_part = ""
        if pos.trail_armed and pos.trail_stop_price is not None:
            trail_part = f" · trail {pos.trail_stop_price:.2f} (armed)"
        elif pos.trail_enabled:
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
            self._update_premium_trail(position, ltp, settings)

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
