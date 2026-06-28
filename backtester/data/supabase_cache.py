"""Optional Supabase Storage sync for backtester spot/options parquet caches."""

from __future__ import annotations

import json
import logging
import os
import re
from io import BytesIO

import httpx

log = logging.getLogger(__name__)

BUCKET = os.getenv("SUPABASE_BACKTEST_BUCKET", "backtest-cache")
TIMEOUT = 120.0
_bucket_ready = False


def enabled() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


def _base() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/")


def _service_key() -> str:
    return os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def _headers(*, content_type: str | None = None, upsert: bool = False) -> dict[str, str]:
    key = _service_key()
    h: dict[str, str] = {"Authorization": f"Bearer {key}", "apikey": key}
    if content_type:
        h["Content-Type"] = content_type
    if upsert:
        h["x-upsert"] = "true"
    return h


def spot_parquet_key(day: str, interval: str) -> str:
    return f"spot/{day}/{interval}.parquet"


def spot_meta_key(day: str) -> str:
    return f"spot/{day}/meta.json"


def options_parquet_key(day: str, interval: str, strikes: int) -> str:
    return f"options/{day}/{interval}_atm{strikes}.parquet"


def options_meta_key(day: str) -> str:
    return f"options/{day}/meta.json"


def ensure_bucket() -> None:
    global _bucket_ready
    if not enabled() or _bucket_ready:
        return
    url = f"{_base()}/storage/v1/bucket"
    try:
        r = httpx.post(
            url,
            json={"id": BUCKET, "public": False, "file_size_limit": 104857600},
            headers=_headers(content_type="application/json"),
            timeout=TIMEOUT,
        )
        if r.status_code not in (200, 201, 409):
            r.raise_for_status()
        _bucket_ready = True
    except Exception as exc:
        log.warning("Supabase bucket setup skipped: %s", exc)


def upload_bytes(key: str, data: bytes) -> None:
    if not enabled():
        return
    ensure_bucket()
    url = f"{_base()}/storage/v1/object/{BUCKET}/{key}"
    r = httpx.post(
        url,
        content=data,
        headers=_headers(content_type="application/octet-stream", upsert=True),
        timeout=TIMEOUT,
    )
    r.raise_for_status()


def upload_json(key: str, obj: dict) -> None:
    upload_bytes(key, json.dumps(obj, indent=2).encode())


def _bytes_look_like_parquet(data: bytes) -> bool:
    return len(data) >= 8 and data[:4] == b"PAR1" and data[-4:] == b"PAR1"


def download_bytes(key: str) -> bytes | None:
    if not enabled():
        return None
    url = f"{_base()}/storage/v1/object/{BUCKET}/{key}"
    r = httpx.get(url, headers=_headers(), timeout=TIMEOUT)
    if r.status_code in (400, 404):
        return None
    r.raise_for_status()
    return r.content


def _list_level(prefix: str) -> list[dict]:
    url = f"{_base()}/storage/v1/object/list/{BUCKET}"
    r = httpx.post(
        url,
        json={"prefix": prefix, "limit": 1000, "offset": 0},
        headers=_headers(content_type="application/json"),
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def _day_folders(kind: str) -> list[str]:
    rows = _list_level(f"{kind}/")
    days: list[str] = []
    for row in rows:
        name = row.get("name", "")
        if not name or name.endswith(".parquet") or name.endswith(".json"):
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            days.append(name)
    return sorted(days)


def list_spot_inventory() -> list[dict]:
    if not enabled():
        return []
    items: list[dict] = []
    for day in _day_folders("spot"):
        raw = download_bytes(spot_meta_key(day))
        if raw:
            try:
                meta = json.loads(raw.decode())
                meta["storage"] = "supabase"
                items.append(meta)
            except json.JSONDecodeError:
                continue
    return items


def list_options_inventory() -> list[dict]:
    if not enabled():
        return []
    items: list[dict] = []
    for day in _day_folders("options"):
        raw = download_bytes(options_meta_key(day))
        if raw:
            try:
                meta = json.loads(raw.decode())
                meta["storage"] = "supabase"
                items.append(meta)
            except json.JSONDecodeError:
                continue
    return items


def push_spot(day: str, interval: str, meta: dict, parquet_path) -> None:
    upload_bytes(spot_parquet_key(day, interval), parquet_path.read_bytes())
    upload_json(spot_meta_key(day), meta)


def push_options(day: str, interval: str, strikes: int, meta: dict, parquet_path) -> None:
    upload_bytes(options_parquet_key(day, interval, strikes), parquet_path.read_bytes())
    upload_json(options_meta_key(day), meta)


def hydrate_spot(day: str, interval: str, spot_path, meta_path) -> bool:
    data = download_bytes(spot_parquet_key(day, interval))
    if data is None or not _bytes_look_like_parquet(data):
        return False
    spot_path.parent.mkdir(parents=True, exist_ok=True)
    spot_path.write_bytes(data)
    meta_raw = download_bytes(spot_meta_key(day))
    if meta_raw:
        meta_path.write_bytes(meta_raw)
    return True


def hydrate_options(day: str, interval: str, strikes: int, options_path, meta_path) -> bool:
    data = download_bytes(options_parquet_key(day, interval, strikes))
    if data is None or not _bytes_look_like_parquet(data):
        return False
    options_path.parent.mkdir(parents=True, exist_ok=True)
    options_path.write_bytes(data)
    meta_raw = download_bytes(options_meta_key(day))
    if meta_raw:
        meta_path.write_bytes(meta_raw)
    return True


def infer_day_from_filename(filename: str) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename or "")
    return m.group(1) if m else None


def infer_day_from_bytes(content: bytes, filename: str) -> str | None:
    import pandas as pd

    try:
        if filename.endswith(".parquet"):
            df = pd.read_parquet(BytesIO(content), columns=["timestamp"])
        elif filename.endswith(".csv"):
            df = pd.read_csv(BytesIO(content), usecols=["timestamp"], nrows=1)
        else:
            return None
        if df.empty:
            return None
        ts = pd.to_datetime(df["timestamp"].iloc[0])
        return ts.date().isoformat()
    except Exception:
        return None
