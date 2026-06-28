"""Unified data interface: per-day spot/options cache, merge, load."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .merge import merge_spot_and_options
from .providers import helm_api, yahoo_api
from .resample import resample_options, resample_spot
from . import store

DATA_DIR = Path(__file__).resolve().parent
CACHE_DIR = DATA_DIR / "data_cache"
META_DIR = DATA_DIR / "cache_meta"

COLUMNS = [
    "timestamp", "symbol", "spot_open", "spot_high", "spot_low", "spot_close",
    "strike", "opt_type", "open", "high", "low", "close", "oi", "oi_chg", "volume", "iv",
]


def _ensure_legacy_dirs() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)


def cache_key(symbol: str, start: str, end: str, interval: str, strikes: int = 10) -> str:
    return f"{symbol.upper()}_{interval}_{start}_{end}_atm{strikes}"


def _normalize_merged(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    out = df.copy()
    if "opt_type" in out.columns:
        out["opt_type"] = out["opt_type"].replace({"CALL": "CE", "PUT": "PE"})
    for col in COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    return out[COLUMNS].sort_values(["timestamp", "strike", "opt_type"]).reset_index(drop=True)


def download_spot_day(day: str, interval: str, force: bool = False) -> dict:
    """Download or skip Yahoo/Dhan spot for one date. No synthetic fallback."""
    if not force and store.spot_cache_valid(day, interval):
        df = store.load_spot_day(day, interval)
        meta = store.spot_meta(day) or {}
        return {
            "date": day,
            "skipped": True,
            "rows": len(df),
            "source": meta.get("source", "yahoo"),
        }

    source_holder: list[str] = []
    raw = yahoo_api.fetch_spot_day(day, source_out=source_holder)
    if interval != "1min":
        raw = resample_spot(raw, interval)
    source = source_holder[0] if source_holder else "yahoo"
    meta = store.save_spot(day, interval, raw, source=source)
    meta["skipped"] = False
    return meta


def download_options_day(day: str, interval: str, strikes: int, force: bool = False) -> dict:
    """Download or skip Dhan options for one date. No synthetic fallback."""
    if not force and store.options_cache_valid(day, interval, strikes):
        df = store.load_options_day(day, interval, strikes)
        return {"date": day, "skipped": True, "rows": len(df), "source": "dhan"}

    raw = helm_api.fetch_options_day(day, strikes)
    if interval != "1min":
        raw = resample_options(raw, interval)
    meta = store.save_options(day, interval, strikes, raw, source="dhan")
    meta["skipped"] = False
    return meta


def download_day(day: str, interval: str, strikes: int, force: bool = False) -> dict:
    """Download spot + options for a single trading day."""
    spot_result: dict | None = None
    options_result: dict | None = None
    spot_error: str | None = None
    options_error: str | None = None

    try:
        spot_result = download_spot_day(day, interval, force=force)
    except Exception as exc:
        spot_error = str(exc)

    try:
        options_result = download_options_day(day, interval, strikes, force=force)
    except Exception as exc:
        options_error = str(exc)

    return {
        "date": day,
        "spot": spot_result,
        "options": options_result,
        "spot_error": spot_error,
        "options_error": options_error,
        "ok": spot_error is None and options_error is None,
    }


def download(
    symbol: str = "NIFTY",
    start: str | None = None,
    end: str | None = None,
    interval: str = "5min",
    strikes_around_atm: int = 10,
    force: bool = False,
) -> dict:
    """Download missing spot (Yahoo/Dhan) + options (Dhan) per trading day."""
    if not start or not end:
        raise ValueError("start and end dates are required")

    days = store.trading_days(start, end)
    spot_results: list[dict] = []
    options_results: list[dict] = []
    errors: list[str] = []

    for day in days:
        result = download_day(day, interval, strikes_around_atm, force=force)
        if result.get("spot"):
            spot_results.append(result["spot"])
        if result.get("spot_error"):
            errors.append(f"Spot {day}: {result['spot_error']}")
        if result.get("options"):
            options_results.append(result["options"])
        if result.get("options_error"):
            errors.append(f"Options {day}: {result['options_error']}")

    if errors:
        raise ValueError("Download failed:\n" + "\n".join(errors))

    key = cache_key(symbol, start, end, interval, strikes_around_atm)
    merged = load(symbol, start, end, interval, strikes_around_atm)

    spot_skipped = sum(1 for r in spot_results if r.get("skipped"))
    opt_skipped = sum(1 for r in options_results if r.get("skipped"))

    meta = {
        "cache_key": key,
        "symbol": symbol.upper(),
        "start": start,
        "end": end,
        "interval": interval,
        "strikes_around_atm": strikes_around_atm,
        "sources": {"spot_iv": "yahoo", "options": "dhan"},
        "fallback": False,
        "days": len(days),
        "spot_skipped": spot_skipped,
        "options_skipped": opt_skipped,
        "rows": len(merged),
        "downloaded_at": datetime.utcnow().isoformat() + "Z",
    }
    _ensure_legacy_dirs()
    merged.to_parquet(CACHE_DIR / f"{key}.parquet", index=False)
    (META_DIR / f"{key}.json").write_text(json.dumps(meta, indent=2, default=str))
    return meta


def load(
    symbol: str = "NIFTY",
    start: str | None = None,
    end: str | None = None,
    interval: str = "5min",
    strikes_around_atm: int = 10,
    dates: list[str] | None = None,
) -> pd.DataFrame:
    """Merge cached spot + options for the date range (or explicit date list)."""
    if not start or not end:
        raise ValueError("start and end dates are required")

    day_list = dates if dates else store.trading_days(start, end)
    spot_frames: list[pd.DataFrame] = []
    opt_frames: list[pd.DataFrame] = []
    missing: list[str] = []

    for day in day_list:
        try:
            spot_frames.append(store.load_spot_day(day, interval))
            opt_frames.append(store.load_options_day(day, interval, strikes_around_atm))
        except FileNotFoundError:
            missing.append(day)

    if missing:
        raise FileNotFoundError(
            f"Missing cached data for {len(missing)} day(s): {', '.join(missing[:5])}"
            + ("…" if len(missing) > 5 else "")
            + ". Download or upload spot + options first."
        )

    spot_df = pd.concat(spot_frames, ignore_index=True) if spot_frames else pd.DataFrame()
    opt_df = pd.concat(opt_frames, ignore_index=True) if opt_frames else pd.DataFrame()
    return _normalize_merged(merge_spot_and_options(spot_df, opt_df))


def list_cached() -> list[dict]:
    _ensure_legacy_dirs()
    items: list[dict] = []
    for meta_file in sorted(META_DIR.glob("*.json")):
        try:
            items.append(json.loads(meta_file.read_text()))
        except json.JSONDecodeError:
            continue
    items.sort(key=lambda x: x.get("downloaded_at", ""), reverse=True)
    return items


def list_inventory() -> dict:
    return {"spot": store.list_spot(), "options": store.list_options()}


def upload(kind: str, day: str, interval: str, strikes: int, content: bytes, filename: str) -> dict:
    return store.ingest_upload(day, interval, strikes, kind, content, filename)


def upload_dhan_json(
    file_items: list[tuple[str, bytes]],
    interval: str,
    strikes: int,
    default_day: str = "",
) -> dict:
    from . import dhan_json

    return dhan_json.ingest_dhan_json_files(file_items, interval, strikes, default_day=default_day)
