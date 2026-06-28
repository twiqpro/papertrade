"""FastAPI app for the Options Backtesting Platform."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from data import feed
from data import supabase_cache as cloud_cache
from engine.json_util import json_safe
from engine.runner import run_strategy

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
PORTAL_FRONTEND = ROOT.parent / "frontend"
EXAMPLE = ROOT / "strategies" / "ema_atm_high_win.py"
ROOT_PATH = os.environ.get("ROOT_PATH", "").rstrip("/")

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT.parent / "backend" / ".env")
except ImportError:
    pass


app = FastAPI(
    title="Options Backtester",
    version="1.2.0",
    root_path=ROOT_PATH,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")
if PORTAL_FRONTEND.is_dir():
    app.mount("/portal-assets", StaticFiles(directory=str(PORTAL_FRONTEND)), name="portal-assets")


class DownloadRequest(BaseModel):
    start: str
    end: str
    interval: str = "5min"
    strikes_around_atm: int = Field(default=10, ge=1, le=18)
    force: bool = False


class RunRequest(BaseModel):
    code: str
    symbol: str = "NIFTY"
    start: str
    end: str
    interval: str = "5min"
    strikes_around_atm: int = Field(default=10, ge=1, le=18)
    dates: Optional[list[str]] = None


def _index_html() -> str:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    base = ROOT_PATH or ""
    html = html.replace(
        '<meta name="backtester-base" content="" />',
        f'<meta name="backtester-base" content="{base}" />',
        1,
    )
    if base:
        html = html.replace("<head>", f'<head>\n  <base href="{base}/" />', 1)
    return html


@app.get("/")
def index():
    return HTMLResponse(_index_html())


@app.get("/api/cached")
def cached():
    return {"items": feed.list_cached()}


@app.get("/api/inventory")
def inventory():
    return feed.list_inventory()


@app.get("/api/cache/cloud")
def cache_cloud():
    return {
        "enabled": cloud_cache.enabled(),
        "bucket": cloud_cache.BUCKET if cloud_cache.enabled() else None,
    }


@app.post("/api/download")
def download(req: DownloadRequest):
    try:
        meta = feed.download(
            symbol="NIFTY",
            start=req.start,
            end=req.end,
            interval=req.interval,
            strikes_around_atm=req.strikes_around_atm,
            force=req.force,
        )
        return {
            "ok": True,
            "cache_key": meta["cache_key"],
            "rows": meta["rows"],
            "sources": meta["sources"],
            "fallback": meta.get("fallback", False),
            "spot_skipped": meta.get("spot_skipped", 0),
            "options_skipped": meta.get("options_skipped", 0),
            "days": meta.get("days", 0),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/upload")
async def upload(
    kind: Literal["spot", "options"] = Form(...),
    date: str = Form(""),
    interval: str = Form("5min"),
    strikes_around_atm: int = Form(10),
    file: UploadFile = File(...),
):
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        meta = feed.upload(kind, date, interval, strikes_around_atm, content, file.filename or "data.csv")
        return {"ok": True, **meta}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/run")
def run(req: RunRequest):
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="Strategy code is empty")
    if req.dates is not None and len(req.dates) == 0:
        raise HTTPException(status_code=400, detail="No dates selected for backtest")
    try:
        feed.load(
            req.symbol,
            req.start,
            req.end,
            req.interval,
            req.strikes_around_atm,
            dates=req.dates,
        )
        result = run_strategy(
            code=req.code,
            symbol=req.symbol,
            start=req.start,
            end=req.end,
            interval=req.interval,
            strikes_around_atm=req.strikes_around_atm,
            dates=req.dates,
        )
        return json_safe(result)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/example")
def example():
    if not EXAMPLE.exists():
        raise HTTPException(status_code=404, detail="Example strategy not found")
    return {"code": EXAMPLE.read_text(encoding="utf-8")}
