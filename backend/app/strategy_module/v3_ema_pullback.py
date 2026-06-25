from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, time, timedelta
from enum import Enum
from math import floor
from typing import Any, Optional
from uuid import uuid4

from ..models import StrategySettings
from ..paper_broker import timeframe_minutes
from ..signal_engine import CandleBar, MarketContext, OptionQuote, calc_ema_series, spread_aware_slippage
from ..strategy import LOT_SIZE, is_market_close, parse_hhmm
from .base import AccountState, Decision, ExitDecision, Position


STRATEGY_ID = "ema_pullback_cross"
STRATEGY_VERSION = "3.0.0"


@dataclass
class StratConfig:
    lot_size: int = 65
    trade_start: time = time(9, 30)
    trade_end: time = time(11, 30)
    force_exit: time = time(15, 0)
    timeframe: str = "5m"
    chain_staleness_seconds: int = 75
    option_chain_window: int = 10
    capital_budget: float = 100_000.0

    big_bar_atr_mult: float = 1.5
    big_bar_body_ratio: float = 0.60
    pullback_min_frac: float = 0.20
    pullback_max_frac: float = 0.60
    max_pullback_bars: int = 3
    setup_expiry_bars: int = 5
    max_extension_atr: float = 1.5
    stop_buffer_atr: float = 0.10
    breakeven_at_R: float = 1.0

    max_bid_ask_spread: float = 1.5
    max_india_vix: float = 22.0
    entry_slippage_rupees: float = 0.5
    brokerage_per_lot_round_trip: float = 40.0
    risk_fraction_per_trade: float = 0.01
    max_lots: int = 8
    max_trades_per_day: int = 4
    max_consecutive_losses: int = 2
    cooldown_bars_after_stop: int = 2
    stop_rupees: float = 8.0  # option stop proxy for sizing when index R is tiny


def config_from_preset(overrides: dict[str, Any] | None = None) -> StratConfig:
    cfg = StratConfig()
    if not overrides:
        return cfg
    parsed: dict[str, Any] = {}
    for key, value in overrides.items():
        if key in ("trade_start", "trade_end", "force_exit") and isinstance(value, str):
            parsed[key] = parse_hhmm(value)
        else:
            parsed[key] = value
    return replace(cfg, **parsed)


