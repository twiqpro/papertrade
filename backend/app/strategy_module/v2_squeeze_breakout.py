from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, time, timedelta
from math import floor
from typing import Any, Optional
from uuid import uuid4

from ..models import StrategySettings
from ..paper_broker import timeframe_minutes
from ..signal_engine import MarketContext, OptionQuote, spread_aware_slippage
from ..strategy import LOT_SIZE, is_market_close, parse_hhmm
from .base import AccountState, Decision, ExitDecision, Position


STRATEGY_ID = "squeeze_breakout"
STRATEGY_VERSION = "2.0.0"


@dataclass
class StratConfig:
    lot_size: int = 65

    trade_start: time = time(9, 30)
    trade_end: time = time(11, 30)
    force_exit: time = time(15, 0)

    squeeze_lookback_bars: int = 6
    squeeze_max_range_atr: float = 0.8

    min_body_ratio: float = 0.5
    breakout_buffer_atr: float = 0.05

    ema_gap_min: float = 2.0
    ema_gap_max: float = 9.0
    max_extension_atr: float = 1.2

    use_iv_gate: bool = True
    atm_iv_max: float = 0.18

    max_bid_ask_spread: float = 1.5
    max_india_vix: float = 22.0
    wall_headroom_points: float = 15.0

    stop_rupees: float = 6.0
    rr_min: float = 1.8
    target_rupees: float = 12.0
    trail_trigger_rupees: float = 8.0
    trail_distance_rupees: float = 4.0
    time_stop_bars: int = 4

    brokerage_per_lot_round_trip: float = 40.0
    entry_slippage_rupees: float = 0.5
    friction_target_mult: float = 3.0

    risk_fraction_per_trade: float = 0.01
    max_lots: int = 8
    max_trades_per_day: int = 4
    max_consecutive_losses: int = 2
    cooldown_bars_after_stop: int = 2

    timeframe: str = "5m"
    chain_staleness_seconds: int = 75
    option_chain_window: int = 10
    capital_budget: float = 100_000.0


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
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _config_dict(cfg: StratConfig) -> dict:
    raw = asdict(cfg)
    for key in ("trade_start", "trade_end", "force_exit"):
        raw[key] = raw[key].strftime("%H:%M")
    return raw


def settings_shell(cfg: StratConfig) -> StrategySettings:
    """Minimal StrategySettings for replay costs / DB metadata."""
    return StrategySettings(
        capital_budget=cfg.capital_budget,
        daily_risk=cfg.capital_budget,
        per_trade_risk_cap=cfg.capital_budget,
        use_full_capital=False,
        target_rupees=cfg.target_rupees,
        stop_loss_rupees=cfg.stop_rupees,
        max_trades_per_day=cfg.max_trades_per_day,
        max_consecutive_losses=cfg.max_consecutive_losses,
        timeframe=cfg.timeframe,  # type: ignore[arg-type]
        trade_start=cfg.trade_start.strftime("%H:%M"),
        time_stop_candles=cfg.time_stop_bars,
        fill_slippage_rupees=cfg.entry_slippage_rupees,
        brokerage_per_lot_round_trip=cfg.brokerage_per_lot_round_trip,
        max_bid_ask_spread=cfg.max_bid_ask_spread,
        wall_headroom_points=cfg.wall_headroom_points,
        max_india_vix=cfg.max_india_vix,
        trail_enabled=True,
        trail_trigger_rupees=cfg.trail_trigger_rupees,
        trail_distance_rupees=cfg.trail_distance_rupees,
        chain_staleness_seconds=cfg.chain_staleness_seconds,
        option_chain_window=cfg.option_chain_window,
        cooldown_enabled=True,
        reentry_cooldown_candles=cfg.cooldown_bars_after_stop,
    )


def _per_unit_friction(cfg: StratConfig, spread: float) -> float:
    return cfg.brokerage_per_lot_round_trip / cfg.lot_size + cfg.entry_slippage_rupees + 0.5 * spread


def validated_stop_target(cfg: StratConfig, spread: float) -> tuple[float, float]:
    stop = cfg.stop_rupees
    friction_floor = cfg.friction_target_mult * _per_unit_friction(cfg, spread)
    target = max(cfg.target_rupees, cfg.rr_min * stop, friction_floor)
    return stop, target


