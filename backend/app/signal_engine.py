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


# Hard ceiling for paper/live: max loss if stop-loss hits (rupees, not points).
PER_TRADE_MAX_LOSS_RS = 20_000


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
    history_seeded: bool = False


@dataclass
class PendingLimitOrder:
    side: OptionSide
    strike: int
    limit: float
    armed_candle_count: int
    age: int = 0


EMA_ATM_STRIKE_OFFSET = 3
EMA_ATM_ORDER_TTL_BARS = 9
_pending_limit_order: PendingLimitOrder | None = None
_pending_last_candle_count: int = -1


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
    by_capital = floor(settings.capital_budget / (option_ltp * LOT_SIZE))
    stop_risk_per_lot = settings.stop_loss_rupees * LOT_SIZE
    if stop_risk_per_lot <= 0:
        return max(0, by_capital)

    # Never size so a full SL costs more than PER_TRADE_MAX_LOSS_RS (e.g. 20k).
    by_loss_cap = floor(PER_TRADE_MAX_LOSS_RS / stop_risk_per_lot)

    if settings.use_full_capital:
        return max(0, min(by_capital, by_loss_cap))

    risk_cap = min(settings.per_trade_risk_cap, PER_TRADE_MAX_LOSS_RS)
    by_risk = floor(risk_cap / stop_risk_per_lot)
    return max(0, min(by_risk, by_capital, by_loss_cap))


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
    global _entry_evaluated_candle_count, _pending_limit_order, _pending_last_candle_count
    _entry_evaluated_candle_count = -1
    _pending_limit_order = None
    _pending_last_candle_count = -1


def _limit_price(atm_ltp: float, offset: int = EMA_ATM_STRIKE_OFFSET) -> int:
    raw = int(round(atm_ltp)) - offset
    while raw > 0 and raw % 5 == 0:
        raw -= 2
    return raw


def _quote_for_strike(ctx: MarketContext, strike: int, side: OptionSide) -> OptionQuote:
    key = f"{strike:.6f}"
    row = (ctx.chain_oc or {}).get(key)
    if not row:
        return ctx.atm_ce if side == "CE" else ctx.atm_pe
    payload = row.get("ce") if side == "CE" else row.get("pe")
    if not payload:
        return OptionQuote(0, 0, 0, 0, 0, 0)
    return quote_from_chain_row(payload)


def select_delta1_strike(ctx: MarketContext, side: OptionSide) -> tuple[int, OptionQuote] | None:
    """Pick the strike nearest delta ±1.0 for the given side, with deepest-ITM fallback."""
    target_delta = 1.0 if side == "CE" else -1.0
    best_strike: int | None = None
    best_quote: OptionQuote | None = None
    best_dist = float("inf")
    best_abs_delta = -1.0

    chain = ctx.chain_oc or {}
    for key, row in chain.items():
        strike = int(float(key))
        payload = row.get("ce") if side == "CE" else row.get("pe")
        if not payload:
            continue
        quote = quote_from_chain_row(payload)
        delta = quote.delta
        if delta == 0:
            continue
        if side == "CE" and delta <= 0:
            continue
        if side == "PE" and delta >= 0:
            continue
        dist = abs(delta - target_delta)
        abs_delta = abs(delta)
        if dist < best_dist or (dist == best_dist and abs_delta > best_abs_delta):
            best_dist = dist
            best_abs_delta = abs_delta
            best_strike = strike
            best_quote = quote

    if best_strike is not None and best_quote is not None:
        return best_strike, best_quote

    spot = ctx.spot
    if side == "CE":
        itm_strikes = [int(float(key)) for key in chain if int(float(key)) < spot]
        if not itm_strikes:
            return None
        fallback_strike = min(itm_strikes)
    else:
        itm_strikes = [int(float(key)) for key in chain if int(float(key)) > spot]
        if not itm_strikes:
            return None
        fallback_strike = max(itm_strikes)

    quote = _quote_for_strike(ctx, fallback_strike, side)
    if quote.ltp <= 0:
        return None
    return fallback_strike, quote


