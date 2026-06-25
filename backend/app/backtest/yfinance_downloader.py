from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any

import pandas as pd

from .db import ensure_data_dirs, get_connection
from .importer import import_nifty_candles, import_vix_bars

DEFAULT_PERIOD = "1d"

NIFTY_TICKER = "^NSEI"
VIX_TICKER = "^INDIAVIX"


def flatten_yahoo_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [str(level[0]).lower() for level in out.columns]
    else:
        out.columns = [str(col).lower() for col in out.columns]
    out = out.reset_index()
    ts_col = out.columns[0]
    out = out.rename(columns={ts_col: "timestamp"})
    for col in ("open", "high", "low", "close", "volume"):
        if col not in out.columns:
            out[col] = 0.0 if col == "volume" else pd.NA
    return out[["timestamp", "open", "high", "low", "close", "volume"]]


def to_ist_naive(timestamps: pd.Series) -> pd.Series:
    ts = pd.to_datetime(timestamps)
    if getattr(ts.dt, "tz", None) is None:
        return ts
    return ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)


def _frame_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue()


def _save_raw_csv(name: str, content: bytes) -> None:
    root = ensure_data_dirs()
    path = root / "raw" / "yahoo" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _download_ticker_for_date(ticker: str, trading_date: date) -> pd.DataFrame:
    import yfinance as yf

    start = trading_date.isoformat()
    end = (trading_date + timedelta(days=1)).isoformat()
    raw = yf.download(
        tickers=ticker,
        start=start,
        end=end,
        interval="1m",
        progress=False,
        auto_adjust=True,
    )
    flat = flatten_yahoo_frame(raw)
    if flat.empty:
        return flat
    flat["timestamp"] = to_ist_naive(flat["timestamp"])
    flat = flat.dropna(subset=["open", "high", "low", "close"])
    flat = flat[flat["timestamp"].dt.date == trading_date]
    return flat


def _download_ticker(ticker: str, period: str) -> pd.DataFrame:
    return _download_ticker_for_date(ticker, date.today())


def import_yahoo_nifty_vix_for_date(trading_date: date) -> dict[str, Any]:
    batch_id = str(uuid.uuid4())
    day_label = trading_date.isoformat()

    nifty_df = _download_ticker_for_date(NIFTY_TICKER, trading_date)
    vix_df = _download_ticker_for_date(VIX_TICKER, trading_date)

    if nifty_df.empty and vix_df.empty:
        return {
            "batch_id": batch_id,
            "trading_date": day_label,
            "nifty_rows": 0,
            "vix_rows": 0,
            "error": f"No Yahoo 1-minute bars for {day_label}. Try Dhan or pick a recent trading day.",
        }

    nifty_result: dict[str, Any] = {"rows_imported": 0}
    vix_result: dict[str, Any] = {"rows_imported": 0}

    if not nifty_df.empty:
        nifty_csv = _frame_to_csv_bytes(nifty_df)
        _save_raw_csv(f"nifty_{day_label}_{batch_id[:8]}.csv", nifty_csv)
        nifty_result = import_nifty_candles(
            nifty_csv,
            {"timestamp": "timestamp", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume", "timeframe": "1m"},
            batch_id=f"{batch_id}-nifty",
        )
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE underlying_bars SET source='yahoo' WHERE import_batch_id=?",
                [f"{batch_id}-nifty"],
            )
        finally:
            conn.close()

    if not vix_df.empty:
        vix_csv = _frame_to_csv_bytes(vix_df)
        _save_raw_csv(f"vix_{day_label}_{batch_id[:8]}.csv", vix_csv)
        vix_result = import_vix_bars(
            vix_csv,
            {"timestamp": "timestamp", "open": "open", "high": "high", "low": "low", "close": "close"},
            batch_id=f"{batch_id}-vix",
        )
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE vix_bars SET source='yahoo' WHERE import_batch_id=?",
                [f"{batch_id}-vix"],
            )
        finally:
            conn.close()

    date_from = None
    date_to = None
    for df in (nifty_df, vix_df):
        if not df.empty:
            start = df["timestamp"].min()
            end = df["timestamp"].max()
            date_from = start if date_from is None else min(date_from, start)
            date_to = end if date_to is None else max(date_to, end)

    checksum = hashlib.md5(f"{day_label}:{len(nifty_df)}:{len(vix_df)}".encode()).hexdigest()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO import_batches (id, dataset_type, source, created_at, row_count, checksum, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                batch_id,
                "yahoo_nifty_vix",
                "yahoo",
                datetime.utcnow(),
                int(nifty_result.get("rows_imported", 0)) + int(vix_result.get("rows_imported", 0)),
                checksum,
                "completed",
            ],
        )
    finally:
        conn.close()

    return {
        "batch_id": batch_id,
        "trading_date": day_label,
        "interval": "1m",
        "nifty_rows": nifty_result.get("rows_imported", 0),
        "vix_rows": vix_result.get("rows_imported", 0),
        "date_from": str(date_from) if date_from is not None else None,
        "date_to": str(date_to) if date_to is not None else None,
        "note": "Yahoo 1-minute history is limited to about the last 7 days.",
    }


def import_yahoo_nifty_vix(period: str = DEFAULT_PERIOD) -> dict[str, Any]:
    return import_yahoo_nifty_vix_for_date(date.today())
