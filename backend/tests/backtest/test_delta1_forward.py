from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import pytest

from app.models import StrategySettings
from app.oi_analysis import OiWallMap
from app.signal_engine import (
    CandleBar,
    MarketContext,
    OptionQuote,
    build_demo_chain_oc,
    evaluate_delta1_entry_signal,
    select_delta1_strike,
)

IST = ZoneInfo("Asia/Kolkata")


def _chain_row(ce_delta: float, pe_delta: float, ce_ltp: float = 200.0, pe_ltp: float = 200.0) -> dict:
    return {
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


def _test_context(
    *,
    spot: float = 23500.0,
    ema_9: float = 23510.0,
    ema_20: float = 23500.0,
    chain_oc: Optional[dict] = None,
    candles: Optional[list] = None,
) -> MarketContext:
    bars = candles or [CandleBar(23490, 23510, 23485, 23505, 1000)] * 25
    strike = 23500
    chain = chain_oc or build_demo_chain_oc(spot, strike, 110.0, 95.0)
    walls = OiWallMap(call_wall=23600, put_wall=23400, pin_strike=23500.0, pcr=1.0, total_call_oi=0, total_put_oi=0)
    return MarketContext(
        spot=spot,
        ema_9=ema_9,
        ema_15=ema_20 - 2,
        ema_9_history=[ema_9 - 2, ema_9 - 1, ema_9],
        candles=bars,
        vwap=spot,
        atr_14=20,
        session_high=spot + 30,
        session_low=spot - 30,
        atm_strike=strike,
        atm_ce=OptionQuote(110, 109.5, 110.5, 0, 12, 0.5),
        atm_pe=OptionQuote(95, 94.5, 95.5, 0, 12, -0.5),
        walls=walls,
        gamma_flip=float(strike),
        expiry="2026-07-07",
        chain_oc=chain,
        ema_20=ema_20,
        history_seeded=True,
    )


def test_select_delta1_strike_ce_pe():
    chain = {
        f"{23000:.6f}": _chain_row(0.95, -0.05, ce_ltp=350),
        f"{23100:.6f}": _chain_row(0.88, -0.12, ce_ltp=300),
        f"{23200:.6f}": _chain_row(0.75, -0.25, ce_ltp=250),
        f"{23500:.6f}": _chain_row(0.50, -0.50, ce_ltp=110, pe_ltp=95),
        f"{23800:.6f}": _chain_row(0.12, -0.88, pe_ltp=300),
        f"{23900:.6f}": _chain_row(0.05, -0.95, pe_ltp=350),
    }
    ctx = _test_context(chain_oc=chain)

    ce_strike, ce_quote = select_delta1_strike(ctx, "CE")
    pe_strike, pe_quote = select_delta1_strike(ctx, "PE")

    assert ce_strike == 23000
    assert ce_quote.delta == pytest.approx(0.95)
    assert pe_strike == 23900
    assert pe_quote.delta == pytest.approx(-0.95)


def test_select_delta1_strike_itm_fallback_without_greeks():
    chain = {
        f"{23000:.6f}": {
            "ce": {"last_price": 350, "top_bid_price": 349.5, "top_ask_price": 350.5, "oi": 1, "implied_volatility": 12},
            "pe": {"last_price": 20, "top_bid_price": 19.5, "top_ask_price": 20.5, "oi": 1, "implied_volatility": 12},
        },
        f"{23900:.6f}": {
            "ce": {"last_price": 20, "top_bid_price": 19.5, "top_ask_price": 20.5, "oi": 1, "implied_volatility": 12},
            "pe": {"last_price": 350, "top_bid_price": 349.5, "top_ask_price": 350.5, "oi": 1, "implied_volatility": 12},
        },
    }
    ctx = _test_context(chain_oc=chain)

    ce_strike, _ = select_delta1_strike(ctx, "CE")
    pe_strike, _ = select_delta1_strike(ctx, "PE")

    assert ce_strike == 23000
    assert pe_strike == 23900


def test_delta1_immediate_entry():
    settings = StrategySettings(
        vix_filter_enabled=False,
        spread_filter_enabled=False,
        ema_gap_min_points=6,
    )
    ctx = _test_context(ema_9=23512.0, ema_20=23500.0)
    now = datetime(2025, 6, 2, 10, 0, tzinfo=IST)

    signal = evaluate_delta1_entry_signal(now, settings, ctx, has_open_position=False)

    assert signal.status == "Taken"
    assert signal.signal == "EMA delta-1 entry"
    assert signal.side == "CE"
    assert signal.strike == 23000
    assert signal.strike != ctx.atm_strike


def test_delta1_no_pending_state():
    settings = StrategySettings(vix_filter_enabled=False, spread_filter_enabled=False)
    ctx = _test_context(ema_9=23512.0, ema_20=23500.0)
    now = datetime(2025, 6, 2, 10, 0, tzinfo=IST)

    first = evaluate_delta1_entry_signal(now, settings, ctx, has_open_position=False)
    second = evaluate_delta1_entry_signal(now, settings, ctx, has_open_position=False)

    assert first.status == "Taken"
    assert second.status == "Taken"
    assert "limit" not in first.signal.lower()
    assert "limit" not in second.signal.lower()


def test_delta1_exit_defaults():
    settings = StrategySettings(target_rupees=3, stop_loss_rupees=10)
    entry = 250.0
    target = entry + settings.target_rupees
    stop = max(0.05, entry - settings.stop_loss_rupees)
    assert target == pytest.approx(253.0)
    assert stop == pytest.approx(240.0)


def test_delta1_entry_blocked_by_gap():
    settings = StrategySettings(vix_filter_enabled=False, spread_filter_enabled=False, ema_gap_min_points=6)
    ctx = _test_context(ema_9=23502.0, ema_20=23500.0)
    now = datetime(2025, 6, 2, 10, 0, tzinfo=IST)

    signal = evaluate_delta1_entry_signal(now, settings, ctx, has_open_position=False)

    assert signal.status == "Skipped"
    assert signal.signal == "EMA gap"


def test_delta1_store_default_settings():
    from app.strategy_module import get_strategy_v1

    get_strategy_v1()
    from app.store import PaperTradingStore

    store = PaperTradingStore()
    assert store.settings.target_rupees == 3
    assert store.settings.stop_loss_rupees == 10
