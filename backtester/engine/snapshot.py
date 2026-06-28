"""Read-only market state for one bar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

STRIKE_STEP = 50


def _norm_opt_type(opt_type: str) -> str:
    t = str(opt_type).upper()
    if t in ("CE", "CALL"):
        return "CE"
    if t in ("PE", "PUT"):
        return "PE"
    raise ValueError(f"Invalid option type: {opt_type}")


@dataclass(frozen=True)
class OptionBar:
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    oi: int | None
    oi_chg: int | None
    volume: int | None
    iv: float | None

    @classmethod
    def from_row(cls, row: pd.Series) -> "OptionBar":
        def _f(val: Any) -> float | None:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None
            return float(val)

        def _i(val: Any) -> int | None:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None
            return int(val)

        return cls(
            open=_f(row.get("open")),
            high=_f(row.get("high")),
            low=_f(row.get("low")),
            close=_f(row.get("close")),
            oi=_i(row.get("oi")),
            oi_chg=_i(row.get("oi_chg")),
            volume=_i(row.get("volume")),
            iv=_f(row.get("iv")),
        )


class MarketSnapshot:
    def __init__(self, timestamp: datetime, bar_df: pd.DataFrame):
        self.timestamp = timestamp
        self._df = bar_df.copy()
        if not self._df.empty:
            first = self._df.iloc[0]
            self._spot_open = float(first.get("spot_open", first.get("spot_close", 0)))
            self._spot_high = float(first.get("spot_high", first.get("spot_close", 0)))
            self._spot_low = float(first.get("spot_low", first.get("spot_close", 0)))
            self._spot_close = float(first.get("spot_close", 0))
        else:
            self._spot_open = self._spot_high = self._spot_low = self._spot_close = 0.0

    @property
    def spot(self) -> float:
        return self._spot_close

    @property
    def spot_open(self) -> float:
        return self._spot_open

    @property
    def spot_high(self) -> float:
        return self._spot_high

    @property
    def spot_low(self) -> float:
        return self._spot_low

    @property
    def atm_strike(self) -> int:
        spot = self.spot
        if spot is None or spot != spot:  # NaN
            return 0
        return int(round(spot / STRIKE_STEP) * STRIKE_STEP)

    @property
    def chain(self) -> pd.DataFrame:
        return self._df

    def option(self, strike: int | str, opt_type: str) -> OptionBar:
        side = _norm_opt_type(opt_type)
        if isinstance(strike, str) and strike.upper() == "ATM":
            strike = self.atm_strike
        strike = int(strike)
        match = self._df[(self._df["strike"] == strike) & (self._df["opt_type"] == side)]
        if match.empty:
            return OptionBar(None, None, None, None, None, None, None, None)
        return OptionBar.from_row(match.iloc[0])

    def by_offset(self, n: int, opt_type: str) -> OptionBar:
        strike = self.atm_strike + int(n) * STRIKE_STEP
        return self.option(strike, opt_type)
