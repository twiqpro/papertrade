from __future__ import annotations

import json
import uuid
from datetime import datetime

from .db import get_connection

CANONICAL_FIELDS = {
    "nifty_candles": ["timestamp", "open", "high", "low", "close", "volume"],
    "option_bars": [
        "timestamp",
        "expiry",
        "strike",
        "side",
        "open",
        "high",
        "low",
        "close",
        "open_interest",
        "bid",
        "ask",
        "implied_volatility",
        "delta",
        "gamma",
    ],
    "india_vix": ["timestamp", "open", "high", "low", "close"],
}


def suggest_mapping(headers: list[str], dataset_type: str) -> dict[str, str]:
    canonical = CANONICAL_FIELDS.get(dataset_type, [])
    mapping: dict[str, str] = {}
    lowered = {h.lower().strip(): h for h in headers}
    aliases = {
        "timestamp": ["timestamp", "time", "datetime", "date_time"],
        "open": ["open", "o"],
        "high": ["high", "h"],
        "low": ["low", "l"],
        "close": ["close", "c", "ltp"],
        "volume": ["volume", "vol"],
        "expiry": ["expiry", "expiry_date", "exp"],
        "strike": ["strike", "strike_price"],
        "side": ["side", "option_side", "type", "call_put"],
        "open_interest": ["open_interest", "oi"],
        "bid": ["bid", "top_bid"],
        "ask": ["ask", "top_ask"],
        "implied_volatility": ["implied_volatility", "iv"],
        "delta": ["delta"],
        "gamma": ["gamma"],
    }
    if dataset_type == "option_bars":
        has_wide = any(k.startswith("ce_") for k in lowered) and any(k.startswith("pe_") for k in lowered)
        if has_wide:
            mapping["format"] = "wide"
            for field in ("timestamp", "expiry", "strike"):
                for alias in aliases.get(field, [field]):
                    if alias in lowered:
                        mapping[field] = lowered[alias]
                        break
            return mapping
    for field in canonical:
        for alias in aliases.get(field, [field]):
            if alias in lowered:
                mapping[field] = lowered[alias]
                break
    return mapping


def save_mapping_profile(name: str, dataset_type: str, mapping: dict) -> str:
    profile_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO mapping_profiles (id, name, dataset_type, mapping, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [profile_id, name, dataset_type, json.dumps(mapping), datetime.utcnow()],
        )
    finally:
        conn.close()
    return profile_id


def list_mapping_profiles(dataset_type: str | None = None) -> list[dict]:
    conn = get_connection(read_only=True)
    try:
        if dataset_type:
            rows = conn.execute(
                "SELECT id, name, dataset_type, mapping, created_at FROM mapping_profiles WHERE dataset_type=? ORDER BY created_at DESC",
                [dataset_type],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, dataset_type, mapping, created_at FROM mapping_profiles ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "dataset_type": r[2],
                "mapping": json.loads(r[3]),
                "created_at": str(r[4]),
            }
            for r in rows
        ]
    finally:
        conn.close()
