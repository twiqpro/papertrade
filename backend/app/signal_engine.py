from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import floor
from typing import Optional
from uuid import uuid4

from .models import OptionSide, Signal, SignalStatus, StrategySettings
from .oi_analysis import (
    OiWallMap,
    build_oi_wall_map,
    choose_trend_side,
    classify_regime,
    estimate_gamma_flip,
    gamma_context,
    headroom_ok,
    pcr_ok,
    pin_layer_ok,
    range_compressed,
    regime_display_label,
    reversal_signal,
)
from .strategy import LOT_SIZE, nearest_nifty_strike


INDIA_VIX_SECURITY_ID = 26


@dataclass
class CandleBar:
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class OptionQuote:
    ltp: float
    bid: float
    ask: float
    oi: int
    iv: float
    delta: float


@dataclass
class MarketContext:
    spot: float
    ema_9: float
    ema_15: float
    ema_9_history: list[float]
    candles: list[CandleBar]
    vwap: float
    atr_14: float
    session_high: float
    session_low: float
    atm_strike: int
    atm_ce: OptionQuote
    atm_pe: OptionQuote
    walls: OiWallMap
    gamma_flip: float
    expiry: str
    chain_oc: dict
    india_vix: Optional[float] = None


def calc_ema_series(closes: list[float], period: int) -> list[float]:
    if not closes:
        return []
    multiplier = 2 / (period + 1)
    values = [closes[0]]
    for price in closes[1:]:
        values.append((price * multiplier) + (values[-1] * (1 - multiplier)))
    return values


def calc_vwap(candles: list[CandleBar]) -> float:
    numer = 0.0
    denom = 0.0
    for bar in candles:
        typical = (bar.high + bar.low + bar.close) / 3
        volume = max(bar.volume, 0.0)
        numer += typical * volume
        denom += volume
    if denom <= 0 and candles:
        return candles[-1].close
    return numer / denom if denom > 0 else 0.0


def calc_atr(candles: list[CandleBar], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    ranges: list[float] = []
    previous_close = candles[0].close
    for bar in candles[1:]:
        tr = max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))
        ranges.append(tr)
        previous_close = bar.close
    if not ranges:
        return 0.0
    window = ranges[-period:]
    return sum(window) / len(window)


def spread_aware_slippage(bid: float, ask: float, floor_rupees: float = 0.0) -> float:
    if floor_rupees <= 0:
        return 0.0
    spread = max(ask - bid, 0.0)
    return max(floor_rupees, 0.5 * spread)


def capital_lots(settings: StrategySettings, option_ltp: float) -> int:
    if option_ltp <= 0:
        return 0
    if settings.use_full_capital:
        return max(0, floor(settings.capital_budget / (option_ltp * LOT_SIZE)))
    stop_risk_per_lot = settings.stop_loss_rupees * LOT_SIZE
    if stop_risk_per_lot <= 0:
        return 0
    risk_cap = min(settings.per_trade_risk_cap, settings.daily_risk)
    by_risk = floor(risk_cap / stop_risk_per_lot)
    by_capital = floor(settings.capital_budget / (option_ltp * LOT_SIZE))
    return max(0, min(by_risk, by_capital))


def risk_based_lots(settings: StrategySettings, remaining_daily_budget: float, option_ltp: float) -> int:
    return capital_lots(settings, option_ltp)


def _signal(
    now: datetime,
    name: str,
    side: Optional[OptionSide],
    ema_gap: float,
    status: SignalStatus,
    reason: str,
    strike: int,
    option_ltp: Optional[float],
) -> Signal:
    return Signal(
        id=str(uuid4()),
        timestamp=now,
        time=now.strftime("%H:%M"),
        signal=name,
        side=side,
        ema_gap=ema_gap,
        status=status,
        reason=reason,
        strike=strike,
        option_ltp=option_ltp,
    )


