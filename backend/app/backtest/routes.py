from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Header, UploadFile
from pydantic import BaseModel

from ..config import get_settings
from ..models import BacktestRunRequest, StrategySettings
from ..strategy_module import get_strategy_v1
from ..strategy_module.v1_nifty_atm import hash_settings
from .costs import apply_cost_preset
from .csv_mapper import list_mapping_profiles, save_mapping_profile
from .csv_preview import preview_csv_file
from .db import db_status, ensure_data_dirs
from .dhan_downloader import (
    import_dhan_json_bulk,
    import_dhan_json_from_disk,
    list_dhan_json_inventory,
    start_dhan_sync_job,
    start_today_options_job,
)
from .spot_vix_downloader import import_spot_vix_for_date
from .importer import coverage_report, data_inventory, import_nifty_candles, import_option_bars, import_option_bars_bulk, import_vix_bars, list_imports
from .jobs import cancel_job, create_job, get_job
from .replay import attach_trade_prices_to_signals, get_run, list_run_signals, list_run_trades, list_runs, run_backtest
from .repository import list_equity
from .validators import quality_report

router = APIRouter(prefix="/api", tags=["backtest"])


class CompareRequest(BaseModel):
    run_ids: list[str]


class MappingProfileRequest(BaseModel):
    name: str
    dataset_type: str
    mapping: dict


def verify_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> None:
    expected = get_settings().api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.get("/data/status")
def data_status(_: None = Depends(verify_api_key)) -> dict:
    settings = get_settings()
    dhan_configured = bool(settings.dhan_client_id and settings.dhan_access_token)
    data_root = ensure_data_dirs()
    duck = db_status()
    return {
        "duckdb": duck,
        "dhan_configured": dhan_configured,
        "storage": {
            "data_dir": str(data_root),
            "duckdb": duck.get("path"),
            "raw_yahoo": str(data_root / "raw" / "yahoo"),
            "raw_dhan": str(data_root / "raw" / "dhan"),
        },
    }


@router.post("/data/csv/preview")
async def csv_preview(
    file: UploadFile = File(...),
    dataset_type: str = Form("nifty_candles"),
    _: None = Depends(verify_api_key),
) -> dict:
    content = await file.read()
    return preview_csv_file(content, file.filename or "upload.csv", dataset_type)


@router.post("/data/csv/import")
async def csv_import(
    dataset_type: str = Form(...),
    file: UploadFile = File(...),
    mapping: str = Form("{}"),
    profile_name: Optional[str] = Form(None),
    _: None = Depends(verify_api_key),
) -> dict:
    content = await file.read()
    mapping_dict = json.loads(mapping)
    if profile_name:
        save_mapping_profile(profile_name, dataset_type, mapping_dict)
    if dataset_type == "nifty_candles":
        return import_nifty_candles(content, mapping_dict)
    if dataset_type == "option_bars":
        return import_option_bars(content, mapping_dict)
    if dataset_type == "india_vix":
        return import_vix_bars(content, mapping_dict)
    raise HTTPException(status_code=400, detail=f"Unknown dataset_type: {dataset_type}")


@router.post("/data/csv/import-bulk")
async def csv_import_bulk(
    files: list[UploadFile] = File(...),
    dataset_type: str = Form("option_bars"),
    _: None = Depends(verify_api_key),
) -> dict:
    if dataset_type != "option_bars":
        raise HTTPException(status_code=400, detail="Bulk import supports option_bars only")
    payloads: list[tuple[str, bytes]] = []
    for upload in files:
        payloads.append((upload.filename or "unknown.csv", await upload.read()))
    if not payloads:
        raise HTTPException(status_code=400, detail="No files uploaded")
    return import_option_bars_bulk(payloads)


@router.get("/data/mapping-profiles")
def mapping_profiles(dataset_type: Optional[str] = None, _: None = Depends(verify_api_key)) -> list[dict]:
    return list_mapping_profiles(dataset_type)


@router.post("/data/mapping-profiles")
def create_mapping_profile(payload: MappingProfileRequest, _: None = Depends(verify_api_key)) -> dict:
    profile_id = save_mapping_profile(payload.name, payload.dataset_type, payload.mapping)
    return {"id": profile_id}


@router.get("/data/imports")
def data_imports(_: None = Depends(verify_api_key)) -> list[dict]:
    return list_imports()


@router.get("/data/coverage")
def data_coverage(from_date: str, to_date: str, _: None = Depends(verify_api_key)) -> dict:
    return coverage_report(from_date, to_date)


