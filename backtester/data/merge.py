"""Merge Yahoo spot data with Dhan option chain into long-format bars."""

from __future__ import annotations

import pandas as pd

_COLUMNS = [
    "timestamp", "symbol", "spot_open", "spot_high", "spot_low", "spot_close",
    "strike", "opt_type", "open", "high", "low", "close", "oi", "oi_chg", "volume", "iv",
]


def merge_spot_and_options(spot_df: pd.DataFrame, options_df: pd.DataFrame) -> pd.DataFrame:
    """Join spot OHLC (+ optional ATM IV from Yahoo) onto each option row by timestamp."""
    if options_df.empty and spot_df.empty:
        return pd.DataFrame(columns=_COLUMNS)

    if spot_df.empty:
        return _options_only(options_df)

    if options_df.empty:
        return _spot_only_rows(spot_df)

    spot = spot_df.copy()
    spot["timestamp"] = pd.to_datetime(spot["timestamp"])
    spot = spot.sort_values("timestamp").drop_duplicates("timestamp", keep="last")

    opts = options_df.copy()
    opts["timestamp"] = pd.to_datetime(opts["timestamp"])

    spot_cols = ["timestamp", "spot_open", "spot_high", "spot_low", "spot_close"]
    for col in spot_cols[1:]:
        if col not in spot.columns:
            alt = col.replace("spot_", "")
            if alt in spot.columns:
                spot = spot.rename(columns={alt: col})

    if "spot_close" not in spot.columns and "close" in spot.columns:
        spot = spot.rename(columns={
            "open": "spot_open",
            "high": "spot_high",
            "low": "spot_low",
            "close": "spot_close",
        })

    # Yahoo may supply ATM IV as a separate column on spot bars
    if "iv" in spot.columns and "atm_iv" not in spot.columns:
        spot = spot.rename(columns={"iv": "atm_iv"})

    merge_cols = [c for c in spot_cols if c in spot.columns]
    extra = [c for c in ("atm_iv", "symbol") if c in spot.columns]
    merged = opts.merge(spot[merge_cols + extra], on="timestamp", how="left")

    if "atm_iv" in merged.columns:
        if "iv" not in merged.columns:
            merged["iv"] = merged["atm_iv"]
        else:
            merged["iv"] = merged["iv"].fillna(merged["atm_iv"])
        merged = merged.drop(columns=["atm_iv"], errors="ignore")

    if "symbol" not in merged.columns:
        merged["symbol"] = "NIFTY"

    return merged


def _options_only(options_df: pd.DataFrame) -> pd.DataFrame:
    out = options_df.copy()
    if "symbol" not in out.columns:
        out["symbol"] = "NIFTY"
    return out


def _spot_only_rows(spot_df: pd.DataFrame) -> pd.DataFrame:
    """Rare fallback when only spot is available — one synthetic ATM row per bar."""
    rows = []
    for _, row in spot_df.iterrows():
        ts = row["timestamp"]
        spot_close = row.get("spot_close", row.get("close", 0))
        rows.append({
            "timestamp": ts,
            "symbol": "NIFTY",
            "spot_open": row.get("spot_open", row.get("open", spot_close)),
            "spot_high": row.get("spot_high", row.get("high", spot_close)),
            "spot_low": row.get("spot_low", row.get("low", spot_close)),
            "spot_close": spot_close,
            "strike": int(round(float(spot_close) / 50) * 50),
            "opt_type": "CE",
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "oi": None,
            "oi_chg": None,
            "volume": None,
            "iv": row.get("iv", row.get("atm_iv")),
        })
    return pd.DataFrame(rows)