def evaluate_entry_signal(
    now: datetime,
    settings: StrategySettings,
    ctx: MarketContext,
    remaining_daily_budget: Optional[float] = None,
    has_open_position: bool = False,
) -> Signal:
    ema_gap = abs(ctx.ema_9 - ctx.ema_15)
    strike = ctx.atm_strike
    compressed = range_compressed(
        ctx.session_high,
        ctx.session_low,
        ctx.atr_14,
        settings.gamma_range_atr_ratio,
    )
    regime = classify_regime(
        ema_gap,
        ctx.session_high,
        ctx.session_low,
        ctx.atr_14,
        settings.strong_trend_gap,
        settings.gamma_range_atr_ratio,
    )
    gamma = gamma_context(ctx.spot, ctx.gamma_flip)

    side = choose_trend_side(ctx.ema_9, ctx.ema_15, ctx.ema_9_history)
    reversal = False
    if side is None:
        rev_side = reversal_signal(
            ctx.spot,
            ctx.walls,
            regime,
            compressed,
            settings.reversal_enabled,
            settings.pcr_ce_block,
            settings.pcr_pe_block,
        )
        if rev_side is None:
            probe = ctx.atm_ce if ctx.ema_9 > ctx.ema_15 else ctx.atm_pe
            return _signal(
                now,
                "Layer 0",
                "CE" if ctx.ema_9 > ctx.ema_15 else "PE",
                ema_gap,
                "Skipped",
                "No clean trend direction",
                strike,
                probe.ltp,
            )
        side = rev_side
        reversal = True

    quote = ctx.atm_ce if side == "CE" else ctx.atm_pe
    option_ltp = quote.ltp
    signal_prefix = "Reversal" if reversal else "ATM entry"

    if has_open_position:
        return _signal(now, "Position", side, ema_gap, "Skipped", "Open paper position — one at a time", strike, option_ltp)

    if not reversal:
        if ema_gap < settings.ema_gap_min_points:
            return _signal(now, "Layer A", side, ema_gap, "Skipped", "EMA gap below 3-point threshold", strike, option_ltp)

        if len(ctx.ema_9_history) >= 3:
            slope = ctx.ema_9_history[-1] - ctx.ema_9_history[-3]
            if side == "CE" and slope <= 0:
                return _signal(now, "Layer A", side, ema_gap, "Skipped", "EMA9 not rising over last 2 candles", strike, option_ltp)
            if side == "PE" and slope >= 0:
                return _signal(now, "Layer A", side, ema_gap, "Skipped", "EMA9 not falling over last 2 candles", strike, option_ltp)

        if ctx.candles:
            bar = ctx.candles[-1]
            candle_range = max(bar.high - bar.low, 0.01)
            body = abs(bar.close - bar.open)
            body_ratio = body / candle_range
            bullish = bar.close > bar.open
            bearish = bar.close < bar.open
            if body_ratio < settings.min_candle_body_ratio:
                return _signal(now, "Layer A", side, ema_gap, "Skipped", "Last candle body too small (doji/indecision)", strike, option_ltp)
            if side == "CE" and not bullish:
                return _signal(now, "Layer A", side, ema_gap, "Skipped", "Last candle not bullish for CE", strike, option_ltp)
            if side == "PE" and not bearish:
                return _signal(now, "Layer A", side, ema_gap, "Skipped", "Last candle not bearish for PE", strike, option_ltp)

    if ctx.vwap > 0:
        if side == "CE" and ctx.spot < ctx.vwap:
            return _signal(now, "Layer B", side, ema_gap, "Skipped", "Price below VWAP — counter-momentum for CE", strike, option_ltp)
        if side == "PE" and ctx.spot > ctx.vwap:
            return _signal(now, "Layer B", side, ema_gap, "Skipped", "Price above VWAP — counter-momentum for PE", strike, option_ltp)

    ok, reason = headroom_ok(
        side,
        ctx.spot,
        ctx.walls,
        ctx.candles,
        settings.wall_headroom_points,
        settings.wall_break_lookback,
    )
    if not ok:
        return _signal(now, "Layer C", side, ema_gap, "Skipped", reason, strike, option_ltp)

    ok, reason = pcr_ok(
        side,
        ctx.walls.pcr,
        regime,
        gamma,
        settings.pcr_filter_enabled,
        settings.pcr_ce_block,
        settings.pcr_pe_block,
    )
    if not ok:
        return _signal(now, "Layer C", side, ema_gap, "Skipped", reason, strike, option_ltp)

    ok, reason = pin_layer_ok(
        ctx.spot,
        ctx.walls,
        settings.pin_band_points,
        compressed,
        regime,
        gamma,
    )
    if not ok:
        return _signal(now, "Layer D", side, ema_gap, "Skipped", reason, strike, option_ltp)

    spread = max(quote.ask - quote.bid, 0.0)
    if spread > settings.max_bid_ask_spread:
        return _signal(now, "Layer E", side, ema_gap, "Skipped", f"Bid-ask spread Rs {spread:.2f} too wide", strike, option_ltp)

    if quote.ltp <= 0:
        return _signal(now, "Layer E", side, ema_gap, "Skipped", "ATM option has no LTP", strike, option_ltp)

    if ctx.india_vix is not None and ctx.india_vix >= settings.max_india_vix:
        return _signal(now, "Layer E", side, ema_gap, "Skipped", f"India VIX {ctx.india_vix:.1f} above cap", strike, option_ltp)

    lots = capital_lots(settings, option_ltp)
    if lots < 1:
        return _signal(now, "Sizing", side, ema_gap, "Skipped", "Risk budget / capital cannot support 1 lot", strike, option_ltp)

    regime_label = regime_display_label(regime, gamma)
    hr_ok, hr_reason = headroom_ok(
        side,
        ctx.spot,
        ctx.walls,
        ctx.candles,
        settings.wall_headroom_points,
        settings.wall_break_lookback,
    )
    pcr_ok_msg = pcr_ok(
        side,
        ctx.walls.pcr,
        regime,
        gamma,
        settings.pcr_filter_enabled,
        settings.pcr_ce_block,
        settings.pcr_pe_block,
    )[1]

    return _signal(
        now,
        signal_prefix,
        side,
        ema_gap,
        "Taken",
        f"{regime_label} · {hr_reason} · {pcr_ok_msg} · {lots} lot(s)",
        strike,
        option_ltp,
    )


