from __future__ import annotations

import json
import uuid
from datetime import date, datetime

from .db import get_connection


def save_signal(run_id: str, trading_date: date, payload: dict) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_signals (id, run_id, trading_date, payload) VALUES (?, ?, ?, ?)",
            [payload.get("id") or str(uuid.uuid4()), run_id, trading_date, json.dumps(payload, default=str)],
        )
    finally:
        conn.close()


def save_trade(run_id: str, trading_date: date, payload: dict) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_trades (id, run_id, trading_date, payload) VALUES (?, ?, ?, ?)",
            [payload.get("id") or str(uuid.uuid4()), run_id, trading_date, json.dumps(payload, default=str)],
        )
    finally:
        conn.close()


def save_equity_point(run_id: str, timestamp: datetime, equity: float, drawdown: float) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_equity (run_id, timestamp_ist, equity, drawdown) VALUES (?, ?, ?, ?)",
            [run_id, timestamp, equity, drawdown],
        )
    finally:
        conn.close()


def list_equity(run_id: str) -> list[dict]:
    conn = get_connection(read_only=True)
    try:
        rows = conn.execute(
            "SELECT timestamp_ist, equity, drawdown FROM backtest_equity WHERE run_id=? ORDER BY timestamp_ist",
            [run_id],
        ).fetchall()
        return [{"timestamp": str(r[0]), "equity": r[1], "drawdown": r[2]} for r in rows]
    finally:
        conn.close()
