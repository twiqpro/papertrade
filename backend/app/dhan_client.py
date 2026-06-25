from __future__ import annotations

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import get_settings


BASE_URL = "https://api.dhan.co/v2"
NIFTY_SECURITY_ID = 13
NIFTY_SEGMENT = "IDX_I"
INDIA_VIX_SECURITY_ID = 26
NSE_FNO = "NSE_FNO"


class DhanApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DhanAdapter:
    """Dhan REST client for market quotes, option chain, and intraday candles."""

    def __init__(self) -> None:
        settings = get_settings()
        self.client_id = settings.dhan_client_id
        self.access_token = settings.dhan_access_token

    def authenticate(self) -> bool:
        return bool(self.client_id and self.access_token)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        if not self.authenticate():
            raise DhanApiError("Dhan credentials are not configured")

        payload = json.dumps(body or {}).encode()
        request = Request(
            f"{BASE_URL}{path}",
            data=payload if method == "POST" else None,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "access-token": self.access_token,
                "client-id": self.client_id,
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read())
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise DhanApiError(f"Dhan API {error.code}: {detail}", status_code=error.code) from error
        except URLError as error:
            raise DhanApiError(f"Dhan API unreachable: {error.reason}") from error

    def get_ltp(self, securities: dict[str, list[int]]) -> dict[str, dict[str, dict[str, float]]]:
        response = self._request("POST", "/marketfeed/ltp", securities)
        return response.get("data", {})

    def get_expiry_list(self, underlying_scrip: int = NIFTY_SECURITY_ID, segment: str = NIFTY_SEGMENT) -> list[str]:
        response = self._request(
            "POST",
            "/optionchain/expirylist",
            {"UnderlyingScrip": underlying_scrip, "UnderlyingSeg": segment},
        )
        return list(response.get("data") or [])

    def get_option_chain(
        self,
        expiry: str,
        underlying_scrip: int = NIFTY_SECURITY_ID,
        segment: str = NIFTY_SEGMENT,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/optionchain",
            {"UnderlyingScrip": underlying_scrip, "UnderlyingSeg": segment, "Expiry": expiry},
        )
        return response.get("data") or {}

    def get_intraday_candles(
        self,
        from_date: str,
        to_date: str,
        interval: str = "1",
        security_id: str = str(NIFTY_SECURITY_ID),
        exchange_segment: str = NIFTY_SEGMENT,
        instrument: str = "INDEX",
    ) -> dict[str, list[Any]]:
        response = self._request(
            "POST",
            "/charts/intraday",
            {
                "securityId": security_id,
                "exchangeSegment": exchange_segment,
                "instrument": instrument,
                "interval": interval,
                "fromDate": from_date,
                "toDate": to_date,
            },
        )
        data = response.get("data", response)
        return data if isinstance(data, dict) else {}

    def get_rolling_expired_options(
        self,
        from_date: str,
        to_date: str,
        strike: str = "ATM",
        drv_option_type: str = "CALL",
        interval: str = "1",
        expiry_code: int = 1,
        expiry_flag: str = "WEEK",
        security_id: str = str(NIFTY_SECURITY_ID),
        exchange_segment: str = NSE_FNO,
        instrument: str = "OPTIDX",
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/charts/rollingoption",
            {
                "exchangeSegment": exchange_segment,
                "interval": interval,
                "securityId": security_id,
                "instrument": instrument,
                "expiryFlag": expiry_flag,
                "expiryCode": expiry_code,
                "strike": strike,
                "drvOptionType": drv_option_type,
                "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
                "fromDate": from_date,
                "toDate": to_date,
            },
        )
        return response.get("data", response) if isinstance(response, dict) else {}

    def get_vix_intraday(self, from_date: str, to_date: str, interval: str = "1") -> dict[str, list[Any]]:
        return self.get_intraday_candles(
            from_date=from_date,
            to_date=to_date,
            interval=interval,
            security_id=str(INDIA_VIX_SECURITY_ID),
            exchange_segment="IDX_I",
            instrument="INDEX",
        )
