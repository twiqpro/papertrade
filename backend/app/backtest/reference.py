from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# NSE holidays simplified — extend via CSV import
NSE_HOLIDAYS: set[date] = set()

DEFAULT_LOT_SIZE = 65
LOT_SCHEDULE: list[tuple[date, int]] = [
    (date(2000, 1, 1), 65),
]


def lot_size_on(trading_date: date, conn=None) -> int:
    if conn is not None:
        row = conn.execute(
            "SELECT lot_size FROM lot_size_schedule WHERE effective_from <= ? ORDER BY effective_from DESC LIMIT 1",
            [trading_date],
        ).fetchone()
        if row:
            return int(row[0])
    for effective_from, size in reversed(LOT_SCHEDULE):
        if trading_date >= effective_from:
            return size
    return DEFAULT_LOT_SIZE


def is_trading_day(trading_date: date) -> bool:
    if trading_date.weekday() >= 5:
        return False
    if trading_date in NSE_HOLIDAYS:
        return False
    return True


def market_open(trading_date: date) -> datetime:
    return datetime(trading_date.year, trading_date.month, trading_date.day, 9, 15, tzinfo=IST)


def market_close(trading_date: date) -> datetime:
    return datetime(trading_date.year, trading_date.month, trading_date.day, 15, 30, tzinfo=IST)