def build_demo_chain_oc(
    spot: float,
    atm_strike: int,
    atm_ce_ltp: float,
    atm_pe_ltp: float,
    window: int = 10,
) -> dict:
    """Synthetic option chain with plausible deltas for offline forward testing."""
    chain: dict = {}
    step = 50
    span = window * step
    for offset in range(-window, window + 1):
        strike = atm_strike + offset * step
        key = f"{strike:.6f}"

        if strike < spot:
            ce_delta = min(0.99, 0.5 + (spot - strike) / span * 0.49)
        else:
            ce_delta = max(0.01, 0.5 - (strike - spot) / span * 0.49)
        if strike > spot:
            pe_delta = max(-0.99, -0.5 - (strike - spot) / span * 0.49)
        else:
            pe_delta = min(-0.01, -0.5 + (spot - strike) / span * 0.49)

        ce_ltp = max(20.0, atm_ce_ltp + (atm_strike - strike) * 0.8)
        pe_ltp = max(20.0, atm_pe_ltp + (strike - atm_strike) * 0.8)

        chain[key] = {
            "ce": {
                "last_price": ce_ltp,
                "top_bid_price": ce_ltp - 0.5,
                "top_ask_price": ce_ltp + 0.5,
                "oi": 10000,
                "implied_volatility": 12.0,
                "greeks": {"delta": ce_delta},
            },
            "pe": {
                "last_price": pe_ltp,
                "top_bid_price": pe_ltp - 0.5,
                "top_ask_price": pe_ltp + 0.5,
                "oi": 10000,
                "implied_volatility": 12.0,
                "greeks": {"delta": pe_delta},
            },
        }
    return chain


def evaluate_delta1_entry_signal(
    now: datetime,
    settings: StrategySettings,
    ctx: MarketContext,
    remaining_daily_budget: Optional[float] = None,
    has_open_position: bool = False,
    entry_block_reason: Optional[str] = None,
) -> Signal:
    """Forward-test entry: immediate delta-1 contract fill when EMA gap is valid."""
    strike = ctx.atm_strike
    ema_gap = abs(ctx.ema_9 - ctx.ema_20) if ctx.ema_20 else abs(ctx.ema_9 - ctx.ema_15)
    gap = (ctx.ema_9 - ctx.ema_20) if ctx.ema_20 else (ctx.ema_9 - ctx.ema_15)

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

    if entry_block_reason:
        return _signal(
            now,
            "Entry blocked",
            None,
            ema_gap,
            "Skipped",
            f"Entry blocked: {entry_block_reason}",
            strike,
            None,
        )

    candle_count = len(ctx.candles)
    min_bars = settings.warmup_bars if ctx.history_seeded else 2
    if candle_count < min_bars:
        return _signal(
            now,
            "Data",
            None,
            ema_gap,
            "Skipped",
            f"Need {min_bars}+ bars for EMA warmup (have {candle_count})",
            strike,
            None,
        )

    if abs(gap) < settings.ema_gap_min_points:
        return _signal(now, "EMA gap", None, ema_gap, "Skipped", f"EMA gap {gap:+.1f} < {settings.ema_gap_min_points}", strike, None)

    side: OptionSide = "CE" if gap > 0 else "PE"
    selected = select_delta1_strike(ctx, side)
    if selected is None:
        return _signal(
            now,
            "Contract",
            side,
            ema_gap,
            "Skipped",
            f"No delta-1 {side} contract available in chain window",
            strike,
            None,
        )

    strike, quote = selected
    option_ltp = quote.ltp

    spread = max(quote.ask - quote.bid, 0.0)
    if settings.spread_filter_enabled and quote.bid > 0 and quote.ask > 0 and spread > settings.max_bid_ask_spread:
        return _signal(now, "Liquidity", side, ema_gap, "Skipped", f"Bid-ask spread Rs {spread:.2f} too wide", strike, option_ltp)

    if option_ltp <= 0:
        return _signal(now, "Liquidity", side, ema_gap, "Skipped", f"Delta-1 {side} has no LTP at {strike}", strike, option_ltp)

    if settings.vix_filter_enabled and ctx.india_vix is not None and ctx.india_vix >= settings.max_india_vix:
        return _signal(now, "Liquidity", side, ema_gap, "Skipped", f"India VIX {ctx.india_vix:.1f} above cap", strike, option_ltp)

    effective_capital = settings.capital_budget
    if remaining_daily_budget is not None:
        effective_capital = max(0.0, settings.capital_budget + remaining_daily_budget - settings.daily_risk)
    sizing_settings = settings.model_copy(update={"capital_budget": effective_capital})
    lots = capital_lots(sizing_settings, option_ltp) if settings.use_full_capital else settings.lots_per_trade
    if lots < 1:
        return _signal(now, "Sizing", side, ema_gap, "Skipped", "Cannot support 1 lot", strike, option_ltp)

    delta_label = f"delta {quote.delta:+.2f}" if quote.delta else "ITM proxy"
    return _signal(
        now,
        "EMA delta-1 entry",
        side,
        ema_gap,
        "Taken",
        (
            f"Immediate {side} {strike} entry at {option_ltp:.1f} ({delta_label}); "
            f"EMA gap {gap:+.1f}, approx {lots} lot(s)"
        ),
        strike,
        option_ltp,
    )