@router.get("/data/inventory")
def data_inventory_route(_: None = Depends(verify_api_key)) -> dict:
    return data_inventory()


@router.get("/data/quality")
def data_quality(from_date: str, to_date: str, _: None = Depends(verify_api_key)) -> dict:
    return quality_report(from_date, to_date)


class TradingDateRequest(BaseModel):
    trading_date: Optional[str] = None


class YahooSyncRequest(TradingDateRequest):
    period: str = "1d"


@router.post("/data/yahoo/sync")
def yahoo_sync(payload: YahooSyncRequest = YahooSyncRequest(), _: None = Depends(verify_api_key)) -> dict:
    try:
        trading_date = payload.trading_date or date.today().isoformat()
        return import_spot_vix_for_date(trading_date)
    except ImportError as error:
        raise HTTPException(status_code=500, detail="yfinance is not installed on the backend") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Spot/VIX download failed: {error}") from error


@router.post("/data/dhan/today-options")
def dhan_today_options(
    background_tasks: BackgroundTasks,
    payload: TradingDateRequest = TradingDateRequest(),
    _: None = Depends(verify_api_key),
) -> dict:
    trading_date = payload.trading_date or date.today().isoformat()
    job_id = create_job("dhan_today_options", {"trading_date": trading_date})
    background_tasks.add_task(start_today_options_job, job_id, trading_date)
    return {
        "job_id": job_id,
        "status": "queued",
        "trading_date": trading_date,
        "description": f"NIFTY options ATM±10, next expiry, 1m CE+PE for {trading_date}",
    }


@router.get("/data/dhan/json-inventory")
def dhan_json_inventory(_: None = Depends(verify_api_key)) -> dict:
    return list_dhan_json_inventory()


@router.post("/data/dhan/import-json")
def dhan_import_json(payload: TradingDateRequest = TradingDateRequest(), _: None = Depends(verify_api_key)) -> dict:
    trading_date = payload.trading_date
    result = import_dhan_json_from_disk(trading_date)
    if result["files_imported"] == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No Dhan JSON files found for {trading_date or 'any date'} in backend/data/raw/dhan/",
        )
    return result


@router.post("/data/dhan/import-json-bulk")
async def dhan_import_json_bulk(
    files: list[UploadFile] = File(...),
    trading_date: Optional[str] = Form(None),
    _: None = Depends(verify_api_key),
) -> dict:
    payloads: list[tuple[str, bytes]] = []
    for upload in files:
        name = upload.filename or "unknown.json"
        if not name.lower().endswith(".json"):
            continue
        payloads.append((Path(name).name, await upload.read()))
    if not payloads:
        raise HTTPException(status_code=400, detail="No .json files uploaded")
    return import_dhan_json_bulk(payloads, trading_date)


