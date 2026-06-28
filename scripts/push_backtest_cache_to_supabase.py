#!/usr/bin/env python3
"""Push local backtester spot/options parquet caches to Supabase Storage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backtester"))

from dotenv import load_dotenv

load_dotenv(ROOT / "backend" / ".env")

from data import store  # noqa: E402
from data import supabase_cache as cloud  # noqa: E402


def push_all() -> int:
    if not cloud.enabled():
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in backend/.env", file=sys.stderr)
        return 1

    cloud.ensure_bucket()
    pushed = 0

    for day_dir in sorted((store.SPOT_DIR).iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        meta_path = day_dir / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
        for pq in day_dir.glob("*.parquet"):
            interval = pq.stem
            if not meta:
                meta = {"type": "spot", "date": day, "interval": interval, "source": "upload", "rows": 0}
            cloud.push_spot(day, interval, meta, pq)
            print(f"spot  {day} {interval}")
            pushed += 1

    for day_dir in sorted((store.OPTIONS_DIR).iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        meta_path = day_dir / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
        for pq in day_dir.glob("*.parquet"):
            stem = pq.stem
            interval = stem.split("_")[0] if "_" in stem else stem
            strikes = int(stem.split("atm")[-1]) if "atm" in stem else 10
            if not meta:
                meta = {
                    "type": "options",
                    "date": day,
                    "interval": interval,
                    "strikes_around_atm": strikes,
                    "source": "upload",
                    "rows": 0,
                }
            cloud.push_options(day, interval, strikes, meta, pq)
            print(f"options {day} {interval} atm{strikes}")
            pushed += 1

    print(f"Done — {pushed} file(s) uploaded to bucket {cloud.BUCKET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(push_all())
