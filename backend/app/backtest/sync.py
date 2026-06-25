from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..strategy import nearest_nifty_strike


@dataclass
class SyncedSnapshot:
    nifty_timestamp: datetime
    chain_timestamp: datetime
    vix_timestamp: datetime | None
    spot: float
    chain_rows: list[dict]
    vix: float | None
    stale: bool
    stale_reason: str | None


def select_chain_snapshot(snapshots: list[tuple[datetime, list[dict]]], decision_time: datetime, staleness_seconds: int) -> tuple[datetime | None, list[dict], str | None]:
    eligible = [(ts, rows) for ts, rows in snapshots if ts <= decision_time]
    if not eligible:
        return None, [], "No option chain at or before decision time"
    chain_ts, rows = max(eligible, key=lambda item: item[0])
    age = (decision_time - chain_ts).total_seconds()
    if age > staleness_seconds:
        return chain_ts, rows, f"Chain stale by {age:.0f}s (max {staleness_seconds}s)"
    return chain_ts, rows, None


def select_vix(vix_rows: list[tuple[datetime, float]], decision_time: datetime) -> tuple[datetime | None, float | None]:
    eligible = [(ts, v) for ts, v in vix_rows if ts <= decision_time]
    if not eligible:
        return None, None
    return max(eligible, key=lambda item: item[0])


def filter_atm_window(rows: list[dict], spot: float, window: int = 10) -> list[dict]:
    atm = nearest_nifty_strike(spot)
    low = atm - window * 50
    high = atm + window * 50
    return [row for row in rows if low <= int(row.get("strike", 0)) <= high]


def completed_candles_before(candles: list[tuple[datetime, object]], decision_time: datetime) -> list:
    return [bar for ts, bar in candles if ts <= decision_time]
