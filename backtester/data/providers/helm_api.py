"""Dhan API — NIFTY options CE/PE ATM ±N."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd


def _strike_labels(n: int) -> list[str]:
    return [f"ATM{i:+d}" if i else "ATM" for i in range(-n, n + 1)]


def _pick_expiry(expiries: list[str], as_of: date) -> str:
    as_of_str = as_of.isoformat()
    future = sorted(v for v in expiries if v >= as_of_str)
    if len(future) >= 2:
        return future[1]
    return future[0] if future else expiries[-1]


def _parse_block(block: dict, opt_type: str, strike_label: str) -> list[dict]:
    if not isinstance(block, dict):
        return []
    side = "CE" if opt_type == "CALL" else "PE"
    timestamps = block.get("timestamp") or []
    opens = block.get("open") or []
    highs = block.get("high") or []
    lows = block.get("low") or []
    closes = block.get("close") or []
    ois = block.get("oi") or []
    ivs = block.get("iv") or []
    strikes = block.get("strike") or []
    volumes = block.get("volume") or []
    rows: list[dict] = []
    for i in range(len(closes)):
        ts = datetime.fromtimestamp(int(timestamps[i])) if i < len(timestamps) else None
        if ts is None:
            continue
        strike = int(float(strikes[i])) if i < len(strikes) else 0
        rows.append({
            "timestamp": ts,
            "symbol": "NIFTY",
            "strike": strike,
            "opt_type": side,
            "open": float(opens[i]) if i < len(opens) else float(closes[i]),
            "high": float(highs[i]) if i < len(highs) else float(closes[i]),
            "low": float(lows[i]) if i < len(lows) else float(closes[i]),
            "close": float(closes[i]),
            "oi": int(ois[i]) if i < len(ois) else 0,
            "volume": float(volumes[i]) if i < len(volumes) else 0.0,
            "iv": float(ivs[i]) if i < len(ivs) else None,
            "strike_label": strike_label,
        })
    return rows


def fetch_options_day(trading_date: str | date, strikes_around_atm: int = 10) -> pd.DataFrame:
    """Download 1m CE/PE for ATM ±N on one trading day."""
    from ._bridge import get_dhan_classes

    DhanAdapter, DhanApiError = get_dhan_classes()
    adapter = DhanAdapter()
    if not adapter.authenticate():
        raise DhanApiError("Dhan credentials not configured (set DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN)")

    day = date.fromisoformat(str(trading_date)[:10])
    tomorrow = day + timedelta(days=1)
    expiries = adapter.get_expiry_list()
    if not expiries:
        raise DhanApiError("No NIFTY expiries from Dhan")

    all_rows: list[dict] = []
    errors: list[str] = []
    for label in _strike_labels(strikes_around_atm):
        for opt_type in ("CALL", "PUT"):
            try:
                payload = adapter.get_rolling_expired_options(
                    from_date=day.isoformat(),
                    to_date=tomorrow.isoformat(),
                    strike=label,
                    drv_option_type=opt_type,
                    expiry_code=2,
                    expiry_flag="WEEK",
                    interval="1",
                )
                side_key = "ce" if opt_type == "CALL" else "pe"
                block = payload.get(side_key) or payload.get(opt_type.lower()) or {}
                all_rows.extend(_parse_block(block, opt_type, label))
            except DhanApiError as exc:
                errors.append(f"{label} {opt_type}: {exc}")

    if not all_rows:
        msg = "; ".join(errors[:3]) if errors else "no rows"
        raise DhanApiError(f"No Dhan options for {day}: {msg}")

    df = pd.DataFrame(all_rows)
    df = df.drop(columns=["strike_label"], errors="ignore")
    df = df.sort_values(["timestamp", "strike", "opt_type"]).reset_index(drop=True)
    df["oi_chg"] = df.groupby(["strike", "opt_type"])["oi"].diff().fillna(0).astype(int)
    return df


def fetch_options(
    symbol: str = "NIFTY",
    start: str | None = None,
    end: str | None = None,
    interval: str = "5min",
    strikes_around_atm: int = 18,
) -> pd.DataFrame:
    from ..store import trading_days

    if not start or not end:
        raise ValueError("start and end required")
    frames = [fetch_options_day(d, strikes_around_atm) for d in trading_days(start, end)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise ValueError(f"No Dhan options for {start}→{end}")
    return pd.concat(frames, ignore_index=True)