def position_lots(cfg: StratConfig, equity: float) -> int:
    risk_amount = cfg.risk_fraction_per_trade * max(equity, 0.0)
    risk_per_lot = cfg.stop_rupees * cfg.lot_size
    if risk_per_lot <= 0:
        return 0
    return max(0, min(cfg.max_lots, floor(risk_amount / risk_per_lot)))


def _recent_range(candles: list, lookback: int) -> tuple[float, float]:
    window = candles[-lookback:] if len(candles) >= lookback else candles
    if not window:
        return 0.0, 0.0
    return max(b.high for b in window), min(b.low for b in window)


def _ema_slope(ctx: MarketContext) -> float:
    if len(ctx.ema_9_history) >= 2:
        return ctx.ema_9_history[-1] - ctx.ema_9_history[-2]
    return 0.0


def _entry_block_reason(
    now: datetime,
    cfg: StratConfig,
    account: AccountState,
) -> Optional[str]:
    if account.has_open_position:
        return "Position already open (one at a time)"
    if account.halted:
        return "Day halted (consecutive-loss limit hit)"
    if account.trades_today >= cfg.max_trades_per_day:
        return f"Daily trade cap reached ({cfg.max_trades_per_day})"
    if account.bar_index < account.cooldown_until_bar:
        return "In cooldown after a stop"
    t = now.time()
    if not (cfg.trade_start <= t <= cfg.trade_end):
        return f"Outside trade window ({cfg.trade_start.strftime('%H:%M')}-{cfg.trade_end.strftime('%H:%M')})"
    return None


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
class SqueezeBreakoutStrategy:
    cfg: StratConfig = field(default_factory=StratConfig)
    strategy_id: str = STRATEGY_ID
    strategy_version: str = STRATEGY_VERSION

    def required_features(self) -> set[str]:
        return {"nifty_candles", "option_prices", "option_oi", "india_vix_optional", "bid_ask_optional"}

    def evaluate_entry(
        self,
        timestamp: datetime,
        market_context: MarketContext,
        account_state: AccountState,
        settings: StrategySettings,
    ) -> Decision:
        cfg = self.cfg
        ctx = market_context
        st = account_state
        blocked = _entry_block_reason(timestamp, cfg, st)
        if blocked:
            return self._skipped(timestamp, ctx, blocked, "Risk gate")

        if ctx.atr_14 <= 0:
            return self._skipped(timestamp, ctx, "ATR unavailable", "Data")

        candles = ctx.candles
        if not candles:
            return self._skipped(timestamp, ctx, "No completed candles", "Data")

        bar = candles[-1]
        body = abs(bar.close - bar.open)
        rng = max(bar.high - bar.low, 1e-9)
        body_ratio = body / rng
        ema_gap = abs(ctx.ema_9 - ctx.ema_15)
        ema_slope = _ema_slope(ctx)
        recent_high, recent_low = _recent_range(candles[:-1], cfg.squeeze_lookback_bars)
        if recent_high <= recent_low:
            recent_high, recent_low = _recent_range(candles, cfg.squeeze_lookback_bars)

        recent_range = recent_high - recent_low
        if recent_range > cfg.squeeze_max_range_atr * ctx.atr_14:
            return self._skipped(
                timestamp,
                ctx,
                "No compression — not buying an extended/already-moving market",
                "A: squeeze",
            )

        buf = cfg.breakout_buffer_atr * ctx.atr_14
        breakout_up = bar.close > recent_high + buf
        breakout_dn = bar.close < recent_low - buf
        if breakout_up:
            side = "CE"
        elif breakout_dn:
            side = "PE"
        else:
            return self._skipped(timestamp, ctx, "No breakout from the coil yet", "B: breakout")

        if body_ratio < cfg.min_body_ratio:
            return self._skipped(timestamp, ctx, "Breakout candle body too small (weak initiation)", "B: breakout", side)

        bullish = bar.close > bar.open
        if side == "CE" and not bullish:
            return self._skipped(timestamp, ctx, "Breakout candle not bullish", "B", side)
        if side == "PE" and bullish:
            return self._skipped(timestamp, ctx, "Breakout candle not bearish", "B", side)

        if side == "CE" and ctx.ema_9 < ctx.ema_15 and ema_gap > cfg.ema_gap_min:
            return self._skipped(timestamp, ctx, "EMA stack opposes CE", "C", side)
        if side == "PE" and ctx.ema_9 > ctx.ema_15 and ema_gap > cfg.ema_gap_min:
            return self._skipped(timestamp, ctx, "EMA stack opposes PE", "C", side)
        if ema_gap > cfg.ema_gap_max:
            return self._skipped(
                timestamp,
                ctx,
                f"EMA gap {ema_gap:.1f} too wide — move already extended (anti-chase)",
                "C: anti-exhaustion",
                side,
            )

        if side == "CE" and ema_slope < 0:
            return self._skipped(timestamp, ctx, "EMA9 sloping down against CE", "C", side)
        if side == "PE" and ema_slope > 0:
            return self._skipped(timestamp, ctx, "EMA9 sloping up against PE", "C", side)

        extension = abs(bar.close - ctx.vwap)
        if extension > cfg.max_extension_atr * ctx.atr_14:
            return self._skipped(
                timestamp,
                ctx,
                f"Price {extension / ctx.atr_14:.1f} ATR from VWAP — too stretched",
                "D: extension",
                side,
            )

        if side == "CE" and (ctx.walls.call_wall - ctx.spot) < cfg.wall_headroom_points:
            return self._skipped(timestamp, ctx, "Insufficient headroom to call wall", "E", side)
        if side == "PE" and (ctx.spot - ctx.walls.put_wall) < cfg.wall_headroom_points:
            return self._skipped(timestamp, ctx, "Insufficient headroom to put wall", "E", side)

        quote = ctx.atm_ce if side == "CE" else ctx.atm_pe
        spread = max(quote.ask - quote.bid, 0.0)
        iv = quote.iv if quote.iv else None
        if cfg.use_iv_gate and iv is not None and iv > cfg.atm_iv_max:
            return self._skipped(
                timestamp,
                ctx,
                f"ATM IV {iv:.1%} rich — premium too pumped to buy",
                "F: vol gate",
                side,
            )

        if spread > cfg.max_bid_ask_spread:
            return self._skipped(timestamp, ctx, "Bid-ask spread too wide", "G", side)
        entry_premium = quote.ltp
        if entry_premium <= 0:
            return self._skipped(timestamp, ctx, "ATM option has no price", "G", side)
        if ctx.india_vix is not None and ctx.india_vix > cfg.max_india_vix:
            return self._skipped(timestamp, ctx, "India VIX too high", "G", side)

        equity = st.session_equity if st.session_equity > 0 else cfg.capital_budget
        lots = position_lots(cfg, equity)
        if lots < 1:
            return self._skipped(timestamp, ctx, "Risk-based size < 1 lot", "Sizing", side)

        stop_delta, target_delta = validated_stop_target(cfg, spread)
        flags: list[str] = []
        if ctx.vwap_label == "TWAP":
            flags.append("vwap_fallback_twap")

        return Decision(
            timestamp=timestamp,
            strategy_id=STRATEGY_ID,
            strategy_version=STRATEGY_VERSION,
            status="Taken",
            side=side,
            expiry=ctx.expiry,
            strike=ctx.atm_strike,
            signal_layer="All clear",
            reason=(
                f"Breakout-from-compression · stop -{stop_delta:.1f} target +{target_delta:.1f} · {lots} lot(s)"
            ),
            ema_gap=ema_gap,
            ema_9=ctx.ema_9,
            ema_15=ctx.ema_15,
            spot=ctx.spot,
            vwap=ctx.vwap,
            vwap_label=ctx.vwap_label,
            atr_14=ctx.atr_14,
            session_high=ctx.session_high,
            session_low=ctx.session_low,
            market_regime="SQUEEZE_BREAKOUT",
            call_wall=ctx.walls.call_wall,
            put_wall=ctx.walls.put_wall,
            pin_strike=ctx.walls.pin_strike,
            pcr=ctx.walls.pcr,
            gamma_flip=ctx.gamma_flip,
            gamma_regime="",
            india_vix=ctx.india_vix,
            atm_ce_price=ctx.atm_ce.ltp,
            atm_pe_price=ctx.atm_pe.ltp,
            option_ltp=entry_premium,
            lots=lots,
            data_quality_flags=flags,
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
        spread = max(execution_quote.ask - execution_quote.bid, 0.0)
        entry_slip = spread_aware_slippage(execution_quote.bid, execution_quote.ask, cfg.entry_slippage_rupees)
        entry_price = execution_quote.ltp + entry_slip if execution_quote.ltp > 0 else entry_slip
        stop_delta, target_delta = validated_stop_target(cfg, spread)
        lots = decision.lots or position_lots(cfg, cfg.capital_budget)
        bar_minutes = timeframe_minutes(cfg.timeframe)
        time_stop_at = decision.timestamp + timedelta(minutes=bar_minutes * cfg.time_stop_bars)
        return Position(
            id=str(uuid4()),
            side=decision.side or "CE",
            strike=decision.strike,
            expiry=decision.expiry,
            quantity=lots * LOT_SIZE,
            lots=lots,
            entry_price=entry_price,
            entry_time=decision.timestamp,
            target_price=round(entry_price + target_delta, 2),
            base_stop_price=round(entry_price - stop_delta, 2),
            time_stop_at=time_stop_at,
            trail_enabled=True,
            peak_ltp=entry_price,
            trail_armed=False,
            trail_stop_price=None,
            regime_at_entry="squeeze_breakout",
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
        position.peak_ltp = max(position.peak_ltp, ltp)

        if position.peak_ltp - position.entry_price >= cfg.trail_trigger_rupees:
            position.trail_armed = True
        if position.trail_armed:
            trail = position.peak_ltp - cfg.trail_distance_rupees
            position.trail_stop_price = max(position.base_stop_price, trail)

        if timestamp.time() >= cfg.force_exit or is_market_close(timestamp):
            exit_slip = spread_aware_slippage(option_quote.bid, option_quote.ask, settings.exit_slippage_rupees)
            return ExitDecision("Time Exit", max(0.05, ltp - exit_slip), timestamp, "Force exit / market close")

        stop_hit = option_bar_low <= position.base_stop_price
        target_hit = option_bar_high >= position.target_price
        if stop_hit and target_hit:
            return ExitDecision("Stop", position.base_stop_price, timestamp, "Stop before target (same bar)")
        if target_hit:
            return ExitDecision("Target", position.target_price, timestamp, "Target touched")
        if stop_hit:
            return ExitDecision("Stop", position.base_stop_price, timestamp, "Stop touched")
        if (
            position.trail_armed
            and position.trail_stop_price is not None
            and option_bar_low <= position.trail_stop_price
        ):
            return ExitDecision("Trail", position.trail_stop_price, timestamp, "Trail stop touched")
        if timestamp >= position.time_stop_at:
            exit_slip = spread_aware_slippage(option_quote.bid, option_quote.ask, settings.exit_slippage_rupees)
            return ExitDecision("Time Exit", max(0.05, ltp - exit_slip), timestamp, "Time stop (bars)")
        return None

    def _skipped(
        self,
        timestamp: datetime,
        ctx: MarketContext,
        reason: str,
        layer: str,
        side: Optional[str] = None,
    ) -> Decision:
        ema_gap = abs(ctx.ema_9 - ctx.ema_15)
        return Decision(
            timestamp=timestamp,
            strategy_id=STRATEGY_ID,
            strategy_version=STRATEGY_VERSION,
            status="Skipped",
            side=side,
            expiry=ctx.expiry,
            strike=ctx.atm_strike,
            signal_layer=layer,
            reason=reason,
            ema_gap=ema_gap,
            ema_9=ctx.ema_9,
            ema_15=ctx.ema_15,
            spot=ctx.spot,
            vwap=ctx.vwap,
            vwap_label=ctx.vwap_label,
            atr_14=ctx.atr_14,
            session_high=ctx.session_high,
            session_low=ctx.session_low,
            market_regime="SQUEEZE_BREAKOUT",
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


def get_strategy_v2(cfg: StratConfig | None = None) -> SqueezeBreakoutStrategy:
    return SqueezeBreakoutStrategy(cfg=cfg or StratConfig())
