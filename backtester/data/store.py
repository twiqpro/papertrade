"""Filesystem store: per-date spot (Yahoo) and options (Dhan) parquet caches."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd

from . import supabase_cache as cloud

DATA_DIR = Path(__file__).resolve().parent
SPOT_DIR = DATA_DIR / "spot"
OPTIONS_DIR = DATA_DIR / "options"

SPOT_COLUMNS = ["timestamp", "symbol", "spot_open", "spot_high", "spot_low", "spot_close", "iv"]
OPTIONS_COLUMNS = [
    "timestamp", "symbol", "strike", "opt_type",
    "open", "high", "low", "close", "oi", "oi_chg", "volume", "iv",
]


def _ensure_dirs() -> None:
    SPOT_DIR.mkdir(parents=True, exist_ok=True)
    OPTIONS_DIR.mkdir(parents=True, exist_ok=True)


def trading_days(start: str | date, end: str | date) -> list[str]:
    s = date.fromisoformat(str(start)[:10])
    e = date.fromisoformat(str(end)[:10])
    out: list[str] = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _spot_path(day: str, interval: str) -> Path:
    return SPOT_DIR / day / f"{interval}.parquet"


def _spot_meta_path(day: str) -> Path:
    return SPOT_DIR / day / "meta.json"


def _options_path(day: str, interval: str, strikes: int) -> Path:
    return OPTIONS_DIR / day / f"{interval}_atm{strikes}.parquet"


def _options_meta_path(day: str) -> Path:
    return OPTIONS_DIR / day / "meta.json"


def spot_meta(day: str) -> dict | None:
    path = _spot_meta_path(day)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def options_meta(day: str) -> dict | None:
    path = _options_meta_path(day)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def spot_cache_valid(day: str, interval: str) -> bool:
    if not spot_exists(day, interval):
        _hydrate_spot_from_cloud(day, interval)
    if not spot_exists(day, interval):
        return False
    meta = spot_meta(day)
    return bool(meta and meta.get("source") in ("yahoo", "dhan", "upload"))


def options_cache_valid(day: str, interval: str, strikes: int) -> bool:
    if not options_exists(day, interval, strikes):
        _hydrate_options_from_cloud(day, interval, strikes)
    if not options_exists(day, interval, strikes):
        return False
    meta = options_meta(day)
    return bool(meta and meta.get("source") in ("dhan", "upload"))


def _parquet_ok(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        size = path.stat().st_size
        if size < 8:
            return False
        with path.open("rb") as fh:
            if fh.read(4) != b"PAR1":
                return False
            fh.seek(-4, 2)
            return fh.read(4) == b"PAR1"
    except OSError:
        return False


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    try:
        df.to_parquet(tmp, index=False)
        pd.read_parquet(tmp)  # verify readable before commit
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_parquet(path: Path, *, kind: str, day: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No {kind} data for {day} ({path.name})")
    if not _parquet_ok(path):
        raise ValueError(
            f"Corrupt {kind} cache for {day} ({path.name}). "
            "Click Force refresh for that range, or re-upload the file."
        )
    try:
        return pd.read_parquet(path)
    except Exception as exc:
        msg = str(exc)
        if "thrift" in msg.lower() or "parquet" in msg.lower():
            raise ValueError(
                f"Corrupt {kind} cache for {day} ({path.name}). "
                "Click Force refresh for that range, or re-upload the file."
            ) from exc
        raise


def spot_exists(day: str, interval: str) -> bool:
    path = _spot_path(day, interval)
    return path.exists() and _parquet_ok(path)


def options_exists(day: str, interval: str, strikes: int) -> bool:
    path = _options_path(day, interval, strikes)
    return path.exists() and _parquet_ok(path)


def _hydrate_spot_from_cloud(day: str, interval: str) -> None:
    if not cloud.enabled():
        return
    try:
        cloud.hydrate_spot(day, interval, _spot_path(day, interval), _spot_meta_path(day))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Cloud spot hydrate failed for %s: %s", day, exc)


def _hydrate_options_from_cloud(day: str, interval: str, strikes: int) -> None:
    if not cloud.enabled():
        return
    try:
        cloud.hydrate_options(
            day, interval, strikes, _options_path(day, interval, strikes), _options_meta_path(day)
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Cloud options hydrate failed for %s: %s", day, exc)


def save_spot(day: str, interval: str, df: pd.DataFrame, source: str = "yahoo") -> dict:
    _ensure_dirs()
    out = _normalize_spot(df)
    _write_parquet(out, _spot_path(day, interval))
    meta = {
        "type": "spot",
        "date": day,
        "interval": interval,
        "source": source,
        "rows": len(out),
        "saved_at": datetime.utcnow().isoformat() + "Z",
    }
    _spot_meta_path(day).write_text(json.dumps(meta, indent=2))
    if cloud.enabled():
        try:
            cloud.push_spot(day, interval, meta, _spot_path(day, interval))
            meta["storage"] = "supabase"
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Supabase spot push failed for %s: %s", day, exc)
    return meta


def save_options(day: str, interval: str, strikes: int, df: pd.DataFrame, source: str = "dhan") -> dict:
    _ensure_dirs()
    out = _normalize_options(df)
    _write_parquet(out, _options_path(day, interval, strikes))
    meta = {
        "type": "options",
        "date": day,
        "interval": interval,
        "strikes_around_atm": strikes,
        "source": source,
        "rows": len(out),
        "saved_at": datetime.utcnow().isoformat() + "Z",
    }
    _options_meta_path(day).write_text(json.dumps(meta, indent=2))
    if cloud.enabled():
        try:
            cloud.push_options(day, interval, strikes, meta, _options_path(day, interval, strikes))
            meta["storage"] = "supabase"
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Supabase options push failed for %s: %s", day, exc)
    return meta


def load_spot_day(day: str, interval: str) -> pd.DataFrame:
    path = _spot_path(day, interval)
    if not path.exists():
        _hydrate_spot_from_cloud(day, interval)
    return _normalize_spot(_read_parquet(path, kind="spot", day=day))


def load_options_day(day: str, interval: str, strikes: int) -> pd.DataFrame:
    path = _options_path(day, interval, strikes)
    if not path.exists():
        _hydrate_options_from_cloud(day, interval, strikes)
    return _normalize_options(_read_parquet(path, kind="options", day=day))


def list_spot() -> list[dict]:
    _ensure_dirs()
    items: list[dict] = []
    for day_dir in sorted(SPOT_DIR.iterdir()):
        if not day_dir.is_dir():
            continue
        meta_file = day_dir / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            interval = meta.get("interval", "1min")
            if not _parquet_ok(_spot_path(day_dir.name, interval)):
                continue
            items.append(meta)
        else:
            for pq in day_dir.glob("*.parquet"):
                if not _parquet_ok(pq):
                    continue
                items.append({
                    "type": "spot",
                    "date": day_dir.name,
                    "interval": pq.stem,
                    "source": "upload",
                    "rows": len(pd.read_parquet(pq)),
                })
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    return _merge_cloud_inventory(items, cloud.list_spot_inventory(), ("date", "interval"))


def list_options() -> list[dict]:
    _ensure_dirs()
    items: list[dict] = []
    for day_dir in sorted(OPTIONS_DIR.iterdir()):
        if not day_dir.is_dir():
            continue
        meta_file = day_dir / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            interval = meta.get("interval", "1min")
            strikes = int(meta.get("strikes_around_atm", 10))
            if not _parquet_ok(_options_path(day_dir.name, interval, strikes)):
                continue
            items.append(meta)
        else:
            for pq in day_dir.glob("*.parquet"):
                if not _parquet_ok(pq):
                    continue
                stem = pq.stem  # e.g. 5min_atm10
                items.append({
                    "type": "options",
                    "date": day_dir.name,
                    "interval": stem.split("_")[0] if "_" in stem else stem,
                    "strikes_around_atm": int(stem.split("atm")[-1]) if "atm" in stem else 10,
                    "source": "upload",
                    "rows": len(pd.read_parquet(pq)),
                })
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    return _merge_cloud_inventory(items, cloud.list_options_inventory(), ("date", "interval", "strikes_around_atm"))


def _merge_cloud_inventory(local: list[dict], remote: list[dict], key_fields: tuple[str, ...]) -> list[dict]:
    if not remote:
        return local
    seen = {tuple(item.get(f) for f in key_fields) for item in local}
    merged = list(local)
    for item in remote:
        key = tuple(item.get(f) for f in key_fields)
        if key in seen:
            continue
        item = dict(item)
        item["remote_only"] = True
        merged.append(item)
    merged.sort(key=lambda x: x.get("date", ""), reverse=True)
    return merged


def _normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=SPOT_COLUMNS)
    out = df.copy()
    renames = {
        "open": "spot_open", "high": "spot_high", "low": "spot_low", "close": "spot_close",
    }
    for old, new in renames.items():
        if old in out.columns and new not in out.columns:
            out = out.rename(columns={old: new})
    if "symbol" not in out.columns:
        out["symbol"] = "NIFTY"
    if "iv" not in out.columns:
        out["iv"] = pd.NA
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    return out[[c for c in SPOT_COLUMNS if c in out.columns]].sort_values("timestamp").reset_index(drop=True)


def _normalize_options(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=OPTIONS_COLUMNS)
    out = df.copy()
    if "opt_type" in out.columns:
        out["opt_type"] = out["opt_type"].replace({"CALL": "CE", "PUT": "PE", "call": "CE", "put": "PE"})
    if "symbol" not in out.columns:
        out["symbol"] = "NIFTY"
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    out = out.sort_values(["timestamp", "strike", "opt_type"]).reset_index(drop=True)
    if "oi_chg" not in out.columns and "oi" in out.columns:
        out["oi_chg"] = out.groupby(["strike", "opt_type"])["oi"].diff().fillna(0).astype(int)
    for col in OPTIONS_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out[OPTIONS_COLUMNS]


def resolve_upload_day(day: str, content: bytes, filename: str) -> str:
    """Pick target trading day from form field, filename, or first timestamp in file."""
    if day and str(day).strip():
        return str(day).strip()[:10]
    inferred = cloud.infer_day_from_filename(filename)
    if inferred:
        return inferred
    inferred = cloud.infer_day_from_bytes(content, filename)
    if inferred:
        return inferred
    raise ValueError(
        "Could not determine date — set Upload date or use a filename like 2026-06-25_spot.parquet"
    )


def ingest_upload(day: str, interval: str, strikes: int, kind: str, content: bytes, filename: str) -> dict:
    """Parse uploaded CSV or parquet and save to the appropriate folder."""
    name = (filename or "data.csv").lower()
    if name.endswith(".parquet"):
        df = pd.read_parquet(BytesIO(content))
    elif name.endswith(".csv"):
        df = pd.read_csv(BytesIO(content))
    else:
        raise ValueError("Upload .csv or .parquet only")

    target_day = resolve_upload_day(day, content, filename)

    if kind == "spot":
        return save_spot(target_day, interval, df, source="upload")
    if kind == "options":
        return save_options(target_day, interval, strikes, df, source="upload")
    raise ValueError(f"Unknown kind: {kind}")
