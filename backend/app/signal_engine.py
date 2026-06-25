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
    filter_chain_atm_window,
    gamma_context,
    headroom_ok,
    pcr_ok,
    pin_layer_ok,
    range_compressed,
    regime_display_label,
    reversal_signal,
)
from .strategy import LOT_SIZE, is_trade_window_open, nearest_nifty_strike, parse_hhmm


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
    vwap_label: str = "VWAP"
    ema_20: float = 0.0
    macd: float = 0.0
    macd_signal: float = 0.0


def calc_ema_series(closes: list[float], period: int) -> list[float]:
    if not closes:
        return []
    multiplier = 2 / (period + 1)
    values = [closes[0]]
    for price in closes[1:]:
        values.append((price * multiplier) + (values[-1] * (1 - multiplier)))
    return values


def calc_vwap_or_twap(candles: list[CandleBar]) -> tuple[float, str]:
    numer = 0.0
    denom = 0.0
    for bar in candles:
        typical = (bar.high + bar.low + bar.close) / 3
        volume = max(bar.volume, 0.0)
        numer += typical * volume
        denom += volume
    if denom > 0:
        return numer / denom, "VWAP"
    if not candles:
        return 0.0, "TWAP"
    return sum((bar.high + bar.low + bar.close) / 3 for bar in candles) / len(candles), "TWAP"


def calc_vwap(candles: list[CandleBar]) -> float:
    value, _ = calc_vwap_or_twap(candles)
    return value


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


def _enrich_context_indicators(ctx: MarketContext, settings: StrategySettings) -> MarketContext:
    from .strategy_module.ema_macd_cross import build_indicator_bars

    if not ctx.candles:
        return ctx
    rows = build_indicator_bars(ctx.candles, settings)
    if not rows:
        return ctx
    last = rows[-1]
    return MarketContext(
        spot=ctx.spot,
        ema_9=last.ema_fast,
        ema_15=ctx.ema_15,
        ema_9_history=ctx.ema_9_history,
        candles=ctx.candles,
        vwap=ctx.vwap,
        atr_14=ctx.atr_14,
        session_high=ctx.session_high,
        session_low=ctx.session_low,
        atm_strike=ctx.atm_strike,
        atm_ce=ctx.atm_ce,
        atm_pe=ctx.atm_pe,
        walls=ctx.walls,
        gamma_flip=ctx.gamma_flip,
        expiry=ctx.expiry,
        chain_oc=ctx.chain_oc,
        india_vix=ctx.india_vix,
        vwap_label=ctx.vwap_label,
        ema_20=last.ema_slow,
        macd=last.macd,
        macd_signal=last.macd_sig,
    )


_entry_evaluated_candle_count: int = -1


def reset_entry_bar_tracking() -> None:
    global _entry_evaluated_candle_count
    _entry_evaluated_candle_count = -1


def evaluate_entry_signal(
    now: datetime,
    settings: StrategySettings,
    ctx: MarketContext,
    remaining_daily_budget: Optional[float] = None,
    has_open_position: bool = False,
) -> Signal:
    global _entry_evaluated_candle_count

    from .strategy_module.ema_macd_cross import build_indicator_bars, entry_signal, entry_skip_reason

    ctx = _enrich_context_indicators(ctx, settings)
    strike = ctx.atm_strike
    ema_gap = abs(ctx.ema_9 - ctx.ema_20) if ctx.ema_20 else abs(ctx.ema_9 - ctx.ema_15)

    if has_open_position:
        return _signal(now, "Position", None, ema_gap, "Skipped", "Open paper position — one at a time", strike, None)

    if not is_trade_window_open(now, settings):
        return _signal(
            now,
            "Window",
            None,
            ema_gap,
            "Skipped",
            f"Outside entry window {settings.trade_start}-{settings.trade_end}",
            strike,
            None,
        )

    candle_count = len(ctx.candles)
    min_bars = max(settings.ema_slow, settings.macd_slow) + settings.macd_signal_period + 2
    if candle_count < min_bars:
        return _signal(now, "Data", None, ema_gap, "Skipped", f"Need {min_bars}+ bars for EMA/MACD warmup", strike, None)

    if candle_count <= _entry_evaluated_candle_count:
        return _signal(now, "Bar", None, ema_gap, "Skipped", "Waiting for next 5m bar close", strike, None)

    rows = build_indicator_bars(ctx.candles, settings)
    prev2, prev, cur = rows[-3], rows[-2], rows[-1]
    _entry_evaluated_candle_count = candle_count

    side = entry_signal(prev2, prev, cur, settings)
    if side is None:
        reason = entry_skip_reason(prev2, prev, cur, settings)
        return _signal(now, "EMA/MACD", None, ema_gap, "Skipped", reason, strike, None)

    quote = ctx.atm_ce if side == "CE" else ctx.atm_pe
    option_ltp = quote.ltp

    spread = max(quote.ask - quote.bid, 0.0)
    if settings.spread_filter_enabled and quote.bid > 0 and quote.ask > 0 and spread > settings.max_bid_ask_spread:
        return _signal(now, "Liquidity", side, ema_gap, "Skipped", f"Bid-ask spread Rs {spread:.2f} too wide", strike, option_ltp)

    if option_ltp <= 0:
        return _signal(now, "Liquidity", side, ema_gap, "Skipped", "ATM option has no LTP", strike, option_ltp)

    if settings.vix_filter_enabled and ctx.india_vix is not None and ctx.india_vix >= settings.max_india_vix:
        return _signal(now, "Liquidity", side, ema_gap, "Skipped", f"India VIX {ctx.india_vix:.1f} above cap", strike, option_ltp)

    if settings.use_full_capital:
        lots = capital_lots(settings, option_ltp)
    else:
        lots = settings.lots_per_trade
    if lots < 1:
        return _signal(now, "Sizing", side, ema_gap, "Skipped", "Cannot support 1 lot", strike, option_ltp)

    return _signal(
        now,
        "EMA cross confirm",
        side,
        ema_gap,
        "Taken",
        (
            f"9/20 cross + MACD confirm · SL -{settings.sl_pct:.0%} prem"
            f"{f' · TP +{settings.target_pct:.0%}' if settings.target_pct_enabled else ''}"
            f" · {lots} lot(s)"
        ),
        strike,
        option_ltp,
    )


