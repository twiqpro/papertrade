from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from .db import get_connection


def create_job(job_type: str, payload: dict) -> str:
    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO backtest_jobs (id, job_type, status, payload, progress, created_at, updated_at)
            VALUES (?, ?, 'queued', ?, '{}', ?, ?)
            """,
            [job_id, job_type, json.dumps(payload), now, now],
        )
    finally:
        conn.close()
    return job_id


def update_job(job_id: str, status: str, progress: dict | None = None, error: str | None = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE backtest_jobs SET status=?, progress=?, error_message=?, updated_at=? WHERE id=?",
            [status, json.dumps(progress or {}), error, datetime.utcnow(), job_id],
        )
    finally:
        conn.close()


def get_job(job_id: str) -> dict | None:
    conn = get_connection(read_only=True)
    try:
        row = conn.execute("SELECT id, job_type, status, payload, progress, error_message FROM backtest_jobs WHERE id=?", [job_id]).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "job_type": row[1],
            "status": row[2],
            "payload": json.loads(row[3]),
            "progress": json.loads(row[4] or "{}"),
            "error_message": row[5],
        }
    finally:
        conn.close()


def mark_interrupted_jobs() -> int:
    conn = get_connection()
    try:
        result = conn.execute(
            "UPDATE backtest_jobs SET status='interrupted', updated_at=? WHERE status IN ('queued', 'running')",
            [datetime.utcnow()],
        )
        return result.rowcount
    finally:
        conn.close()


def cancel_job(job_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute("SELECT status FROM backtest_jobs WHERE id=?", [job_id]).fetchone()
        if not row or row[0] in ("completed", "failed", "cancelled"):
            return False
        conn.execute(
            "UPDATE backtest_jobs SET status='cancelled', updated_at=? WHERE id=?",
            [datetime.utcnow(), job_id],
        )
        return True
    finally:
        conn.close()
