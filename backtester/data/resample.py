"""Resample 1-minute bars to coarser intervals."""

from __future__ import annotations

import pandas as pd

INTERVAL_RULES = {"1min": "1min", "5min": "5min", "15min": "15min"}


def interval_minutes(interval: str) -> int:
    return {"1min": 1, "5min": 5, "15min": 15}.get(interval.lower(), 5)


def resample_spot(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    if df.empty or interval == "1min":
        return df
    rule = INTERVAL_RULES.get(interval, "5min")
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    out = out.set_index("timestamp")
    agg = out.resample(rule).agg({
        "spot_open": "first",
        "spot_high": "max",
        "spot_low": "min",
        "spot_close": "last",
        "iv": "last",
        "symbol": "first",
    }).dropna(subset=["spot_close"])
    return agg.reset_index()


def resample_options(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    if df.empty or interval == "1min":
        return df
    rule = INTERVAL_RULES.get(interval, "5min")
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    grouped = out.groupby(["strike", "opt_type"])
    parts = []
    for (_, _), grp in grouped:
        g = grp.set_index("timestamp")
        agg = g.resample(rule).agg({
            "open": "first", "high": "max", "low": "min", "close": "last",
            "oi": "last", "volume": "sum", "iv": "last", "symbol": "first",
        }).dropna(subset=["close"])
        agg["strike"] = grp["strike"].iloc[0]
        agg["opt_type"] = grp["opt_type"].iloc[0]
        parts.append(agg.reset_index())
    if not parts:
        return df
    merged = pd.concat(parts, ignore_index=True)
    merged = merged.sort_values(["timestamp", "strike", "opt_type"]).reset_index(drop=True)
    merged["oi_chg"] = merged.groupby(["strike", "opt_type"])["oi"].diff().fillna(0).astype(int)
    return merged
