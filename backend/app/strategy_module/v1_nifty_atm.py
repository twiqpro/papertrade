from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from ..models import StrategySettings, TradeResult
from ..oi_analysis import classify_regime, gamma_context, regime_display_label
from ..paper_broker import _snap_exit_levels, estimate_brokerage, timeframe_minutes
from ..signal_engine import MarketContext, OptionQuote, Signal, capital_lots, evaluate_entry_signal, spread_aware_slippage
from ..strategy import LOT_SIZE, is_market_close
from .base import AccountState, Decision, ExitDecision, Position, strategy_hash


STRATEGY_ID = "nifty_atm_scalp"
STRATEGY_VERSION = "1.0.0"


def _signal_to_decision(signal: Signal, ctx: MarketContext, settings: StrategySettings) -> Decision:
    ema_gap = abs(ctx.ema_9 - ctx.ema_15)
    regime = classify_regime(
        ema_gap,
        ctx.session_high,
        ctx.session_low,
        ctx.atr_14,
        settings.strong_trend_gap,
        settings.gamma_range_atr_ratio,
    )
    gamma = gamma_context(ctx.spot, ctx.gamma_flip)
    quote = ctx.atm_ce if signal.side == "CE" else ctx.atm_pe if signal.side == "PE" else ctx.atm_ce
    lots = capital_lots(settings, quote.ltp) if signal.status == "Taken" else 0
    flags: list[str] = []
    if ctx.vwap_label == "TWAP":
        flags.append("vwap_fallback_twap")
    if ctx.india_vix is None and settings.vix_filter_enabled:
        flags.append("vix_unavailable")
    return Decision(
        timestamp=signal.timestamp,
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        status=signal.status,
        side=signal.side,
        expiry=ctx.expiry,
        strike=signal.strike,
        signal_layer=signal.signal,
        reason=signal.reason,
        ema_gap=signal.ema_gap,
        ema_9=ctx.ema_9,
        ema_15=ctx.ema_15,
        spot=ctx.spot,
        vwap=ctx.vwap,
        vwap_label=ctx.vwap_label,
        atr_14=ctx.atr_14,
        session_high=ctx.session_high,
        session_low=ctx.session_low,
        market_regime=regime,
        call_wall=ctx.walls.call_wall,
        put_wall=ctx.walls.put_wall,
        pin_strike=ctx.walls.pin_strike,
        pcr=ctx.walls.pcr,
        gamma_flip=ctx.gamma_flip,
        gamma_regime=gamma,
        india_vix=ctx.india_vix,
        atm_ce_price=ctx.atm_ce.ltp,
        atm_pe_price=ctx.atm_pe.ltp,
        option_ltp=signal.option_ltp,
        lots=lots,
        data_timestamps={"nifty": signal.timestamp.isoformat(), "chain": signal.timestamp.isoformat()},
        data_quality_flags=flags,
        signal_id=signal.id,
    )


class NiftyAtmStrategyV1:
    strategy_id = STRATEGY_ID
    strategy_version = STRATEGY_VERSION

    def required_features(self) -> set[str]:
        return {
            "nifty_candles",
            "option_prices",
            "option_oi",
            "india_vix_optional",
            "bid_ask_optional",
        }

    def evaluate_entry(
        self,
        timestamp: datetime,
        market_context: MarketContext,
        account_state: AccountState,
        settings: StrategySettings,
    ) -> Decision:
        signal = evaluate_entry_signal(
            timestamp,
            settings,
            market_context,
            remaining_daily_budget=account_state.remaining_daily_budget,
            has_open_position=account_state.has_open_position,
        )
        return _signal_to_decision(signal, market_context, settings)

    def create_position(
        self,
        decision: Decision,
        execution_quote: OptionQuote,
        settings: StrategySettings,
        context: MarketContext,
    ) -> Position:
        entry_slip = spread_aware_slippage(execution_quote.bid, execution_quote.ask, settings.fill_slippage_rupees)
        entry_price = execution_quote.ltp + entry_slip if execution_quote.ltp > 0 else entry_slip
        target_price, base_stop_price, regime_label, trail_on = _snap_exit_levels(entry_price, settings, context)
        lots = decision.lots or capital_lots(settings, entry_price)
        candles = settings.time_stop_candles
        time_stop_at = decision.timestamp + timedelta(minutes=timeframe_minutes(settings.timeframe) * candles)
        return Position(
            id=str(uuid4()),
            side=decision.side or "CE",
            strike=decision.strike,
            expiry=decision.expiry,
            quantity=lots * LOT_SIZE,
            lots=lots,
            entry_price=entry_price,
            entry_time=decision.timestamp,
            target_price=target_price,
            base_stop_price=base_stop_price,
            time_stop_at=time_stop_at,
            trail_enabled=trail_on,
            peak_ltp=entry_price,
            trail_armed=False,
            trail_stop_price=None,
            regime_at_entry=regime_label,
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
        ltp = option_quote.ltp or option_bar_close
        position.peak_ltp = max(position.peak_ltp, ltp)
        if position.trail_enabled and settings.trail_enabled:
            if position.peak_ltp - position.entry_price >= settings.trail_trigger_rupees:
                position.trail_armed = True
            if position.trail_armed:
                trail = position.peak_ltp - settings.trail_distance_rupees
                position.trail_stop_price = max(position.base_stop_price, trail)

        if is_market_close(timestamp):
            exit_slip = spread_aware_slippage(option_quote.bid, option_quote.ask, settings.exit_slippage_rupees)
            return ExitDecision("Time Exit", max(0.05, ltp - exit_slip), timestamp, "Market close square-off")

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
            return ExitDecision("Time Exit", max(0.05, ltp - exit_slip), timestamp, "Time stop")
        return None


def get_strategy_v1() -> NiftyAtmStrategyV1:
    return NiftyAtmStrategyV1()


def hash_settings(settings: StrategySettings) -> str:
    return strategy_hash(STRATEGY_ID, STRATEGY_VERSION, settings)