def parse_candles(raw: dict) -> list[CandleBar]:
    opens = raw.get("open") or []
    highs = raw.get("high") or []
    lows = raw.get("low") or []
    closes = raw.get("close") or []
    volumes = raw.get("volume") or []
    length = min(len(opens), len(highs), len(lows), len(closes))
    bars: list[CandleBar] = []
    for index in range(length):
        volume = float(volumes[index]) if index < len(volumes) and volumes[index] is not None else 0.0
        bars.append(
            CandleBar(
                open=float(opens[index]),
                high=float(highs[index]),
                low=float(lows[index]),
                close=float(closes[index]),
                volume=volume,
            )
        )
    return bars


def quote_from_chain_row(row: dict) -> OptionQuote:
    bid = float(row.get("top_bid_price") or 0)
    ask = float(row.get("top_ask_price") or 0)
    greeks = row.get("greeks") or {}
    return OptionQuote(
        ltp=float(row.get("last_price") or 0),
        bid=bid,
        ask=ask,
        oi=int(row.get("oi") or 0),
        iv=float(row.get("implied_volatility") or 0),
        delta=float(greeks.get("delta") or 0),
    )


def build_market_context(
    chain: dict,
    candles_raw: dict,
    expiry: str,
    india_vix: Optional[float] = None,
) -> MarketContext:
    spot = float(chain.get("last_price") or 0)
    candles = parse_candles(candles_raw)
    closes = [bar.close for bar in candles]
    ema_series = calc_ema_series(closes, 9)
    ema_9 = ema_series[-1] if ema_series else spot
    ema_15 = calc_ema_series(closes, 15)[-1] if closes else spot
    from .strategy import nearest_nifty_strike as strike_fn

    atm_strike = strike_fn(spot)
    strikes = chain.get("oc") or {}
    exact_key = f"{atm_strike:.6f}"
    row = strikes.get(exact_key)
    if row is None and strikes:
        closest_key = min(strikes.keys(), key=lambda key: abs(float(key) - spot))
        row = strikes[closest_key]
        atm_strike = int(float(closest_key))
    row = row or {"ce": {}, "pe": {}}
    walls = build_oi_wall_map(chain, spot)
    gamma_flip = estimate_gamma_flip(chain, spot, walls)
    session_high = max((bar.high for bar in candles), default=spot)
    session_low = min((bar.low for bar in candles), default=spot)
    return MarketContext(
        spot=spot,
        ema_9=ema_9,
        ema_15=ema_15,
        ema_9_history=ema_series[-3:],
        candles=candles,
        vwap=calc_vwap(candles),
        atr_14=calc_atr(candles),
        session_high=session_high,
        session_low=session_low,
        atm_strike=atm_strike,
        atm_ce=quote_from_chain_row(row.get("ce") or {}),
        atm_pe=quote_from_chain_row(row.get("pe") or {}),
        walls=walls,
        gamma_flip=gamma_flip,
        expiry=expiry,
        chain_oc=strikes,
        india_vix=india_vix,
    )