def evaluate_entry_signal(
    now: datetime,
    settings: StrategySettings,
    ctx: MarketContext,
    remaining_daily_budget: Optional[float] = None,
    has_open_position: bool = False,
    entry_block_reason: Optional[str] = None,
) -> Signal:
    global _pending_limit_order, _pending_last_candle_count

    strike = ctx.atm_strike
    ema_gap = abs(ctx.ema_9 - ctx.ema_20) if ctx.ema_20 else abs(ctx.ema_9 - ctx.ema_15)
    gap = (ctx.ema_9 - ctx.ema_20) if ctx.ema_20 else (ctx.ema_9 - ctx.ema_15)

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

    if entry_block_reason:
        if _pending_limit_order is not None:
            pending = _pending_limit_order
            _pending_limit_order = None
            return _signal(
                now,
                "Limit cancelled",
                pending.side,
                ema_gap,
                "Skipped",
                f"Entry blocked: {entry_block_reason}; cancelled pending limit",
                pending.strike,
                pending.limit,
            )
        return _signal(
            now,
            "Entry blocked",
            None,
            ema_gap,
            "Skipped",
            f"Entry blocked: {entry_block_reason}",
            strike,
            None,
        )

    candle_count = len(ctx.candles)
    min_bars = settings.warmup_bars if ctx.history_seeded else 2
    if candle_count < min_bars:
        return _signal(
            now,
            "Data",
            None,
            ema_gap,
            "Skipped",
            f"Need {min_bars}+ bars for EMA warmup (have {candle_count})",
            strike,
            None,
        )

    if _pending_limit_order is not None:
        pending = _pending_limit_order
        if candle_count > _pending_last_candle_count:
            pending.age += 1
            _pending_last_candle_count = candle_count

        if not is_trade_window_open(now, settings):
            _pending_limit_order = None
            return _signal(now, "Limit cancelled", pending.side, ema_gap, "Skipped", "Cancelled after entry cutoff", pending.strike, pending.limit)

        still_valid = abs(gap) >= settings.ema_gap_min_points and ((gap > 0) == (pending.side == "CE"))
        if not still_valid:
            _pending_limit_order = None
            return _signal(now, "Limit cancelled", pending.side, ema_gap, "Skipped", f"EMA gap {gap:+.1f} no longer supports {pending.side}", pending.strike, pending.limit)

        if pending.age > EMA_ATM_ORDER_TTL_BARS:
            _pending_limit_order = None
            return _signal(now, "Limit cancelled", pending.side, ema_gap, "Skipped", f"TTL {EMA_ATM_ORDER_TTL_BARS} bars expired", pending.strike, pending.limit)

        quote = _quote_for_strike(ctx, pending.strike, pending.side)
        option_ltp = quote.ltp
        if option_ltp <= 0:
            return _signal(now, "Limit resting", pending.side, ema_gap, "Skipped", f"No {pending.side} quote at {pending.strike}", pending.strike, pending.limit)

        if option_ltp <= pending.limit:
            _pending_limit_order = None
            return _signal(
                now,
                "EMA ATM limit fill",
                pending.side,
                ema_gap,
                "Taken",
                f"Resting limit {pending.limit:.1f} hit on {pending.side} {pending.strike} (LTP {option_ltp:.1f})",
                pending.strike,
                pending.limit,
            )

        return _signal(
            now,
            "Limit resting",
            pending.side,
            ema_gap,
            "Skipped",
            f"Limit {pending.limit:.1f} resting on {pending.side} {pending.strike} (LTP {option_ltp:.1f})",
            pending.strike,
            pending.limit,
        )

    if abs(gap) < settings.ema_gap_min_points:
        return _signal(now, "EMA gap", None, ema_gap, "Skipped", f"EMA gap {gap:+.1f} < {settings.ema_gap_min_points}", strike, None)

    side: OptionSide = "CE" if gap > 0 else "PE"
    quote = ctx.atm_ce if side == "CE" else ctx.atm_pe
    option_ltp = quote.ltp

    spread = max(quote.ask - quote.bid, 0.0)
    if settings.spread_filter_enabled and quote.bid > 0 and quote.ask > 0 and spread > settings.max_bid_ask_spread:
        return _signal(now, "Liquidity", side, ema_gap, "Skipped", f"Bid-ask spread Rs {spread:.2f} too wide", strike, option_ltp)

    if option_ltp <= 0:
        return _signal(now, "Liquidity", side, ema_gap, "Skipped", "ATM option has no LTP", strike, option_ltp)

    if settings.vix_filter_enabled and ctx.india_vix is not None and ctx.india_vix >= settings.max_india_vix:
        return _signal(now, "Liquidity", side, ema_gap, "Skipped", f"India VIX {ctx.india_vix:.1f} above cap", strike, option_ltp)

    effective_capital = settings.capital_budget
    if remaining_daily_budget is not None:
        effective_capital = max(0.0, settings.capital_budget + remaining_daily_budget - settings.daily_risk)
    sizing_settings = settings.model_copy(update={"capital_budget": effective_capital})
    lots = capital_lots(sizing_settings, option_ltp) if settings.use_full_capital else settings.lots_per_trade
    if lots < 1:
        return _signal(now, "Sizing", side, ema_gap, "Skipped", "Cannot support 1 lot", strike, option_ltp)

    limit = _limit_price(option_ltp)
    if limit <= 0:
        return _signal(now, "Limit", side, ema_gap, "Skipped", f"ATM premium too low to arm limit ({option_ltp:.1f})", strike, option_ltp)

    _pending_limit_order = PendingLimitOrder(side=side, strike=strike, limit=float(limit), armed_candle_count=candle_count)
    _pending_last_candle_count = candle_count
    return _signal(
        now,
        "EMA ATM limit armed",
        side,
        ema_gap,
        "Skipped",
        (
            f"Armed BUY limit {limit:.1f} on {side} {strike}; "
            f"premium {option_ltp:.1f}, EMA gap {gap:+.1f}, approx {lots} lot(s)"
        ),
        strike,
        float(limit),
    )


