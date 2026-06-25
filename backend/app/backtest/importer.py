from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from datetime import datetime
from typing import Any

import pandas as pd

from .db import get_connection

CONTRACT_FILENAME_RE = re.compile(
    r"^(?:NIFTY|BANKNIFTY|SENSEX)_(\d+)_(CE|PE|CALL|PUT)_(\d{1,2})_([A-Z]{3})_(\d{2})\.csv$",
    re.IGNORECASE,
)
MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def detect_csv(text: str) -> dict:
    sample = text[:4096]
    delimiter = ","
    if sample.count(";") > sample.count(","):
        delimiter = ";"
    elif sample.count("\t") > sample.count(","):
        delimiter = "\t"
    reader = csv.reader(io.StringIO(sample), delimiter=delimiter)
    headers = next(reader, [])
    rows = []
    for index, row in enumerate(reader):
        rows.append(row)
        if index >= 4:
            break
    return {"delimiter": delimiter, "headers": headers, "sample_rows": rows}


def preview_csv(content: bytes, filename: str) -> dict:
    text = content.decode("utf-8", errors="replace")
    meta = detect_csv(text)
    return {"filename": filename, **meta}


def import_nifty_candles(content: bytes, mapping: dict[str, str], batch_id: str | None = None) -> dict:
    batch_id = batch_id or str(uuid.uuid4())
    df = pd.read_csv(io.BytesIO(content))
    col_map = mapping or {h: h for h in df.columns}
    conn = get_connection()
    count = 0
    try:
        for _, row in df.iterrows():
            ts = pd.to_datetime(row[col_map["timestamp"]])
            conn.execute(
                """
                INSERT OR REPLACE INTO underlying_bars
                (timestamp_ist, symbol, timeframe, open, high, low, close, volume, source, import_batch_id)
                VALUES (?, 'NIFTY', ?, ?, ?, ?, ?, ?, 'csv', ?)
                """,
                [
                    ts,
                    mapping.get("timeframe", "1m"),
                    float(row[col_map["open"]]),
                    float(row[col_map["high"]]),
                    float(row[col_map["low"]]),
                    float(row[col_map["close"]]),
                    float(row.get(col_map.get("volume", "volume"), 0) or 0),
                    batch_id,
                ],
            )
            count += 1
        conn.execute(
            "INSERT INTO import_batches (id, dataset_type, source, created_at, row_count, checksum, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [batch_id, "nifty_candles", "csv", datetime.utcnow(), count, hashlib.md5(content).hexdigest(), "completed"],
        )
    finally:
        conn.close()
    return {"batch_id": batch_id, "rows_imported": count}


def import_vix_bars(content: bytes, mapping: dict[str, str], batch_id: str | None = None) -> dict:
    batch_id = batch_id or str(uuid.uuid4())
    df = pd.read_csv(io.BytesIO(content))
    col_map = mapping or {h: h for h in df.columns}
    conn = get_connection()
    count = 0
    try:
        for _, row in df.iterrows():
            ts = pd.to_datetime(row[col_map["timestamp"]])
            conn.execute(
                """
                INSERT OR REPLACE INTO vix_bars
                (timestamp_ist, open, high, low, close, source, import_batch_id)
                VALUES (?, ?, ?, ?, ?, 'csv', ?)
                """,
                [
                    ts,
                    float(row.get(col_map.get("open", "open"), row[col_map["close"]]) or row[col_map["close"]]),
                    float(row.get(col_map.get("high", "high"), row[col_map["close"]]) or row[col_map["close"]]),
                    float(row.get(col_map.get("low", "low"), row[col_map["close"]]) or row[col_map["close"]]),
                    float(row[col_map["close"]]),
                    batch_id,
                ],
            )
            count += 1
        conn.execute(
            "INSERT INTO import_batches (id, dataset_type, source, created_at, row_count, checksum, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [batch_id, "india_vix", "csv", datetime.utcnow(), count, hashlib.md5(content).hexdigest(), "completed"],
        )
    finally:
        conn.close()
    return {"batch_id": batch_id, "rows_imported": count}


def _is_wide_option_format(df: pd.DataFrame) -> bool:
    lowered = {str(c).lower() for c in df.columns}
    return any(c.startswith("ce_") for c in lowered) and any(c.startswith("pe_") for c in lowered)


def _wide_side_value(row: pd.Series, prefix: str, field: str, fallback: float = 0.0) -> float:
    for key in (f"{prefix}_{field}", f"{prefix}{field}"):
        if key in row.index:
            val = row[key]
            if pd.notna(val):
                return float(val)
    return fallback


