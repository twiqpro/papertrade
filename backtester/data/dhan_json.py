"""Parse Dhan historical options JSON exports into backtester cache rows."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from . import store


def parse_dhan_json_filename(filename: str) -> dict[str, Any] | None:
    """Parse names like today_2026-06-25_2026-07-01_ATM_CALL.json or rolling_…"""
    stem = Path(filename).name.rsplit("/", 1)[-1]
    stem = Path(stem).stem
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
    return None


def _parse_options_block(block: dict, opt_type: str) -> list[dict]:
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
        })
    return rows


def options_rows_from_json(content: bytes, filename: str) -> tuple[list[dict], dict[str, Any]]:
    meta = parse_dhan_json_filename(filename)
    if not meta or meta.get("kind") != "options":
        raise ValueError(f"Unrecognized Dhan options JSON filename: {filename}")
    payload = json.loads(content.decode() if isinstance(content, (bytes, bytearray)) else content)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {filename}")
    opt_type = meta["option_type"]
    side_key = "ce" if opt_type == "CALL" else "pe"
    block = payload.get(side_key) or payload.get(opt_type.lower()) or payload
    rows = _parse_options_block(block, opt_type)
    if not rows and isinstance(payload.get("data"), dict):
        block = payload["data"].get(side_key) or payload["data"]
        rows = _parse_options_block(block, opt_type)
    return rows, meta


def _infer_day_from_filename(filename: str) -> str | None:
    meta = parse_dhan_json_filename(filename)
    if meta and meta.get("kind") == "options" and meta.get("prefix") in ("today", "options"):
        return meta.get("trading_date")
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename or "")
    return m.group(1) if m else None


def ingest_dhan_json_files(
    file_items: list[tuple[str, bytes]],
    interval: str,
    strikes: int,
    default_day: str = "",
) -> dict:
    """Merge many Dhan JSON files into per-day options parquet caches."""
    by_day: dict[str, list[pd.DataFrame]] = defaultdict(list)
    errors: list[str] = []
    files_seen = 0

    for filename, content in file_items:
        if not (filename.lower().endswith(".json") and content):
            continue
        files_seen += 1
        try:
            rows, meta = options_rows_from_json(content, filename)
            if not rows:
                errors.append(f"{filename}: no option rows")
                continue
            df = pd.DataFrame(rows)
            df["timestamp"] = pd.to_datetime(df["timestamp"])

            if meta["prefix"] in ("today", "options"):
                day = (default_day or meta["trading_date"])[:10]
                by_day[day].append(df)
            elif meta["prefix"] == "rolling":
                if default_day:
                    day = default_day[:10]
                    mask = df["timestamp"].dt.date.astype(str) == day
                    part = df.loc[mask]
                    if part.empty:
                        errors.append(f"{filename}: no rows for {day}")
                        continue
                    by_day[day].append(part)
                else:
                    for day_str, grp in df.groupby(df["timestamp"].dt.date.astype(str)):
                        by_day[day_str].append(grp)
            else:
                errors.append(f"{filename}: unsupported prefix")
        except Exception as exc:
            errors.append(f"{filename}: {exc}")

    if not by_day:
        hint = "Use Dhan JSON names like today_2026-06-25_2026-07-01_ATM_CALL.json"
        msg = "; ".join(errors[:5]) if errors else f"No valid Dhan options JSON found. {hint}"
        raise ValueError(msg)

    saved: list[dict] = []
    total_rows = 0
    for day in sorted(by_day):
        merged = pd.concat(by_day[day], ignore_index=True)
        merged = merged.sort_values(["timestamp", "strike", "opt_type"]).reset_index(drop=True)
        meta = store.save_options(day, interval, strikes, merged, source="dhan")
        saved.append(meta)
        total_rows += len(merged)

    return {
        "ok": True,
        "files": files_seen,
        "days": len(saved),
        "rows": total_rows,
        "dates": [m["date"] for m in saved],
        "errors": errors[:20],
    }


def ingest_dhan_json_file(
    content: bytes,
    filename: str,
    interval: str,
    strikes: int,
    day: str = "",
) -> dict:
    return ingest_dhan_json_files([(filename, content)], interval, strikes, default_day=day)
