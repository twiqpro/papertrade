from __future__ import annotations

from .csv_mapper import CANONICAL_FIELDS, suggest_mapping
from .importer import detect_csv


def preview_csv_file(content: bytes, filename: str, dataset_type: str) -> dict:
    text = content.decode("utf-8", errors="replace")
    meta = detect_csv(text)
    suggested = suggest_mapping(meta["headers"], dataset_type)
    missing = [field for field in CANONICAL_FIELDS.get(dataset_type, []) if field not in suggested]
    required = {
        "nifty_candles": ["timestamp", "open", "high", "low", "close"],
        "option_bars": ["timestamp", "expiry", "strike", "side", "open", "high", "low", "close"],
        "india_vix": ["timestamp", "close"],
    }
    missing_required = [f for f in required.get(dataset_type, []) if f not in suggested]
    if suggested.get("format") == "wide":
        missing_required = [f for f in missing_required if f != "side"]
    return {
        "filename": filename,
        "dataset_type": dataset_type,
        "delimiter": meta["delimiter"],
        "headers": meta["headers"],
        "sample_rows": meta["sample_rows"],
        "suggested_mapping": suggested,
        "missing_optional": missing,
        "missing_required": missing_required,
        "valid": len(missing_required) == 0,
    }
