from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import date as session_date_type
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import database_status, init_db
from .models import DashboardPayload, SessionMode, Signal, StrategySettings, Trade
from .repository import daily_summary, list_signals, list_trades
from .store import store


_dashboard_lock = threading.Lock()


def run_dashboard() -> DashboardPayload:
    with _dashboard_lock:
        return store.dashboard()


async def background_tick_loop() -> None:
    settings = get_settings()
    while True:
        try:
            await asyncio.to_thread(run_dashboard)
        except Exception:
            pass
        await asyncio.sleep(settings.tick_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(background_tick_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)

_cors_kwargs: dict = {
    "allow_credentials": False,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if settings.environment == "development":
    _cors_kwargs["allow_origins"] = settings.cors_origins + ["*"]
else:
    _cors_kwargs["allow_origins"] = settings.cors_origins
    # Allow Vercel production + preview URLs without listing every deploy URL.
    _cors_kwargs["allow_origin_regex"] = r"https://.*\.vercel\.app"

app.add_middleware(CORSMiddleware, **_cors_kwargs)


def verify_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> None:
    expected = get_settings().api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    dhan_configured = bool(settings.dhan_client_id and settings.dhan_access_token)
    dhan_live = False
    if store._dhan_feed is not None and store._dhan_feed.last_good is not None:
        dhan_live = store._dhan_feed.last_good.feed_status == "live"
    db_ok, db_error = database_status()
    return {
        "ok": True,
        "broker": settings.broker,
        "paper_trading_only": settings.paper_trading_only,
        "dhan_configured": dhan_configured,
        "dhan_live": dhan_live,
        "database": db_ok,
        "database_error": db_error,
    }


@app.get("/api/dashboard", response_model=DashboardPayload, dependencies=[Depends(verify_api_key)])
def dashboard() -> DashboardPayload:
    return run_dashboard()


@app.get("/api/settings", response_model=StrategySettings, dependencies=[Depends(verify_api_key)])
def get_strategy_settings() -> StrategySettings:
    return store.settings


@app.post("/api/settings", response_model=StrategySettings, dependencies=[Depends(verify_api_key)])
def update_strategy_settings(payload: StrategySettings) -> StrategySettings:
    return store.set_settings(payload)


@app.post("/api/session/{mode}", dependencies=[Depends(verify_api_key)])
def update_session(mode: SessionMode) -> dict:
    return {"session_mode": store.set_session(mode)}


@app.post("/api/paper/reset", dependencies=[Depends(verify_api_key)])
def reset_paper_day() -> dict:
    store.reset_paper_day()
    return {"ok": True, "message": "Paper session reset for today"}


@app.get("/api/history/signals", response_model=list[Signal], dependencies=[Depends(verify_api_key)])
def history_signals(
    session_date: session_date_type = Query(..., alias="date", description="Session date (YYYY-MM-DD)"),
    limit: int = Query(500, ge=1, le=5000),
) -> list[Signal]:
    db_ok, db_error = database_status()
    if not db_ok:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "History requires a working database connection",
                "error": db_error,
            },
        )
    return list_signals(session_date, limit=limit)


@app.get("/api/history/trades", response_model=list[Trade], dependencies=[Depends(verify_api_key)])
def history_trades(
    session_date: session_date_type = Query(..., alias="date", description="Session date (YYYY-MM-DD)"),
    limit: int = Query(200, ge=1, le=5000),
) -> list[Trade]:
    db_ok, db_error = database_status()
    if not db_ok:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "History requires a working database connection",
                "error": db_error,
            },
        )
    return list_trades(session_date, limit=limit)


@app.get("/api/history/summary", dependencies=[Depends(verify_api_key)])
def history_summary(
    from_date: session_date_type = Query(..., alias="from"),
    to_date: session_date_type = Query(..., alias="to"),
) -> dict:
    db_ok, db_error = database_status()
    if not db_ok:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "History requires a working database connection",
                "error": db_error,
            },
        )
    return {"days": daily_summary(from_date, to_date)}
