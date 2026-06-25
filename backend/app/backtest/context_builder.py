from __future__ import annotations

from typing import Optional

from ..oi_analysis import build_oi_wall_map, estimate_gamma_flip
from ..signal_engine import CandleBar, MarketContext, quote_from_chain_row
from ..strategy import nearest_nifty_strike
from .candles import indicator_snapshot


def build_context_from_chain_rows(
    spot: float,
    expiry: str,
    rows: list[dict],
    candles: list[CandleBar],
    india_vix: Optional[float] = None,
    chain_window: int = 10,
) -> MarketContext:
    atm = nearest_nifty_strike(spot)
    chain_oc: dict = {}
    for row in rows:
        key = f"{int(row['strike']):.6f}"
        entry = chain_oc.setdefault(key, {"ce": {}, "pe": {}})
        payload = {
            "last_price": row.get("ltp") or row.get("close") or 0,
            "top_bid_price": row.get("bid") or 0,
            "top_ask_price": row.get("ask") or 0,
            "oi": row.get("oi") or 0,
            "implied_volatility": row.get("iv") or 0,
            "greeks": {"delta": row.get("delta") or 0, "gamma": row.get("gamma") or 0},
        }
        side = row.get("side") or row.get("option_side")
        if side in ("CE", "CALL"):
            entry["ce"] = payload
        else:
            entry["pe"] = payload

    indicators = indicator_snapshot(candles)
    walls = build_oi_wall_map(chain_oc, spot, chain_window)
    filtered = {"oc": {k: v for k, v in chain_oc.items()}}
    gamma_flip = estimate_gamma_flip(filtered, spot, walls)
    exact_key = f"{atm:.6f}"
    row = chain_oc.get(exact_key, {})
    return MarketContext(
        spot=spot,
        ema_9=indicators["ema_9"],
        ema_15=indicators["ema_15"],
        ema_9_history=indicators["ema_9_history"],
        candles=candles,
        vwap=indicators["vwap"],
        vwap_label=indicators["vwap_label"],
        atr_14=indicators["atr_14"],
        session_high=indicators["session_high"],
        session_low=indicators["session_low"],
        atm_strike=atm,
        atm_ce=quote_from_chain_row(row.get("ce") or {}),
        atm_pe=quote_from_chain_row(row.get("pe") or {}),
        walls=walls,
        gamma_flip=gamma_flip,
        expiry=expiry,
        chain_oc=chain_oc,
        india_vix=india_vix,
        ema_20=indicators.get("ema_20", indicators["ema_15"]),
        macd=indicators.get("macd", 0.0),
        macd_signal=indicators.get("macd_signal", 0.0),
    )
