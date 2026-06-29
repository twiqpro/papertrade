#!/usr/bin/env python3
"""
Download today's 1-minute NIFTY option bars (ATM±10, CE + PE) for the next weekly expiry.

Uses the same Dhan rolling-options API as Twiq's backtester (`POST /api/data/dhan/today-options`).
Credentials come from backend/.env — do NOT hardcode tokens in this file.

Usage (from repo root):
  cd backend && python3 ../ForDownloadingHistoricalData/download_today_1m_options.py
"""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")

from app.backtest.dhan_downloader import STRIKE_OFFSETS, sync_today_options_next_expiry  # noqa: E402


def export_csvs(result: dict, out_dir: Path) -> None:
    """Optional: dump imported rows from DuckDB to per-contract CSV folders."""
    from app.backtest.db import get_connection

    trading_date = result["trading_date"]
    expiry_date = result["expiry_date"]
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT timestamp_ist, strike, option_side, relative_strike, open, high, low, close,
                   volume, open_interest, implied_volatility
            FROM option_bars
            WHERE source='dhan' AND DATE(timestamp_ist)=? AND expiry_date=?
            ORDER BY relative_strike, option_side, timestamp_ist
            """,
            [trading_date, expiry_date],
        ).fetchall()
    finally:
        conn.close()

    base = out_dir / "Today 1m" / "NIFTY" / expiry_date
    grouped: dict[tuple[str, str], list] = {}
    for row in rows:
        rel = row[3] or "ATM"
        side = row[2]
        grouped.setdefault((rel, side), []).append(row)

    for (rel, side), items in grouped.items():
        folder = base / rel
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"NIFTY_{expiry_date}_{side}.csv"
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["timestamp", "strike", "side", "relative_strike", "open", "high", "low", "close", "volume", "oi", "iv"]
            )
            for item in items:
                writer.writerow(list(item))


def main() -> None:
    print("Downloading today's NIFTY options (ATM±10, next expiry, 1m)…")
    result = sync_today_options_next_expiry()
    print(
        f"Done: {result['imported']} bars | trading_date={result['trading_date']} "
        f"| expiry={result['expiry_date']} | failed_requests={result['requests_failed']}"
    )
    if result.get("errors"):
        print("Sample errors:", result["errors"])

    out_dir = Path(__file__).parent / "Options data 1 min"
    export_csvs(result, out_dir)
    print(f"CSV export folder: {out_dir}")


if __name__ == "__main__":
    main()