def import_option_bars(content: bytes, mapping: dict[str, str], batch_id: str | None = None) -> dict:
    batch_id = batch_id or str(uuid.uuid4())
    df = pd.read_csv(io.BytesIO(content))
    if mapping.get("format") == "wide" or _is_wide_option_format(df):
        return _import_option_bars_wide(df, mapping, batch_id, content)
    conn = get_connection()
    count = 0
    try:
        for _, row in df.iterrows():
            ts = pd.to_datetime(row[mapping["timestamp"]])
            side = str(row[mapping["side"]]).upper()
            if side in ("CALL", "C"):
                side = "CE"
            if side in ("PUT", "P"):
                side = "PE"
            conn.execute(
                """
                INSERT OR REPLACE INTO option_bars
                (timestamp_ist, underlying, expiry_date, strike, option_side, open, high, low, close, ltp,
                 volume, open_interest, implied_volatility, bid, ask, delta, gamma, source, import_batch_id)
                VALUES (?, 'NIFTY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'csv', ?)
                """,
                [
                    ts,
                    pd.to_datetime(row[mapping["expiry"]]).date(),
                    int(row[mapping["strike"]]),
                    side,
                    float(row[mapping["open"]]),
                    float(row[mapping["high"]]),
                    float(row[mapping["low"]]),
                    float(row[mapping["close"]]),
                    float(row.get(mapping.get("ltp", "close"), row[mapping["close"]])),
                    float(row.get(mapping.get("volume", "volume"), 0) or 0),
                    int(row.get(mapping.get("open_interest", "open_interest"), 0) or 0),
                    float(row.get(mapping.get("implied_volatility", "implied_volatility"), 0) or 0) or None,
                    float(row.get(mapping.get("bid", "bid"), 0) or 0) or None,
                    float(row.get(mapping.get("ask", "ask"), 0) or 0) or None,
                    float(row.get(mapping.get("delta", "delta"), 0) or 0) or None,
                    float(row.get(mapping.get("gamma", "gamma"), 0) or 0) or None,
                    batch_id,
                ],
            )
            count += 1
        conn.execute(
            "INSERT INTO import_batches (id, dataset_type, source, created_at, row_count, checksum, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [batch_id, "option_bars", "csv", datetime.utcnow(), count, hashlib.md5(content).hexdigest(), "completed"],
        )
    finally:
        conn.close()
    return {"batch_id": batch_id, "rows_imported": count}