def build_demo_context(market: DemoMarket, strike: int, expiry: str | None = None) -> MarketContext:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
    bar = CandleBar(market.nifty_spot - 2, market.nifty_spot + 3, market.nifty_spot - 4, market.nifty_spot + 1, 1000)
    walls = OiWallMap(call_wall=strike + 100, put_wall=strike - 100, pin_strike=float(strike), pcr=1.0, total_call_oi=0, total_put_oi=0)
    vwap_value, vwap_label = calc_vwap_or_twap([bar])
    return MarketContext(
        spot=market.nifty_spot,
        ema_9=market.ema_9,
        ema_15=market.ema_15,
        ema_9_history=[market.ema_9 - 1, market.ema_9 - 0.5, market.ema_9],
        candles=[bar],
        vwap=vwap_value,
        vwap_label=vwap_label,
        atr_14=20,
        session_high=market.nifty_spot + 30,
        session_low=market.nifty_spot - 30,
        atm_strike=strike,
        atm_ce=OptionQuote(market.atm_ce_ltp, market.atm_ce_ltp - 0.5, market.atm_ce_ltp + 0.5, 0, 12, 0.5),
        atm_pe=OptionQuote(market.atm_pe_ltp, market.atm_pe_ltp - 0.5, market.atm_pe_ltp + 0.5, 0, 12, -0.5),
        walls=walls,
        gamma_flip=float(strike),
        expiry=expiry or datetime.now(IST).date().isoformat(),
        chain_oc={},
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
    ema_20 = calc_ema_series(closes, 20)[-1] if closes else spot
    from .strategy_module.ema_macd_cross import calc_macd_series

    macd_line, macd_sig, _ = calc_macd_series(closes, 12, 26, 9)
    macd_val = macd_line[-1] if macd_line else 0.0
    macd_signal_val = macd_sig[-1] if macd_sig else 0.0
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
    window = 10
    walls = build_oi_wall_map(strikes, spot, window)
    filtered_chain = {"oc": filter_chain_atm_window(strikes, spot, window)}
    gamma_flip = estimate_gamma_flip(filtered_chain, spot, walls)
    session_high = max((bar.high for bar in candles), default=spot)
    session_low = min((bar.low for bar in candles), default=spot)
    vwap_value, vwap_label = calc_vwap_or_twap(candles)
    return MarketContext(
        spot=spot,
        ema_9=ema_9,
        ema_15=ema_15,
        ema_9_history=ema_series[-3:],
        candles=candles,
        vwap=vwap_value,
        vwap_label=vwap_label,
        atr_14=calc_atr(candles),
        session_high=session_high,
        session_low=session_low,
        atm_strike=atm_strike,
        atm_ce=quote_from_chain_row(row.get("ce") or {}),
        atm_pe=quote_from_chain_row(row.get("pe") or {}),
        walls=walls,
        gamma_flip=gamma_flip,
        expiry=expiry,
        chain_oc=filter_chain_atm_window(strikes, spot, window),
        india_vix=india_vix,
        ema_20=ema_20,
        macd=macd_val,
        macd_signal=macd_signal_val,
    )
