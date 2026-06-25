from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..dhan_client import DhanAdapter, DhanApiError
from .db import ensure_data_dirs, get_connection
from .jobs import create_job, get_job, update_job


PILOT_FROM = date(2025, 12, 24)
PILOT_TO = date(2026, 6, 23)
INTRADAY_CHUNK_DAYS = 90
EXPIRED_CHUNK_DAYS = 30
STRIKE_OFFSETS = [f"ATM{i:+d}" if i != 0 else "ATM" for i in range(-10, 11)]
OPTION_TYPES = ("CALL", "PUT")


def _atm_label_to_offset(label: str | None) -> int | None:
    if not label:
        return None
    if label == "ATM":
        return 0
    if label.startswith("ATM+"):
        return int(label[4:])
    if label.startswith("ATM-"):
        return -int(label[4:])
    return None


def _save_raw(name: str, payload: dict) -> Path:
    root = ensure_data_dirs()
    path = root / "raw" / "dhan" / f"{name}.json"
    path.write_text(json.dumps(payload))
    return path


def _manifest_key(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def _manifest_done(conn, dataset_key: str) -> bool:
    row = conn.execute(
        "SELECT status FROM download_manifests WHERE dataset_key=? AND status='completed'",
        [dataset_key],
    ).fetchone()
    return row is not None


def _manifest_mark(conn, dataset_key: str, status: str, payload: dict, checksum: str) -> None:
    conn.execute(
        """
        INSERT INTO download_manifests (id, dataset_key, checksum, status, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataset_key) DO UPDATE SET checksum=excluded.checksum, status=excluded.status,
            metadata=excluded.metadata, created_at=excluded.created_at
        """,
        [str(uuid.uuid4()), dataset_key, checksum, status, json.dumps(payload), datetime.utcnow()],
    )


def _job_cancelled(job_id: str | None) -> bool:
    if not job_id:
        return False
    job = get_job(job_id)
    return job is not None and job["status"] == "cancelled"


def _import_nifty_chunk(conn, batch_id: str, current: date, candles: dict) -> int:
    imported = 0
    opens = candles.get("open") or []
    highs = candles.get("high") or []
    lows = candles.get("low") or []
    closes = candles.get("close") or []
    volumes = candles.get("volume") or []
    timestamps = candles.get("timestamp") or candles.get("start_Time") or []
    for index in range(len(opens)):
        if index < len(timestamps):
            ts = datetime.fromtimestamp(int(timestamps[index]))
        else:
            ts = datetime.combine(current, datetime.min.time()) + timedelta(minutes=9 * 60 + 15 + index)
        conn.execute(
            """
            INSERT OR REPLACE INTO underlying_bars
            (timestamp_ist, symbol, timeframe, open, high, low, close, volume, source, import_batch_id)
            VALUES (?, 'NIFTY', '1m', ?, ?, ?, ?, ?, 'dhan', ?)
            """,
            [
                ts,
                float(opens[index]),
                float(highs[index]),
                float(lows[index]),
                float(closes[index]),
                float(volumes[index]) if index < len(volumes) else 0.0,
                batch_id,
            ],
        )
        imported += 1
    return imported


def _import_vix_chunk(conn, batch_id: str, candles: dict) -> int:
    imported = 0
    closes = candles.get("close") or []
    opens = candles.get("open") or []
    highs = candles.get("high") or []
    lows = candles.get("low") or []
    timestamps = candles.get("timestamp") or []
    for index, close in enumerate(closes):
        ts = datetime.fromtimestamp(int(timestamps[index])) if index < len(timestamps) else datetime.utcnow()
        conn.execute(
            """
            INSERT OR REPLACE INTO vix_bars (timestamp_ist, open, high, low, close, source, import_batch_id)
            VALUES (?, ?, ?, ?, ?, 'dhan', ?)
            """,
            [
                ts,
                float(opens[index]) if index < len(opens) else float(close),
                float(highs[index]) if index < len(highs) else float(close),
                float(lows[index]) if index < len(lows) else float(close),
                float(close),
                batch_id,
            ],
        )
        imported += 1
    return imported


def _import_rolling_options(
    conn,
    batch_id: str,
    payload: dict,
    option_type: str,
    expiry_date: date | None = None,
    relative_strike: str | None = None,
) -> int:
    imported = 0
    side_key = "ce" if option_type == "CALL" else "pe"
    block = payload.get(side_key) or payload.get(option_type.lower()) or {}
    if not isinstance(block, dict):
        return 0
    timestamps = block.get("timestamp") or []
    opens = block.get("open") or []
    highs = block.get("high") or []
    lows = block.get("low") or []
    closes = block.get("close") or []
    ois = block.get("oi") or []
    ivs = block.get("iv") or []
    strikes = block.get("strike") or []
    volumes = block.get("volume") or []
    length = len(closes)
    for index in range(length):
        ts = datetime.fromtimestamp(int(timestamps[index])) if index < len(timestamps) else datetime.utcnow()
        strike = int(float(strikes[index])) if index < len(strikes) else 0
        side = "CE" if option_type == "CALL" else "PE"
        bar_expiry = expiry_date or ts.date()
        conn.execute(
            """
            INSERT OR REPLACE INTO option_bars
            (timestamp_ist, underlying, expiry_date, strike, option_side, relative_strike, open, high, low, close, ltp,
             volume, open_interest, implied_volatility, source, import_batch_id)
            VALUES (?, 'NIFTY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'dhan', ?)
            """,
            [
                ts,
                bar_expiry,
                strike,
                side,
                _atm_label_to_offset(relative_strike),
                float(opens[index]) if index < len(opens) else float(closes[index]),
                float(highs[index]) if index < len(highs) else float(closes[index]),
                float(lows[index]) if index < len(lows) else float(closes[index]),
                float(closes[index]),
                float(closes[index]),
                float(volumes[index]) if index < len(volumes) else 0.0,
                int(ois[index]) if index < len(ois) else 0,
                float(ivs[index]) if index < len(ivs) else None,
                batch_id,
            ],
        )
        imported += 1
    return imported


def _pick_next_expiry(expiries: list[str], as_of: date) -> str:
    as_of_str = as_of.isoformat()
    future = [value for value in sorted(expiries) if value >= as_of_str]
    if len(future) >= 2:
        return future[1]
    return future[0] if future else expiries[-1]


def sync_dhan_nifty_vix_for_date(trading_date: date) -> dict:
    adapter = DhanAdapter()
    if not adapter.authenticate():
        raise DhanApiError("Dhan credentials not configured")

    next_day = trading_date + timedelta(days=1)
    batch_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        candles = adapter.get_intraday_candles(
            from_date=trading_date.isoformat(),
            to_date=next_day.isoformat(),
            interval="1",
        )
        vix = adapter.get_vix_intraday(trading_date.isoformat(), next_day.isoformat(), interval="1")
        _save_raw(f"nifty_{trading_date}", candles)
        _save_raw(f"vix_{trading_date}", vix)
        nifty_rows = _import_nifty_chunk(conn, batch_id, trading_date, candles)
        vix_rows = _import_vix_chunk(conn, batch_id, vix)
        conn.execute(
            """
            INSERT INTO import_batches (id, dataset_type, source, created_at, row_count, status)
            VALUES (?, 'dhan_spot_vix', 'dhan', ?, ?, 'completed')
            """,
            [batch_id, datetime.utcnow(), nifty_rows + vix_rows],
        )
    finally:
        conn.close()

    return {
        "batch_id": batch_id,
        "trading_date": trading_date.isoformat(),
        "nifty_rows": nifty_rows,
        "vix_rows": vix_rows,
        "interval": "1m",
    }


def sync_options_for_date(trading_date: date, job_id: str | None = None, expiry_code: int = 2) -> dict:
    """Download 1m CE/PE for NIFTY ATM±10 on the next weekly expiry for a trading day."""
    adapter = DhanAdapter()
    if not adapter.authenticate():
        raise DhanApiError("Dhan credentials not configured")

    today = trading_date
    tomorrow = trading_date + timedelta(days=1)
    expiries = adapter.get_expiry_list()
    if not expiries:
        raise DhanApiError("No NIFTY expiries returned from Dhan")
    expiry_date = date.fromisoformat(_pick_next_expiry(expiries, trading_date))

    batch_id = str(uuid.uuid4())
    conn = get_connection()
    total_imported = 0
    completed = 0
    failed: list[str] = []
    try:
        for strike_label in STRIKE_OFFSETS:
            for option_type in OPTION_TYPES:
                if _job_cancelled(job_id):
                    update_job(job_id, "cancelled", {"imported": total_imported, "expiry": str(expiry_date)})
                    return {"cancelled": True, "imported": total_imported, "expiry_date": str(expiry_date)}

                key = f"options_{trading_date}_{expiry_date}_{strike_label}_{option_type}"
                try:
                    payload = adapter.get_rolling_expired_options(
                        from_date=today.isoformat(),
                        to_date=tomorrow.isoformat(),
                        strike=strike_label,
                        drv_option_type=option_type,
                        expiry_code=expiry_code,
                        expiry_flag="WEEK",
                        interval="1",
                    )
                    _save_raw(key.replace(":", "_"), payload if isinstance(payload, dict) else {"data": payload})
                    count = _import_rolling_options(
                        conn,
                        batch_id,
                        payload if isinstance(payload, dict) else {},
                        option_type,
                        expiry_date=expiry_date,
                        relative_strike=strike_label,
                    )
                    total_imported += count
                    completed += 1
                except DhanApiError as error:
                    failed.append(f"{strike_label} {option_type}: {error}")
                if job_id:
                    update_job(
                        job_id,
                        "running",
                        {
                            "phase": "options",
                            "imported": total_imported,
                            "trading_date": str(trading_date),
                            "expiry_date": str(expiry_date),
                            "completed_requests": completed,
                            "total_requests": len(STRIKE_OFFSETS) * len(OPTION_TYPES),
                            "cursor": f"{strike_label} {option_type}",
                        },
                    )
                time.sleep(0.15)

        conn.execute(
            """
            INSERT INTO import_batches (id, dataset_type, source, created_at, row_count, status)
            VALUES (?, 'dhan_today_options', 'dhan', ?, ?, 'completed')
            """,
            [batch_id, datetime.utcnow(), total_imported],
        )
    finally:
        conn.close()

    result = {
        "batch_id": batch_id,
        "imported": total_imported,
        "trading_date": str(trading_date),
        "expiry_date": str(expiry_date),
        "expiry_code": expiry_code,
        "strike_window": "ATM±10",
        "interval": "1m",
        "requests_completed": completed,
        "requests_failed": len(failed),
        "errors": failed[:5],
    }
    if job_id:
        update_job(job_id, "completed", result)
    return result


def start_today_options_job(job_id: str | None = None, trading_date: str | None = None) -> str:
    if job_id is None:
        td = trading_date or str(date.today())
        job_id = create_job("dhan_today_options", {"trading_date": td})
    else:
        job = get_job(job_id)
        td = trading_date or (job.get("payload") or {}).get("trading_date") or str(date.today())
    try:
        sync_options_for_date(date.fromisoformat(td), job_id)
    except Exception as error:
        update_job(job_id, "failed", None, str(error))
    return job_id


def sync_today_options_next_expiry(job_id: str | None = None, expiry_code: int = 2) -> dict:
    return sync_options_for_date(date.today(), job_id, expiry_code)


def sync_dhan_data(date_from: str | None = None, date_to: str | None = None, job_id: str | None = None) -> dict:
    adapter = DhanAdapter()
    if not adapter.authenticate():
        raise DhanApiError("Dhan credentials not configured")

    start = date.fromisoformat(date_from) if date_from else PILOT_FROM
    end = date.fromisoformat(date_to) if date_to else PILOT_TO
    batch_id = str(uuid.uuid4())
    conn = get_connection()
    total_imported = 0
    try:
        current = start
        while current <= end:
            if _job_cancelled(job_id):
                update_job(job_id, "cancelled", {"cursor": str(current)})
                return {"cancelled": True, "imported": total_imported}

            chunk_end = min(current + timedelta(days=INTRADAY_CHUNK_DAYS - 1), end)
            key = _manifest_key("nifty", f"{current}_{chunk_end}")
            if not _manifest_done(conn, key):
                candles = adapter.get_intraday_candles(
                    from_date=current.isoformat(),
                    to_date=(chunk_end + timedelta(days=1)).isoformat(),
                    interval="1",
                )
                raw = _save_raw(f"nifty_{current}_{chunk_end}", candles)
                checksum = hashlib.md5(raw.read_bytes()).hexdigest()
                count = _import_nifty_chunk(conn, batch_id, current, candles)
                total_imported += count
                _manifest_mark(conn, key, "completed", {"rows": count}, checksum)
            if job_id:
                update_job(job_id, "running", {"phase": "nifty", "imported": total_imported, "cursor": str(current)})
            current = chunk_end + timedelta(days=1)
            time.sleep(0.25)

        current = start
        while current <= end:
            if _job_cancelled(job_id):
                return {"cancelled": True, "imported": total_imported}
            chunk_end = min(current + timedelta(days=EXPIRED_CHUNK_DAYS - 1), end)
            for strike_label in STRIKE_OFFSETS:
                for option_type in OPTION_TYPES:
                    key = _manifest_key("rolling", f"{current}_{chunk_end}_{strike_label}_{option_type}")
                    if _manifest_done(conn, key):
                        continue
                    try:
                        payload = adapter.get_rolling_expired_options(
                            from_date=current.isoformat(),
                            to_date=(chunk_end + timedelta(days=1)).isoformat(),
                            strike=strike_label,
                            drv_option_type=option_type,
                        )
                        _save_raw(key.replace(":", "_"), payload if isinstance(payload, dict) else {"data": payload})
                        count = _import_rolling_options(
                            conn,
                            batch_id,
                            payload if isinstance(payload, dict) else {},
                            option_type,
                            relative_strike=strike_label,
                        )
                        total_imported += count
                        _manifest_mark(conn, key, "completed", {"rows": count, "strike": strike_label}, hashlib.md5(str(payload).encode()).hexdigest())
                    except DhanApiError:
                        _manifest_mark(conn, key, "failed", {"strike": strike_label}, "")
                    time.sleep(0.2)
            if job_id:
                update_job(job_id, "running", {"phase": "options", "imported": total_imported, "cursor": str(current)})
            current = chunk_end + timedelta(days=1)

        vix_key = _manifest_key("vix", f"{start}_{end}")
        if not _manifest_done(conn, vix_key):
            vix = adapter.get_vix_intraday(start.isoformat(), (end + timedelta(days=1)).isoformat())
            total_imported += _import_vix_chunk(conn, batch_id, vix)
            _manifest_mark(conn, vix_key, "completed", {"rows": len(vix.get("close") or [])}, hashlib.md5(str(vix).encode()).hexdigest())

        conn.execute(
            "INSERT INTO import_batches (id, dataset_type, source, created_at, row_count, status) VALUES (?, 'dhan_full', 'dhan', ?, ?, 'completed')",
            [batch_id, datetime.utcnow(), total_imported],
        )
    finally:
        conn.close()

    result = {"batch_id": batch_id, "imported": total_imported, "date_from": str(start), "date_to": str(end)}
    if job_id:
        update_job(job_id, "completed", result)
    return result


def parse_dhan_json_filename(filename: str) -> dict[str, Any] | None:
    stem = Path(filename).stem
    parts = stem.split("_")
    if len(parts) < 2:
        return None
    prefix = parts[0]
    if prefix in ("today", "options") and len(parts) >= 5:
        return {
            "kind": "options",
            "prefix": prefix,
            "trading_date": parts[1],
            "expiry_date": parts[2],
            "relative_strike": parts[3],
            "option_type": parts[4],
        }
    if prefix == "rolling" and len(parts) >= 5:
        return {
            "kind": "options",
            "prefix": prefix,
            "date_from": parts[1],
            "date_to": parts[2],
            "relative_strike": parts[3],
            "option_type": parts[4],
        }
    if prefix == "nifty":
        if len(parts) == 2:
            return {"kind": "nifty", "date_from": parts[1], "date_to": parts[1]}
        if len(parts) >= 3:
            return {"kind": "nifty", "date_from": parts[1], "date_to": parts[2]}
    if prefix == "vix" and len(parts) >= 2:
        return {"kind": "vix", "date": parts[1]}
    return None


def _json_matches_trading_date(meta: dict[str, Any], trading_date: str | None) -> bool:
    if not trading_date:
        return True
    if meta["kind"] == "options":
        if meta["prefix"] in ("today", "options"):
            return meta["trading_date"] == trading_date
        if meta["prefix"] == "rolling":
            return meta["date_from"] <= trading_date <= meta["date_to"]
    if meta["kind"] == "nifty":
        return meta["date_from"] <= trading_date <= meta["date_to"]
    if meta["kind"] == "vix":
        return meta["date"] == trading_date
    return False


def import_dhan_json_bytes(content: bytes, filename: str, batch_id: str, conn) -> dict:
    meta = parse_dhan_json_filename(filename)
    if not meta:
        raise ValueError(f"Unrecognized Dhan JSON filename: {filename}")
    payload = json.loads(content)
    if not isinstance(payload, dict):
        payload = {"data": payload}
    if meta["kind"] == "options":
        expiry = date.fromisoformat(meta["expiry_date"]) if meta.get("expiry_date") else None
        rows = _import_rolling_options(
            conn,
            batch_id,
            payload,
            meta["option_type"],
            expiry_date=expiry,
            relative_strike=meta.get("relative_strike"),
        )
        return {"filename": filename, "kind": "options", "rows_imported": rows}
    if meta["kind"] == "nifty":
        rows = _import_nifty_chunk(conn, batch_id, date.fromisoformat(meta["date_from"]), payload)
        return {"filename": filename, "kind": "nifty", "rows_imported": rows}
    if meta["kind"] == "vix":
        rows = _import_vix_chunk(conn, batch_id, payload)
        return {"filename": filename, "kind": "vix", "rows_imported": rows}
    raise ValueError(f"Unsupported JSON kind for {filename}")


def import_dhan_json_bulk(files: list[tuple[str, bytes]], trading_date: str | None = None) -> dict:
    batch_id = str(uuid.uuid4())
    conn = get_connection()
    total_rows = 0
    imported_files = 0
    errors: list[dict] = []
    by_kind: dict[str, int] = {"options": 0, "nifty": 0, "vix": 0}
    try:
        for filename, content in files:
            meta = parse_dhan_json_filename(filename)
            if not meta:
                errors.append({"filename": filename, "error": "Unrecognized Dhan JSON filename"})
                continue
            if trading_date and not _json_matches_trading_date(meta, trading_date):
                continue
            try:
                result = import_dhan_json_bytes(content, filename, batch_id, conn)
                total_rows += result.get("rows_imported", 0)
                imported_files += 1
                by_kind[result["kind"]] = by_kind.get(result["kind"], 0) + 1
            except Exception as error:
                errors.append({"filename": filename, "error": str(error)})
        if imported_files:
            conn.execute(
                """
                INSERT INTO import_batches (id, dataset_type, source, created_at, row_count, status)
                VALUES (?, 'dhan_json', 'dhan', ?, ?, ?)
                """,
                [batch_id, datetime.utcnow(), total_rows, "completed" if not errors else "partial"],
            )
    finally:
        conn.close()
    return {
        "batch_id": batch_id,
        "files_received": len(files),
        "files_imported": imported_files,
        "rows_imported": total_rows,
        "options_files": by_kind.get("options", 0),
        "nifty_files": by_kind.get("nifty", 0),
        "vix_files": by_kind.get("vix", 0),
        "errors": errors[:20],
        "error_count": len(errors),
        "trading_date": trading_date,
    }


def list_dhan_json_inventory() -> dict:
    root = ensure_data_dirs() / "raw" / "dhan"
    dates: dict[str, dict[str, int]] = {}
    if root.exists():
        for path in sorted(root.glob("*.json")):
            meta = parse_dhan_json_filename(path.name)
            if not meta:
                continue
            if meta["kind"] == "options" and meta["prefix"] in ("today", "options"):
                day = meta["trading_date"]
                bucket = dates.setdefault(day, {"option_files": 0, "nifty_files": 0, "vix_files": 0})
                bucket["option_files"] += 1
            elif meta["kind"] == "nifty":
                day = meta["date_from"]
                bucket = dates.setdefault(day, {"option_files": 0, "nifty_files": 0, "vix_files": 0})
                bucket["nifty_files"] += 1
            elif meta["kind"] == "vix":
                day = meta["date"]
                bucket = dates.setdefault(day, {"option_files": 0, "nifty_files": 0, "vix_files": 0})
                bucket["vix_files"] += 1
    ready = [
        {"date": day, **counts, "total_files": counts["option_files"] + counts["nifty_files"] + counts["vix_files"]}
        for day, counts in sorted(dates.items(), reverse=True)
    ]
    return {"raw_path": str(root), "dates": ready, "total_json_files": sum(item["total_files"] for item in ready)}


def import_dhan_json_from_disk(trading_date: str | None = None) -> dict:
    root = ensure_data_dirs() / "raw" / "dhan"
    if not root.exists():
        return import_dhan_json_bulk([], trading_date)
    payloads: list[tuple[str, bytes]] = []
    for path in sorted(root.glob("*.json")):
        meta = parse_dhan_json_filename(path.name)
        if not meta:
            continue
        if trading_date and not _json_matches_trading_date(meta, trading_date):
            continue
        payloads.append((path.name, path.read_bytes()))
    return import_dhan_json_bulk(payloads, trading_date)


def start_dhan_sync_job(date_from: str | None = None, date_to: str | None = None, job_id: str | None = None) -> str:
    if job_id is None:
        job_id = create_job("dhan_sync", {"date_from": date_from, "date_to": date_to})
    try:
        sync_dhan_data(date_from, date_to, job_id)
    except Exception as error:
        update_job(job_id, "failed", None, str(error))
    return job_id
