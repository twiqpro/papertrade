from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..dhan_client import DhanApiError
from .dhan_downloader import sync_dhan_nifty_vix_for_date
from .yfinance_downloader import import_yahoo_nifty_vix_for_date

YAHOO_MAX_AGE_DAYS = 7


def import_spot_vix_for_date(trading_date: str) -> dict[str, Any]:
    day = date.fromisoformat(trading_date)
    if day > date.today():
        raise ValueError("trading_date cannot be in the future")

    age_days = (date.today() - day).days
    if age_days <= YAHOO_MAX_AGE_DAYS:
        result = import_yahoo_nifty_vix_for_date(day)
        if int(result.get("nifty_rows") or 0) > 0 or int(result.get("vix_rows") or 0) > 0:
            result["source"] = "yahoo"
            result["trading_date"] = trading_date
            return result

    try:
        result = sync_dhan_nifty_vix_for_date(day)
        result["source"] = "dhan"
        result["trading_date"] = trading_date
        return result
    except DhanApiError as error:
        if age_days <= YAHOO_MAX_AGE_DAYS:
            raise
        raise DhanApiError(
            f"Yahoo returned no data and Dhan failed for {trading_date}: {error}"
        ) from error