def hash_config(cfg: StratConfig) -> str:
    payload = {"strategy_id": STRATEGY_ID, "strategy_version": STRATEGY_VERSION, "config": _config_dict(cfg)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _config_dict(cfg: StratConfig) -> dict:
    raw = asdict(cfg)
    for key in ("trade_start", "trade_end", "force_exit"):
        raw[key] = raw[key].strftime("%H:%M")
    return raw


def settings_shell(cfg: StratConfig) -> StrategySettings:
    return StrategySettings(
        capital_budget=cfg.capital_budget,
        daily_risk=cfg.capital_budget,
        per_trade_risk_cap=cfg.capital_budget,
        use_full_capital=False,
        target_rupees=cfg.stop_rupees * 2,
        stop_loss_rupees=cfg.stop_rupees,
        max_trades_per_day=cfg.max_trades_per_day,
        max_consecutive_losses=cfg.max_consecutive_losses,
        timeframe=cfg.timeframe,  # type: ignore[arg-type]
        trade_start=cfg.trade_start.strftime("%H:%M"),
        time_stop_candles=99,
        fill_slippage_rupees=cfg.entry_slippage_rupees,
        brokerage_per_lot_round_trip=cfg.brokerage_per_lot_round_trip,
        max_bid_ask_spread=cfg.max_bid_ask_spread,
        max_india_vix=cfg.max_india_vix,
        chain_staleness_seconds=cfg.chain_staleness_seconds,
        option_chain_window=cfg.option_chain_window,
        cooldown_enabled=True,
        reentry_cooldown_candles=cfg.cooldown_bars_after_stop,
    )


def position_lots(cfg: StratConfig, equity: float, index_risk: float) -> int:
    risk_amount = cfg.risk_fraction_per_trade * max(equity, 0.0)
    index_risk_per_lot = max(index_risk, 1.0) * cfg.lot_size * 0.5
    option_risk_per_lot = max(cfg.stop_rupees * cfg.lot_size, index_risk_per_lot)
    if option_risk_per_lot <= 0:
        return 0
    return max(0, min(cfg.max_lots, floor(risk_amount / option_risk_per_lot)))


def on_trade_closed(cfg: StratConfig, account: AccountState, pnl: float, exit_bar: int, result: str) -> None:
    account.has_open_position = False
    account.trades_today += 1
    account.session_equity += pnl
    if pnl < 0:
        account.consecutive_losses += 1
    else:
        account.consecutive_losses = 0
    if result in ("Stop", "Trail"):
        account.cooldown_until_bar = exit_bar + cfg.cooldown_bars_after_stop
    if account.consecutive_losses >= cfg.max_consecutive_losses:
        account.halted = True
    if account.trades_today >= cfg.max_trades_per_day:
        account.halted = True


@dataclass
class IndexBar:
    open: float
    high: float
    low: float
    close: float
    ema9: float
    ema20: float
    atr: float
    index: int


class SetupState(Enum):
    FLAT = 0
    ARMED = 1
    PULLBACK = 2


@dataclass
class PendingIndexEntry:
    side: str
    entry_price: float
    stop: float
    risk: float


class PullbackSetupEngine:
    """EMA 9/20 cross → big bar → pullback → resumption entry (index levels)."""

    def __init__(self, cfg: StratConfig):
        self.cfg = cfg
        self.state = SetupState.FLAT
        self.bias: Optional[str] = None
        self.big_bar: Optional[IndexBar] = None
        self.pullback_extreme: Optional[float] = None
        self.pullback_bars = 0
        self.armed_at = -1
        self.prev: Optional[IndexBar] = None
        self.last_reason = "Waiting for EMA 9/20 cross"

    def reset(self) -> None:
        self.state = SetupState.FLAT
        self.bias = None
        self.big_bar = None
        self.pullback_extreme = None
        self.pullback_bars = 0
        self.armed_at = -1
        self.last_reason = "Waiting for EMA 9/20 cross"

    def _crossed_up(self, b: IndexBar) -> bool:
        return self.prev is not None and self.prev.ema9 <= self.prev.ema20 and b.ema9 > b.ema20

    def _crossed_dn(self, b: IndexBar) -> bool:
        return self.prev is not None and self.prev.ema9 >= self.prev.ema20 and b.ema9 < b.ema20

    def _is_big_bar(self, b: IndexBar, side: str) -> bool:
        rng = b.high - b.low
        if rng <= 0 or b.atr <= 0:
            return False
        body = abs(b.close - b.open)
        if rng < self.cfg.big_bar_atr_mult * b.atr:
            return False
        if body < self.cfg.big_bar_body_ratio * rng:
            return False
        if side == "CE" and b.close <= b.open:
            return False
        if side == "PE" and b.close >= b.open:
            return False
        return True

    def on_bar(self, b: IndexBar) -> Optional[PendingIndexEntry]:
        if self._crossed_up(b):
            self.bias, self.state, self.big_bar, self.armed_at = "CE", SetupState.ARMED, None, b.index
            self.last_reason = "EMA 9/20 crossed up — armed for CE big bar"
        elif self._crossed_dn(b):
            self.bias, self.state, self.big_bar, self.armed_at = "PE", SetupState.ARMED, None, b.index
            self.last_reason = "EMA 9/20 crossed down — armed for PE big bar"

        entry: Optional[PendingIndexEntry] = None

        if self.state == SetupState.ARMED and self.bias:
            if self._is_big_bar(b, self.bias):
                self.big_bar = b
                self.state = SetupState.PULLBACK
                self.pullback_bars = 0
                self.pullback_extreme = b.low if self.bias == "CE" else b.high
                self.last_reason = f"Big bar printed — waiting for {self.bias} pullback"
            elif b.index - self.armed_at > self.cfg.setup_expiry_bars:
                self.reset()

        elif self.state == SetupState.PULLBACK and self.big_bar and self.bias:
            bb = self.big_bar
            bb_range = bb.high - bb.low
            stale = b.index - bb.index > self.cfg.setup_expiry_bars
            broke = (self.bias == "CE" and b.close < bb.ema20) or (self.bias == "PE" and b.close > bb.ema20)
            if stale:
                self.reset()
                self.last_reason = "Setup expired before resumption"
            elif broke:
                self.reset()
                self.last_reason = "Trend structure broke (close through EMA 20)"
            else:
                self.pullback_bars += 1
                if self.bias == "CE":
                    self.pullback_extreme = min(self.pullback_extreme or b.low, b.low)
                else:
                    self.pullback_extreme = max(self.pullback_extreme or b.high, b.high)

                retr = (
                    (abs(bb.high - self.pullback_extreme) if self.bias == "CE" else abs(self.pullback_extreme - bb.low))
                    / max(bb_range, 1e-9)
                )
                if retr > self.cfg.pullback_max_frac or self.pullback_bars > self.cfg.max_pullback_bars:
                    self.reset()
                    self.last_reason = "Pullback too deep or too long"
                else:
                    deep_enough = retr >= self.cfg.pullback_min_frac
                    not_extended = abs(b.close - b.ema9) <= self.cfg.max_extension_atr * b.atr
                    if self.bias == "CE" and b.high > bb.high and deep_enough and not_extended:
                        entry = self._build_entry("CE", bb.high, self.pullback_extreme, b)
                    elif self.bias == "PE" and b.low < bb.low and deep_enough and not_extended:
                        entry = self._build_entry("PE", bb.low, self.pullback_extreme, b)

        self.prev = b
        return entry

    def _build_entry(self, side: str, entry_price: float, pb_extreme: float, b: IndexBar) -> Optional[PendingIndexEntry]:
        buf = self.cfg.stop_buffer_atr * b.atr
        stop = (pb_extreme - buf) if side == "CE" else (pb_extreme + buf)
        risk = abs(entry_price - stop)
        if risk <= 0:
            self.reset()
            return None
        self.reset()
        return PendingIndexEntry(side=side, entry_price=entry_price, stop=stop, risk=risk)


def _bar_from_context(ctx: MarketContext, bar: CandleBar, bar_index: int) -> IndexBar:
    closes = [c.close for c in ctx.candles]
    ema20 = calc_ema_series(closes, 20)
    return IndexBar(
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        ema9=ctx.ema_9,
        ema20=ema20[-1] if ema20 else ctx.ema_15,
        atr=ctx.atr_14,
        index=bar_index,
    )


def _ema20_from_context(ctx: MarketContext) -> float:
    closes = [c.close for c in ctx.candles]
    ema20 = calc_ema_series(closes, 20)
    return ema20[-1] if ema20 else ctx.ema_15


@dataclass
class EmaPullbackStrategy:
    cfg: StratConfig = field(default_factory=StratConfig)
    strategy_id: str = STRATEGY_ID
    strategy_version: str = STRATEGY_VERSION

    def __post_init__(self) -> None:
        self._setup = PullbackSetupEngine(self.cfg)
        self._index_bar: Optional[IndexBar] = None
        self._last_pending_entry: Optional[PendingIndexEntry] = None

    def required_features(self) -> set[str]:
        return {"nifty_candles", "option_prices", "india_vix_optional", "bid_ask_optional"}

    def reset_session(self) -> None:
        self._setup.reset()
        self._setup.prev = None
        self._index_bar = None
        self._last_pending_entry = None

    def feed_index_bar(
        self,
        *,
        open_: float,
        high: float,
        low: float,
        close: float,
        ema9: float,
        ema20: float,
        atr: float,
        bar_index: int,
    ) -> None:
        self._index_bar = IndexBar(open_, high, low, close, ema9, ema20, atr, bar_index)

    def evaluate_entry(
        self,
        timestamp: datetime,
        market_context: MarketContext,
        account_state: AccountState,
        settings: StrategySettings,
    ) -> Decision:
        cfg = self.cfg
        ctx = market_context
        blocked = self._entry_block_reason(timestamp, account_state)
        if blocked:
            return self._skipped(timestamp, ctx, blocked, "Risk gate")

        if not ctx.candles or ctx.atr_14 <= 0:
            return self._skipped(timestamp, ctx, "Insufficient candle/ATR data", "Data")

        bar = ctx.candles[-1]
        index_bar = _bar_from_context(ctx, bar, account_state.bar_index)
        pending = self._setup.on_bar(index_bar)
        if pending is None:
            return self._skipped(timestamp, ctx, self._setup.last_reason, self._setup.state.name)

        side = pending.side
        quote = ctx.atm_ce if side == "CE" else ctx.atm_pe
        spread = max(quote.ask - quote.bid, 0.0)
        if spread > cfg.max_bid_ask_spread:
            return self._skipped(timestamp, ctx, "Bid-ask spread too wide", "Liquidity", side)
        if quote.ltp <= 0:
            return self._skipped(timestamp, ctx, "ATM option has no price", "Liquidity", side)
        if ctx.india_vix is not None and ctx.india_vix > cfg.max_india_vix:
            return self._skipped(timestamp, ctx, "India VIX too high", "Liquidity", side)

        equity = account_state.session_equity if account_state.session_equity > 0 else cfg.capital_budget
        lots = position_lots(cfg, equity, pending.risk)
        if lots < 1:
            return self._skipped(timestamp, ctx, "Risk-based size < 1 lot", "Sizing", side)

        self._last_pending_entry = pending
        ema20 = index_bar.ema20
        return Decision(
            timestamp=timestamp,
            strategy_id=STRATEGY_ID,
            strategy_version=STRATEGY_VERSION,
            status="Taken",
            side=side,  # type: ignore[arg-type]
            expiry=ctx.expiry,
            strike=ctx.atm_strike,
            signal_layer="Resumption",
            reason=(
                f"EMA cross + big bar + pullback resumption · index R {pending.risk:.1f} pts · "
                f"stop {pending.stop:.1f} · {lots} lot(s)"
            ),
            ema_gap=abs(ctx.ema_9 - ema20),
            ema_9=ctx.ema_9,
            ema_15=ema20,
            spot=ctx.spot,
            vwap=ctx.vwap,
            vwap_label=ctx.vwap_label,
            atr_14=ctx.atr_14,
            session_high=ctx.session_high,
            session_low=ctx.session_low,
            market_regime="EMA_PULLBACK",
            call_wall=ctx.walls.call_wall,
            put_wall=ctx.walls.put_wall,
            pin_strike=ctx.walls.pin_strike,
            pcr=ctx.walls.pcr,
            gamma_flip=ctx.gamma_flip,
            gamma_regime="",
            india_vix=ctx.india_vix,
            atm_ce_price=ctx.atm_ce.ltp,
            atm_pe_price=ctx.atm_pe.ltp,
            option_ltp=quote.ltp,
            lots=lots,
            signal_id=str(uuid4()),
        )

    def create_position(
        self,
        decision: Decision,
        execution_quote: OptionQuote,
        settings: StrategySettings,
        context: MarketContext,
    ) -> Position:
        cfg = self.cfg
        entry_slip = spread_aware_slippage(execution_quote.bid, execution_quote.ask, cfg.entry_slippage_rupees)
        entry_price = execution_quote.ltp + entry_slip if execution_quote.ltp > 0 else entry_slip
        lots = decision.lots or position_lots(cfg, cfg.capital_budget, cfg.stop_rupees)
        bar_minutes = timeframe_minutes(cfg.timeframe)
        time_stop_at = decision.timestamp + timedelta(minutes=bar_minutes * 99)

        pending = self._last_pending_entry
        index_entry = pending.entry_price if pending else context.spot
        index_stop = pending.stop if pending else context.spot
        index_risk = pending.risk if pending else cfg.stop_rupees
        self._last_pending_entry = None

        return Position(
            id=str(uuid4()),
            side=decision.side or "CE",
            strike=decision.strike,
            expiry=decision.expiry,
            quantity=lots * LOT_SIZE,
            lots=lots,
            entry_price=entry_price,
            entry_time=decision.timestamp,
            target_price=round(entry_price + 10_000, 2),
            base_stop_price=0.01,
            time_stop_at=time_stop_at,
            trail_enabled=False,
            peak_ltp=entry_price,
            trail_armed=False,
            trail_stop_price=None,
            regime_at_entry="ema_pullback",
            index_entry=index_entry,
            index_stop=index_stop,
            index_risk=index_risk,
            index_be_armed=False,
        )

    def evaluate_exit(
        self,
        timestamp: datetime,
        position: Position,
        option_bar_high: float,
        option_bar_low: float,
        option_bar_close: float,
        option_quote: OptionQuote,
        market_context: MarketContext,
        settings: StrategySettings,
    ) -> Optional[ExitDecision]:
        cfg = self.cfg
        ltp = option_quote.ltp or option_bar_close
        exit_slip = spread_aware_slippage(option_quote.bid, option_quote.ask, settings.exit_slippage_rupees)
        exit_price = max(0.05, ltp - exit_slip)

        if position.index_entry is None or position.index_stop is None or position.index_risk is None:
            return None

        b = self._index_bar
        if b is None:
            b = IndexBar(
                option_bar_close,
                option_bar_high,
                option_bar_low,
                market_context.spot,
                market_context.ema_9,
                _ema20_from_context(market_context),
                market_context.atr_14,
                0,
            )

        if timestamp.time() >= cfg.force_exit or is_market_close(timestamp):
            return ExitDecision("Time Exit", exit_price, timestamp, "Force exit / session cutoff")

        side = position.side
        if (side == "CE" and b.ema9 < b.ema20) or (side == "PE" and b.ema9 > b.ema20):
            return ExitDecision("Time Exit", exit_price, timestamp, "EMA 9/20 trend flip")

        profit = (b.close - position.index_entry) if side == "CE" else (position.index_entry - b.close)
        if not position.index_be_armed and profit >= cfg.breakeven_at_R * position.index_risk:
            position.index_stop = position.index_entry
            position.index_be_armed = True

        if position.index_be_armed:
            if side == "CE" and b.close < b.ema9:
                return ExitDecision("Trail", exit_price, timestamp, "Close back through EMA 9 (trail)")
            if side == "PE" and b.close > b.ema9:
                return ExitDecision("Trail", exit_price, timestamp, "Close back through EMA 9 (trail)")

        if side == "CE" and b.low <= position.index_stop:
            return ExitDecision("Stop", exit_price, timestamp, "Index structural stop")
        if side == "PE" and b.high >= position.index_stop:
            return ExitDecision("Stop", exit_price, timestamp, "Index structural stop")

        return None

    def _entry_block_reason(self, timestamp: datetime, account: AccountState) -> Optional[str]:
        cfg = self.cfg
        if account.has_open_position:
            return "Position already open (one at a time)"
        if account.halted:
            return "Day halted (consecutive-loss limit hit)"
        if account.trades_today >= cfg.max_trades_per_day:
            return f"Daily trade cap reached ({cfg.max_trades_per_day})"
        if account.bar_index < account.cooldown_until_bar:
            return "In cooldown after a stop"
        t = timestamp.time()
        if not (cfg.trade_start <= t <= cfg.trade_end):
            return f"Outside trade window ({cfg.trade_start.strftime('%H:%M')}-{cfg.trade_end.strftime('%H:%M')})"
        return None

    def _skipped(
        self,
        timestamp: datetime,
        ctx: MarketContext,
        reason: str,
        layer: str,
        side: Optional[str] = None,
    ) -> Decision:
        ema20 = _ema20_from_context(ctx)
        return Decision(
            timestamp=timestamp,
            strategy_id=STRATEGY_ID,
            strategy_version=STRATEGY_VERSION,
            status="Skipped",
            side=side,  # type: ignore[arg-type]
            expiry=ctx.expiry,
            strike=ctx.atm_strike,
            signal_layer=layer,
            reason=reason,
            ema_gap=abs(ctx.ema_9 - ema20),
            ema_9=ctx.ema_9,
            ema_15=ema20,
            spot=ctx.spot,
            vwap=ctx.vwap,
            vwap_label=ctx.vwap_label,
            atr_14=ctx.atr_14,
            session_high=ctx.session_high,
            session_low=ctx.session_low,
            market_regime="EMA_PULLBACK",
            call_wall=ctx.walls.call_wall,
            put_wall=ctx.walls.put_wall,
            pin_strike=ctx.walls.pin_strike,
            pcr=ctx.walls.pcr,
            gamma_flip=ctx.gamma_flip,
            gamma_regime="",
            india_vix=ctx.india_vix,
            atm_ce_price=ctx.atm_ce.ltp,
            atm_pe_price=ctx.atm_pe.ltp,
            option_ltp=None,
            lots=0,
            signal_id=str(uuid4()),
        )


def get_strategy_v3(cfg: StratConfig | None = None) -> EmaPullbackStrategy:
    return EmaPullbackStrategy(cfg=cfg or StratConfig())
