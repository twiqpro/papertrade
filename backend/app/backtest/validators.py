from __future__ import annotations

from datetime import date

from .db import get_connection
from .reference import is_trading_day


def validate_day(trading_date: date) -> dict:
    warnings: list[str] = []
    if not is_trading_day(trading_date):
        return {"date": str(trading_date), "status": "excluded", "warnings": ["Non-trading day"]}

    conn = get_connection(read_only=True)
    try:
        nifty_count = conn.execute(
            "SELECT COUNT(*) FROM underlying_bars WHERE symbol='NIFTY' AND CAST(timestamp_ist AS DATE)=?",
            [trading_date],
        ).fetchone()[0]
        option_count = conn.execute(
            "SELECT COUNT(*) FROM option_bars WHERE CAST(timestamp_ist AS DATE)=?",
            [trading_date],
        ).fetchone()[0]
        atm_rows = conn.execute(
            """
            SELECT COUNT(DISTINCT strike) FROM option_bars
            WHERE CAST(timestamp_ist AS DATE)=?
            """,
            [trading_date],
        ).fetchone()[0]
    finally:
        conn.close()

    if nifty_count < 30:
        warnings.append(f"Low NIFTY bar count: {nifty_count}")
    if option_count < 50:
        warnings.append(f"Low option bar count: {option_count}")
    if atm_rows < 5:
        warnings.append(f"Limited strike coverage: {atm_rows} strikes")

    if nifty_count == 0 or option_count == 0:
        status = "excluded"
    elif warnings:
        status = "valid_with_warnings"
    else:
        status = "valid"

    return {"date": str(trading_date), "status": status, "warnings": warnings, "nifty_bars": nifty_count, "option_bars": option_count}


def quality_report(date_from: str, date_to: str) -> dict:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    days = []
    current = start
    while current <= end:
        days.append(validate_day(current))
        current = date.fromordinal(current.toordinal() + 1)
    return {"days": days, "valid": sum(1 for d in days if d["status"] == "valid"), "excluded": sum(1 for d in days if d["status"] == "excluded")}
