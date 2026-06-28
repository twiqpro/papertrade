"""Subprocess runner for user strategy code."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_SEC = 60


def run_strategy(
    code: str,
    symbol: str,
    start: str,
    end: str,
    interval: str,
    strikes_around_atm: int = 10,
    dates: list[str] | None = None,
) -> dict:
    """Execute user strategy in isolated subprocess; return backtest results."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(code)
        code_path = fh.name

    args = {
        "code_path": code_path,
        "symbol": symbol,
        "start": start,
        "end": end,
        "interval": interval,
        "strikes_around_atm": strikes_around_atm,
        "dates": dates,
    }

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "engine.worker", json.dumps(args)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return {
            "decisions": [],
            "trades": [],
            "summary": _empty_summary(),
            "error": {"message": f"Strategy timed out after {TIMEOUT_SEC}s"},
        }
    finally:
        Path(code_path).unlink(missing_ok=True)

    if proc.returncode != 0:
        try:
            parsed = json.loads(proc.stdout)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        err = proc.stderr.strip() or proc.stdout.strip() or "Unknown subprocess error"
        return {
            "decisions": [],
            "trades": [],
            "summary": _empty_summary(),
            "error": {"message": err, "traceback": proc.stderr},
        }

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "decisions": [],
            "trades": [],
            "summary": _empty_summary(),
            "error": {
                "message": "Worker returned invalid JSON",
                "traceback": proc.stdout[:2000],
            },
        }


def _empty_summary() -> dict:
    return {
        "total_trades": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
        "max_drawdown": 0.0,
        "skip_count": 0,
        "bar_count": 0,
    }