def _import_option_bars_wide(df: pd.DataFrame, mapping: dict[str, str], batch_id: str, content: bytes) -> dict:
    ts_col = mapping.get("timestamp", "timestamp")
    expiry_col = mapping.get("expiry", "expiry")
    strike_col = mapping.get("strike", "strike")
    conn = get_connection()
    count = 0
    try:
        for _, row in df.iterrows():
            ts = pd.to_datetime(row[ts_col])
            expiry = pd.to_datetime(row[expiry_col]).date()
            strike = int(row[strike_col])
            for side, prefix in (("CE", "ce"), ("PE", "pe")):
                close = _wide_side_value(row, prefix, "close")
                if close <= 0:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO option_bars
                    (timestamp_ist, underlying, expiry_date, strike, option_side, open, high, low, close, ltp,
                     volume, open_interest, implied_volatility, bid, ask, delta, gamma, source, import_batch_id)
                    VALUES (?, 'NIFTY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'csv', ?)
                    """,
                    [
                        ts,
                        expiry,
                        strike,
                        side,
                        _wide_side_value(row, prefix, "open", close),
                        _wide_side_value(row, prefix, "high", close),
                        _wide_side_value(row, prefix, "low", close),
                        close,
                        close,
                        int(_wide_side_value(row, prefix, "volume", 0)),
                        int(_wide_side_value(row, prefix, "open_interest", 0) or _wide_side_value(row, prefix, "oi", 0)),
                        _wide_side_value(row, prefix, "implied_volatility", 0) or None,
                        _wide_side_value(row, prefix, "bid", 0) or None,
                        _wide_side_value(row, prefix, "ask", 0) or None,
                        _wide_side_value(row, prefix, "delta", 0) or None,
                        _wide_side_value(row, prefix, "gamma", 0) or None,
                        batch_id,
                    ],
                )
                count += 1
        conn.execute(
            "INSERT INTO import_batches (id, dataset_type, source, created_at, row_count, checksum, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [batch_id, "option_bars_wide", "csv", datetime.utcnow(), count, hashlib.md5(content).hexdigest(), "completed"],
        )
    finally:
        conn.close()
    return {"batch_id": batch_id, "rows_imported": count, "format": "wide"}


def parse_contract_filename(filename: str) -> dict | None:
    """Parse NIFTY_25550_CE_03_OCT_24.csv style names."""
    match = CONTRACT_FILENAME_RE.match(filename.strip())
    if not match:
        return None
    strike, side, day, mon, yy = match.groups()
    side = "CE" if side.upper() in ("CE", "CALL") else "PE"
    month = MONTH_MAP.get(mon.upper())
    if not month:
        return None
    year = 2000 + int(yy)
    expiry = datetime(year, month, int(day)).date()
    return {"strike": int(strike), "side": side, "expiry": expiry}


def _parse_option_timestamp(row: pd.Series, columns: list[str]) -> datetime:
    lowered = {c.lower(): c for c in columns}
    if "timestamp" in lowered:
        return pd.to_datetime(row[lowered["timestamp"]])
    if "date" in lowered and "time" in lowered:
        date_val = str(row[lowered["date"]]).strip()
        time_val = str(row[lowered["time"]]).strip()
        combined = f"{date_val} {time_val}"
        parsed = pd.to_datetime(combined, dayfirst=True, errors="coerce")
        if pd.isna(parsed):
            parsed = pd.to_datetime(combined, errors="coerce")
        return parsed
    if "date" in lowered:
        return pd.to_datetime(row[lowered["date"]], dayfirst=True)
    raise ValueError("No timestamp/date column found")


def import_option_contract_file(
    content: bytes, filename: str, batch_id: str | None = None, record_batch: bool = True
) -> dict:
    """Import one per-contract CSV (strike/side/expiry from filename)."""
    meta = parse_contract_filename(filename)
    if meta is None:
        raise ValueError(f"Cannot parse contract from filename: {filename}")

    batch_id = batch_id or str(uuid.uuid4())
    df = pd.read_csv(io.BytesIO(content))
    cols = list(df.columns)
    lowered = {c.lower(): c for c in cols}

    def col(name: str, default: str | None = None) -> str | None:
        if name in lowered:
            return lowered[name]
        return default

    open_c = col("open", "close")
    high_c = col("high", "close")
    low_c = col("low", "close")
    close_c = col("close") or col("ltp")
    if not close_c:
        raise ValueError(f"{filename}: missing close/ltp column")

    conn = get_connection()
    count = 0
    try:
        for _, row in df.iterrows():
            ts = _parse_option_timestamp(row, cols)
            if pd.isna(ts):
                continue
            o = float(row[open_c]) if open_c else float(row[close_c])
            h = float(row[high_c]) if high_c else o
            l = float(row[low_c]) if low_c else o
            c = float(row[close_c])
            vol_col = col("volume")
            vol = int(float(row[vol_col] or 0)) if vol_col else 0
            oi_col = col("open_interest") or col("oi")
            oi = int(float(row[oi_col] or 0)) if oi_col else 0
            conn.execute(
                """
                INSERT OR REPLACE INTO option_bars
                (timestamp_ist, underlying, expiry_date, strike, option_side, open, high, low, close, ltp,
                 volume, open_interest, source, import_batch_id)
                VALUES (?, 'NIFTY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'csv', ?)
                """,
                [ts, meta["expiry"], meta["strike"], meta["side"], o, h, l, c, c, vol, oi, batch_id],
            )
            count += 1
        if count and record_batch:
            conn.execute(
                "INSERT INTO import_batches (id, dataset_type, source, created_at, row_count, checksum, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [batch_id, "option_contract", "csv", datetime.utcnow(), count, hashlib.md5(content).hexdigest(), "completed"],
            )
    finally:
        conn.close()
    return {
        "batch_id": batch_id,
        "rows_imported": count,
        "filename": filename,
        "strike": meta["strike"],
        "side": meta["side"],
        "expiry": str(meta["expiry"]),
    }


def import_option_bars_bulk(files: list[tuple[str, bytes]]) -> dict:
    """Import many per-contract or standard option CSV files in one batch."""
    batch_id = str(uuid.uuid4())
    total_rows = 0
    imported_files = 0
    errors: list[dict] = []

    for filename, content in files:
        try:
            if parse_contract_filename(filename):
                result = import_option_contract_file(content, filename, batch_id, record_batch=False)
            else:
                result = import_option_bars(content, {}, batch_id)
            total_rows += result.get("rows_imported", 0)
            imported_files += 1
        except Exception as error:
            errors.append({"filename": filename, "error": str(error)})

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO import_batches (id, dataset_type, source, created_at, row_count, checksum, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [batch_id, "option_bars_bulk", "csv", datetime.utcnow(), total_rows, "", "completed" if not errors else "partial"],
        )
    finally:
        conn.close()

    return {
        "batch_id": batch_id,
        "files_received": len(files),
        "files_imported": imported_files,
        "rows_imported": total_rows,
        "errors": errors[:20],
        "error_count": len(errors),
    }


def list_imports() -> list[dict]:
    conn = get_connection(read_only=True)
    try:
        rows = conn.execute(
            "SELECT id, dataset_type, source, created_at, row_count, status FROM import_batches ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        return [
            {"id": r[0], "dataset_type": r[1], "source": r[2], "created_at": str(r[3]), "row_count": r[4], "status": r[5]}
            for r in rows
        ]
    finally:
        conn.close()


def _day_map_summary(day_map: dict[str, int]) -> dict:
    if not day_map:
        return {"count": 0, "first": None, "last": None}
    keys = sorted(day_map.keys())
    return {"count": len(keys), "first": keys[0], "last": keys[-1]}


def coverage_report(date_from: str, date_to: str) -> dict:
    conn = get_connection(read_only=True)
    try:
        nifty = conn.execute(
            """
            SELECT CAST(timestamp_ist AS DATE) d, COUNT(*) c
            FROM underlying_bars WHERE symbol='NIFTY'
              AND CAST(timestamp_ist AS DATE) BETWEEN ? AND ?
            GROUP BY 1 ORDER BY 1
            """,
            [date_from, date_to],
        ).fetchall()
        options = conn.execute(
            """
            SELECT CAST(timestamp_ist AS DATE) d, COUNT(*) c
            FROM option_bars
            WHERE CAST(timestamp_ist AS DATE) BETWEEN ? AND ?
            GROUP BY 1 ORDER BY 1
            """,
            [date_from, date_to],
        ).fetchall()
        vix = conn.execute(
            """
            SELECT CAST(timestamp_ist AS DATE) d, COUNT(*) c
            FROM vix_bars
            WHERE CAST(timestamp_ist AS DATE) BETWEEN ? AND ?
            GROUP BY 1 ORDER BY 1
            """,
            [date_from, date_to],
        ).fetchall()
        nifty_days = {str(r[0]): r[1] for r in nifty}
        option_days = {str(r[0]): r[1] for r in options}
        vix_days = {str(r[0]): r[1] for r in vix}
        overlap = sorted(set(nifty_days.keys()) & set(option_days.keys()))
        return {
            "date_from": date_from,
            "date_to": date_to,
            "nifty_days": nifty_days,
            "option_days": option_days,
            "vix_days": vix_days,
            "summary": {
                "nifty": _day_map_summary(nifty_days),
                "options": _day_map_summary(option_days),
                "vix": _day_map_summary(vix_days),
                "backtest_ready_days": len(overlap),
                "backtest_ready_first": overlap[0] if overlap else None,
                "backtest_ready_last": overlap[-1] if overlap else None,
            },
        }
    finally:
        conn.close()


def data_inventory() -> dict:
    """All backtest-ready days in DuckDB (NIFTY + options on same date)."""
    conn = get_connection(read_only=True)
    try:
        nifty_rows = conn.execute(
            """
            SELECT CAST(timestamp_ist AS DATE) d, COUNT(*) c
            FROM underlying_bars WHERE symbol='NIFTY'
            GROUP BY 1 ORDER BY 1 DESC
            """
        ).fetchall()
        option_rows = conn.execute(
            """
            SELECT CAST(timestamp_ist AS DATE) d, COUNT(*) c
            FROM option_bars
            GROUP BY 1 ORDER BY 1 DESC
            """
        ).fetchall()
        vix_rows = conn.execute(
            """
            SELECT CAST(timestamp_ist AS DATE) d, COUNT(*) c
            FROM vix_bars
            GROUP BY 1 ORDER BY 1 DESC
            """
        ).fetchall()
        nifty_days = {str(r[0]): int(r[1]) for r in nifty_rows}
        option_days = {str(r[0]): int(r[1]) for r in option_rows}
        vix_days = {str(r[0]): int(r[1]) for r in vix_rows}
        ready = sorted(set(nifty_days.keys()) & set(option_days.keys()), reverse=True)
        ready_days = [
            {
                "date": day,
                "nifty_bars": nifty_days.get(day, 0),
                "option_bars": option_days.get(day, 0),
                "vix_bars": vix_days.get(day, 0),
                "has_vix": day in vix_days,
            }
            for day in ready
        ]
        return {
            "ready_days": ready_days,
            "total_ready_days": len(ready_days),
            "nifty_only_days": len(set(nifty_days.keys()) - set(option_days.keys())),
            "options_only_days": len(set(option_days.keys()) - set(nifty_days.keys())),
            "summary": {
                "nifty": _day_map_summary(nifty_days),
                "options": _day_map_summary(option_days),
                "vix": _day_map_summary(vix_days),
            },
        }
    finally:
        conn.close()
