"""Yahoo Finance + Dhan fallback for NIFTY spot and India VIX IV."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

NIFTY_TICKER = "^NSEI"
VIX_TICKER = "^INDIAVIX"
YAHOO_MAX_AGE_DAYS = 30


def _flatten_yahoo(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [str(level[0]).lower() for level in out.columns]
    else:
        out.columns = [str(col).lower() for col in out.columns]
    out = out.reset_index()
    ts_col = out.columns[0]
    out = out.rename(columns={ts_col: "timestamp"})
    for col in ("open", "high", "low", "close", "volume"):
        if col not in out.columns:
            out[col] = 0.0 if col == "volume" else pd.NA
    return out[["timestamp", "open", "high", "low", "close", "volume"]]


def _to_ist_naive(ts: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(ts)
    if getattr(parsed.dt, "tz", None) is None:
        return parsed
    return parsed.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)


def _download_yahoo_day(ticker: str, trading_date: date) -> pd.DataFrame:
    import yfinance as yf

    start = trading_date.isoformat()
    end = (trading_date + timedelta(days=1)).isoformat()
    raw = yf.download(tickers=ticker, start=start, end=end, interval="1m", progress=False, auto_adjust=True)
    flat = _flatten_yahoo(raw)
    if flat.empty:
        return flat
    flat["timestamp"] = _to_ist_naive(flat["timestamp"])
    flat = flat.dropna(subset=["open", "high", "low", "close"])
    return flat[flat["timestamp"].dt.date == trading_date]


def _fetch_spot_dhan_day(trading_date: date) -> pd.DataFrame:
    from datetime import datetime as dt

    from ._bridge import get_dhan_classes

    DhanAdapter, DhanApiError = get_dhan_classes()
    adapter = DhanAdapter()
    if not adapter.authenticate():
        raise DhanApiError("Dhan credentials not configured (set DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN in backend/.env)")

    tomorrow = trading_date + timedelta(days=1)
    candles = adapter.get_intraday_candles(
        from_date=trading_date.isoformat(),
        to_date=tomorrow.isoformat(),
        interval="1",
    )
    opens = candles.get("open") or []
    highs = candles.get("high") or []
    lows = candles.get("low") or []
    closes = candles.get("close") or []
    volumes = candles.get("volume") or []
    timestamps = candles.get("timestamp") or candles.get("start_Time") or []
    if not closes:
        raise DhanApiError(f"Dhan returned no NIFTY spot candles for {trading_date}")

    rows = []
    for i in range(len(closes)):
        if i < len(timestamps):
            ts = dt.fromtimestamp(int(timestamps[i]))
        else:
            ts = dt.combine(trading_date, dt.min.time()) + timedelta(minutes=9 * 60 + 15 + i)
        rows.append({
            "timestamp": ts,
            "open": float(opens[i]) if i < len(opens) else float(closes[i]),
            "high": float(highs[i]) if i < len(highs) else float(closes[i]),
            "low": float(lows[i]) if i < len(lows) else float(closes[i]),
            "close": float(closes[i]),
            "volume": float(volumes[i]) if i < len(volumes) else 0.0,
        })
    return pd.DataFrame(rows)


def fetch_spot_day(trading_date: str | date, source_out: list | None = None) -> pd.DataFrame:
    """Download 1m NIFTY spot + VIX IV. Yahoo for recent days, else Dhan."""
    day = date.fromisoformat(str(trading_date)[:10])
    age_days = (date.today() - day).days
    if day > date.today():
        raise ValueError(f"Cannot download future date {day}")

    errors: list[str] = []
    nifty = pd.DataFrame()

    if age_days <= YAHOO_MAX_AGE_DAYS:
        try:
            nifty = _download_yahoo_day(NIFTY_TICKER, day)
            if not nifty.empty:
                if source_out is not None:
                    source_out.append("yahoo")
        except Exception as exc:
            errors.append(f"Yahoo: {exc}")

    if nifty.empty:
        try:
            nifty = _fetch_spot_dhan_day(day)
            if source_out is not None:
                source_out.append("dhan")
        except Exception as exc:
            errors.append(f"Dhan: {exc}")

    if nifty.empty:
        raise ValueError(f"No spot data for {day}. " + "; ".join(errors))

    vix = pd.DataFrame()
    if age_days <= YAHOO_MAX_AGE_DAYS:
        try:
            vix = _download_yahoo_day(VIX_TICKER, day)
        except Exception:
            pass

    out = nifty.rename(columns={
        "open": "spot_open", "high": "spot_high", "low": "spot_low", "close": "spot_close",
    })
    out["symbol"] = "NIFTY"

    if not vix.empty:
        vix = vix.rename(columns={"close": "iv"})[["timestamp", "iv"]]
        out = out.merge(vix, on="timestamp", how="left")
    else:
        out["iv"] = pd.NA

    return out[["timestamp", "symbol", "spot_open", "spot_high", "spot_low", "spot_close", "iv"]]


def fetch_spot(
    symbol: str = "NIFTY",
    start: str | None = None,
    end: str | None = None,
    interval: str = "5min",
) -> pd.DataFrame:
    from ..store import trading_days

    if not start or not end:
        raise ValueError("start and end required")
    frames = [fetch_spot_day(d) for d in trading_days(start, end)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise ValueError(f"No spot data for {start}→{end}")
    return pd.concat(frames, ignore_index=True)