def build_demo_context(market: DemoMarket, strike: int, expiry: str | None = None) -> MarketContext:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
    bar = CandleBar(market.nifty_spot - 2, market.nifty_spot + 3, market.nifty_spot - 4, market.nifty_spot + 1, 1000)
    walls = OiWallMap(call_wall=strike + 100, put_wall=strike - 100, pin_strike=float(strike), pcr=1.0, total_call_oi=0, total_put_oi=0)
    vwap_value, vwap_label = calc_vwap_or_twap([bar])
    chain_oc = build_demo_chain_oc(
        market.nifty_spot,
        strike,
        market.atm_ce_ltp,
        market.atm_pe_ltp,
    )
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
        chain_oc=chain_oc,
        ema_20=market.ema_15 - 8.0,
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
    session_candles_raw: Optional[dict] = None,
    history_seeded: bool = False,
) -> MarketContext:
    spot = float(chain.get("last_price") or 0)
    candles = parse_candles(candles_raw)
    session_candles = parse_candles(session_candles_raw) if session_candles_raw else candles
    indicator_bars = session_candles or candles
    closes = [bar.close for bar in indicator_bars]
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
    session_high = max((bar.high for bar in session_candles), default=spot)
    session_low = min((bar.low for bar in session_candles), default=spot)
    vwap_value, vwap_label = calc_vwap_or_twap(session_candles)
    return MarketContext(
        spot=spot,
        ema_9=ema_9,
        ema_15=ema_15,
        ema_9_history=ema_series[-3:],
        candles=indicator_bars,
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
        history_seeded=history_seeded,
    )
