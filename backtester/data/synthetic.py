"""Synthetic Nifty options data for local development."""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

import pandas as pd

STRIKE_STEP = 50
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _trading_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _interval_minutes(interval: str) -> int:
    mapping = {"1min": 1, "5min": 5, "15min": 15, "1h": 60}
    key = interval.lower().replace(" ", "")
    if key not in mapping:
        raise ValueError(f"Unsupported interval: {interval}")
    return mapping[key]


def _timestamps_for_day(day: date, interval: str) -> list[datetime]:
    step = _interval_minutes(interval)
    start = datetime.combine(day, MARKET_OPEN)
    end = datetime.combine(day, MARKET_CLOSE)
    out: list[datetime] = []
    current = start
    while current <= end:
        out.append(current)
        current += timedelta(minutes=step)
    return out


def _atm_strike(spot: float) -> int:
    return int(round(spot / STRIKE_STEP) * STRIKE_STEP)


def generate(
    symbol: str = "NIFTY",
    start: str | date | None = None,
    end: str | date | None = None,
    interval: str = "5min",
    strikes_around_atm: int = 10,
) -> pd.DataFrame:
    """Generate long-format option chain bars with spot OHLC."""
    start_d = _parse_date(start or "2026-01-02")
    end_d = _parse_date(end or "2026-01-03")
    strikes_around_atm = min(max(int(strikes_around_atm), 1), 18)

    rng = random.Random(hash((symbol, str(start_d), str(end_d), interval)) & 0xFFFFFFFF)
    spot = 24000.0 + rng.uniform(-200, 200)
    prev_oi: dict[tuple[int, str], int] = {}

    rows: list[dict] = []
    for day in _trading_days(start_d, end_d):
        for ts in _timestamps_for_day(day, interval):
            drift = rng.uniform(-15, 15)
            spot_open = spot
            spot_close = max(22000.0, spot + drift)
            spot_high = max(spot_open, spot_close) + rng.uniform(0, 8)
            spot_low = min(spot_open, spot_close) - rng.uniform(0, 8)
            spot = spot_close

            atm = _atm_strike(spot)
            strikes = [atm + i * STRIKE_STEP for i in range(-strikes_around_atm, strikes_around_atm + 1)]

            for strike in strikes:
                for opt_type in ("CE", "PE"):
                    moneyness = abs(strike - atm) / STRIKE_STEP
                    base = max(5.0, 120.0 - moneyness * 8.0)
                    if opt_type == "PE":
                        base += max(0, (atm - strike) / STRIKE_STEP) * 3.0
                    else:
                        base += max(0, (strike - atm) / STRIKE_STEP) * 3.0

                    o = base + rng.uniform(-2, 2)
                    c = max(0.5, o + rng.uniform(-4, 4))
                    h = max(o, c) + rng.uniform(0, 2)
                    l = max(0.5, min(o, c) - rng.uniform(0, 2))
                    volume = int(rng.uniform(100, 5000))

                    key = (strike, opt_type)
                    prev = prev_oi.get(key, int(rng.uniform(50000, 150000)))
                    # Bias positive OI change on ATM CE ~35% of bars for demo entries
                    if strike == atm and opt_type == "CE" and rng.random() < 0.35:
                        oi_chg = int(rng.uniform(500, 5000))
                    else:
                        oi_chg = int(rng.uniform(-3000, 3000))
                    oi = max(0, prev + oi_chg)
                    prev_oi[key] = oi
                    iv = round(rng.uniform(12.0, 22.0), 2)

                    rows.append(
                        {
                            "timestamp": ts,
                            "symbol": symbol,
                            "spot_open": round(spot_open, 2),
                            "spot_high": round(spot_high, 2),
                            "spot_low": round(spot_low, 2),
                            "spot_close": round(spot_close, 2),
                            "strike": strike,
                            "opt_type": opt_type,
                            "open": round(o, 2),
                            "high": round(h, 2),
                            "low": round(l, 2),
                            "close": round(c, 2),
                            "oi": oi,
                            "oi_chg": oi_chg,
                            "volume": volume,
                            "iv": iv,
                        }
                    )

    return pd.DataFrame(rows)
