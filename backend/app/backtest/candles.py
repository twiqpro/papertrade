from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..signal_engine import CandleBar, calc_atr, calc_ema_series, calc_vwap_or_twap

IST = ZoneInfo("Asia/Kolkata")


def aggregate_candles(bars_1m: list[CandleBar], minutes: int) -> list[tuple[datetime, CandleBar]]:
    if minutes <= 1:
        return []
    if not bars_1m:
        return []
    buckets: dict[datetime, list[CandleBar]] = {}
    for index, bar in enumerate(bars_1m):
        # bars assumed ordered; timestamp derived from index for fixture data
        bucket_start = _bucket_start(index, minutes)
        buckets.setdefault(bucket_start, []).append(bar)

    result: list[tuple[datetime, CandleBar]] = []
    for bucket_start in sorted(buckets.keys()):
        chunk = buckets[bucket_start]
        result.append(
            (
                bucket_start,
                CandleBar(
                    open=chunk[0].open,
                    high=max(b.high for b in chunk),
                    low=min(b.low for b in chunk),
                    close=chunk[-1].close,
                    volume=sum(b.volume for b in chunk),
                ),
            )
        )
    return result


def _bucket_start(index: int, minutes: int) -> datetime:
    base = datetime(2025, 1, 1, 9, 15, tzinfo=IST)
    return base + timedelta(minutes=index // minutes * minutes)


def aggregate_from_timestamps(rows: list[tuple[datetime, CandleBar]], minutes: int) -> list[tuple[datetime, CandleBar]]:
    if not rows:
        return []
    buckets: dict[datetime, list[CandleBar]] = {}
    for ts, bar in rows:
        minute_of_day = ts.hour * 60 + ts.minute
        aligned_minute = (minute_of_day // minutes) * minutes
        bucket = ts.replace(hour=aligned_minute // 60, minute=aligned_minute % 60, second=0, microsecond=0)
        buckets.setdefault(bucket, []).append(bar)
    out: list[tuple[datetime, CandleBar]] = []
    for bucket in sorted(buckets):
        chunk = buckets[bucket]
        out.append(
            (
                bucket + timedelta(minutes=minutes),
                CandleBar(
                    open=chunk[0].open,
                    high=max(b.high for b in chunk),
                    low=min(b.low for b in chunk),
                    close=chunk[-1].close,
                    volume=sum(b.volume for b in chunk),
                ),
            )
        )
    return out


def indicator_snapshot(candles: list[CandleBar]) -> dict:
    closes = [bar.close for bar in candles]
    ema9 = calc_ema_series(closes, 9)
    ema15 = calc_ema_series(closes, 15)
    ema20 = calc_ema_series(closes, 20)
    from ..strategy_module.ema_macd_cross import calc_macd_series

    macd_line, macd_sig, _ = calc_macd_series(closes, 12, 26, 9)
    vwap, vwap_label = calc_vwap_or_twap(candles)
    return {
        "ema_9": ema9[-1] if ema9 else 0.0,
        "ema_15": ema15[-1] if ema15 else 0.0,
        "ema_20": ema20[-1] if ema20 else 0.0,
        "ema_9_history": ema9[-3:],
        "macd": macd_line[-1] if macd_line else 0.0,
        "macd_signal": macd_sig[-1] if macd_sig else 0.0,
        "vwap": vwap,
        "vwap_label": vwap_label,
        "atr_14": calc_atr(candles),
        "session_high": max((b.high for b in candles), default=0.0),
        "session_low": min((b.low for b in candles), default=0.0),
    }