@router.post("/data/dhan/sync")
def dhan_sync(
    background_tasks: BackgroundTasks,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    _: None = Depends(verify_api_key),
) -> dict:
    job_id = create_job("dhan_sync", {"date_from": from_date, "date_to": to_date})
    background_tasks.add_task(start_dhan_sync_job, from_date, to_date, job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/data/jobs/{job_id}")
def data_job(job_id: str, _: None = Depends(verify_api_key)) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/data/jobs/{job_id}/cancel")
def cancel_data_job(job_id: str, _: None = Depends(verify_api_key)) -> dict:
    if not cancel_job(job_id):
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")
    return {"ok": True, "job_id": job_id, "status": "cancelled"}


@router.post("/backtests/runs")
def create_backtest_run(
    payload: BacktestRunRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
) -> dict:
    settings = apply_cost_preset(payload.settings, payload.cost_preset)
    job_id = create_job("backtest_run", {"from_date": payload.from_date, "to_date": payload.to_date})
    background_tasks.add_task(_run_backtest_job, settings, payload.from_date, payload.to_date, job_id)
    return {"job_id": job_id, "status": "queued", "strategy_hash": hash_settings(settings)}


def _run_backtest_job(settings: StrategySettings, from_date: str, to_date: str, job_id: str) -> None:
    if get_job(job_id) and get_job(job_id)["status"] == "cancelled":
        return
    run_backtest(settings, from_date, to_date, job_id)


@router.post("/backtests/jobs/{job_id}/cancel")
def cancel_backtest_job(job_id: str, _: None = Depends(verify_api_key)) -> dict:
    if not cancel_job(job_id):
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")
    return {"ok": True, "job_id": job_id, "status": "cancelled"}


@router.post("/backtests/runs/{run_id}/cancel")
def cancel_backtest_run(run_id: str, _: None = Depends(verify_api_key)) -> dict:
    run = get_run(run_id)
    if not run or run["status"] != "running":
        raise HTTPException(status_code=400, detail="Run is not cancellable")
    return {"ok": True, "run_id": run_id, "message": "Mark run cancelled in UI; job queue uses job cancel endpoint"}


@router.get("/backtests/runs")
def backtest_runs(_: None = Depends(verify_api_key)) -> list[dict]:
    return list_runs()


@router.get("/backtests/runs/{run_id}")
def backtest_run(run_id: str, _: None = Depends(verify_api_key)) -> dict:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/backtests/runs/{run_id}/signals")
def backtest_signals(run_id: str, _: None = Depends(verify_api_key)) -> list[dict]:
    return list_run_signals(run_id)


@router.get("/backtests/runs/{run_id}/trades")
def backtest_trades(run_id: str, _: None = Depends(verify_api_key)) -> list[dict]:
    return list_run_trades(run_id)


@router.get("/backtests/runs/{run_id}/equity")
def backtest_equity(run_id: str, _: None = Depends(verify_api_key)) -> list[dict]:
    return list_equity(run_id)


@router.get("/backtests/runs/{run_id}/replay")
def backtest_replay(run_id: str, trading_date: Optional[str] = None, _: None = Depends(verify_api_key)) -> dict:
    signals = list_run_signals(run_id)
    trades = list_run_trades(run_id)
    if trading_date:
        signals = [s for s in signals if str(s.get("timestamp", "")).startswith(trading_date)]
        trades = [t for t in trades if str(t.get("entry_time", "")).startswith(trading_date)]
    signals = attach_trade_prices_to_signals(signals, trades)
    return {"run_id": run_id, "signals": signals, "trades": trades, "equity": list_equity(run_id)}


@router.post("/backtests/compare")
def backtest_compare(payload: CompareRequest, _: None = Depends(verify_api_key)) -> dict:
    runs = [get_run(run_id) for run_id in payload.run_ids]
    valid = [r for r in runs if r]
    comparison = []
    for run in valid:
        comparison.append(
            {
                "id": run["id"],
                "strategy_hash": run["strategy_hash"],
                "replay_mode": run["replay_mode"],
                "summary": run["summary"],
                "date_from": run["date_from"],
                "date_to": run["date_to"],
            }
        )
    return {"runs": comparison}


@router.get("/backtests/runs/{run_id}/export")
def backtest_export(run_id: str, format: str = "json", _: None = Depends(verify_api_key)):
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["side", "strike", "entry_time", "exit_time", "result", "pnl"])
        writer.writeheader()
        for trade in list_run_trades(run_id):
            writer.writerow({k: trade.get(k) for k in writer.fieldnames})
        return {"format": "csv", "content": output.getvalue(), "strategy_hash": run["strategy_hash"]}
    if format == "html":
        trades = list_run_trades(run_id)
        rows = "".join(
            f"<tr><td>{t.get('side','')}</td><td>{t.get('strike','')}</td><td>{t.get('entry_time','')}</td>"
            f"<td>{t.get('exit_time','')}</td><td>{t.get('result','')}</td><td>{t.get('pnl','')}</td></tr>"
            for t in trades
        )
        html = f"""<!doctype html><html><head><title>Backtest {run_id}</title></head><body>
        <h1>Backtest Report</h1>
        <p>Strategy hash: {run['strategy_hash']}</p>
        <p>Net P&L: {run.get('summary',{}).get('net_pnl',0)}</p>
        <table border="1"><tr><th>Side</th><th>Strike</th><th>Entry</th><th>Exit</th><th>Result</th><th>P&L</th></tr>{rows}</table>
        </body></html>"""
        return {"format": "html", "content": html, "strategy_hash": run["strategy_hash"]}
    return {
        "format": "json",
        "run": run,
        "signals": list_run_signals(run_id),
        "trades": list_run_trades(run_id),
        "equity": list_equity(run_id),
        "strategy_hash": run["strategy_hash"],
    }


@router.get("/strategies")
def strategies(_: None = Depends(verify_api_key)) -> list[dict]:
    strategy = get_strategy_v1()
    return [{"strategy_id": strategy.strategy_id, "strategy_version": strategy.strategy_version}]


@router.get("/strategies/{strategy_id}/settings-schema")
def strategy_settings_schema(strategy_id: str, _: None = Depends(verify_api_key)) -> dict:
    if strategy_id != get_strategy_v1().strategy_id:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return StrategySettings.model_json_schema()
