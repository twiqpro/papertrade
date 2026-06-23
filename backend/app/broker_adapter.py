from __future__ import annotations

from typing import Protocol


class BrokerDataAdapter(Protocol):
    def authenticate(self) -> bool:
        ...

    def get_instruments(self) -> list[dict]:
        ...

    def get_ltp(self, instruments: list[str]) -> dict[str, float]:
        ...

    def get_historical_candles(self, instrument: str, from_time: str, to_time: str, timeframe: str) -> list[dict]:
        ...

    def get_option_chain(self, underlying: str, expiry: str) -> dict:
        ...

