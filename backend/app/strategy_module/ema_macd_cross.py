"""
NIFTY 5m — EMA 9/20 cross + big-bar filter, ATM option buying.
Index signals; premium-based exits on the held option.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import StrategySettings
from ..signal_engine import CandleBar, calc_ema_series


@dataclass
class IndicatorBar:
    open: float
    high: float
    low: float
    close: float
    ema_fast: float
    ema_slow: float
    macd: float
    macd_sig: float
    atr: float
    body: float


def calc_macd_series(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float], list[float], list[float]]:
    if not closes:
        return [], [], []
    ema_fast = calc_ema_series(closes, fast)
    ema_slow = calc_ema_series(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    macd_sig = calc_ema_series(macd_line, signal)
    hist = [m - s for m, s in zip(macd_line, macd_sig)]
    return macd_line, macd_sig, hist


def calc_atr_series(candles: list[CandleBar], period: int = 14) -> list[float]:
    if not candles:
        return []
    trs: list[float] = []
    for i, bar in enumerate(candles):
        if i == 0:
            trs.append(max(bar.high - bar.low, 0.0))
        else:
            prev_close = candles[i - 1].close
            trs.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - prev_close),
                    abs(bar.low - prev_close),
                )
            )
    return calc_ema_series(trs, period)


def build_indicator_bars(candles: list[CandleBar], settings: StrategySettings) -> list[IndicatorBar]:
    if not candles:
        return []
    closes = [bar.close for bar in candles]
    ema_fast = calc_ema_series(closes, settings.ema_fast)
    ema_slow = calc_ema_series(closes, settings.ema_slow)
    macd_line, macd_sig, _ = calc_macd_series(
        closes,
        settings.macd_fast,
        settings.macd_slow,
        settings.macd_signal_period,
    )
    atr_series = calc_atr_series(candles, settings.atr_period)
    rows: list[IndicatorBar] = []
    for i, bar in enumerate(candles):
        rows.append(
            IndicatorBar(
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                ema_fast=ema_fast[i],
                ema_slow=ema_slow[i],
                macd=macd_line[i],
                macd_sig=macd_sig[i],
                atr=atr_series[i] if i < len(atr_series) else 0.0,
                body=abs(bar.close - bar.open),
            )
        )
    return rows


def _is_big(bar: IndicatorBar, settings: StrategySettings) -> bool:
    if bar.atr <= 0:
        return False
    return bar.body >= settings.big_bar_atr_mult * bar.atr


def entry_signal(prev: IndicatorBar, cur: IndicatorBar, settings: StrategySettings) -> Optional[str]:
    """Fresh 9/20 cross on cur bar + big impulse candle (cur or prev)."""
    big_bull = (_is_big(cur, settings) and cur.close > cur.open) or (
        _is_big(prev, settings) and prev.close > prev.open
    )
    big_bear = (_is_big(cur, settings) and cur.close < cur.open) or (
        _is_big(prev, settings) and prev.close < prev.open
    )

    bull_cross = prev.ema_fast <= prev.ema_slow and cur.ema_fast > cur.ema_slow
    bull_macd_ok = (cur.macd > cur.macd_sig) if settings.require_macd else True
    if bull_cross and big_bull and bull_macd_ok:
        return "CE"

    bear_cross = prev.ema_fast >= prev.ema_slow and cur.ema_fast < cur.ema_slow
    bear_macd_ok = (cur.macd < cur.macd_sig) if settings.require_macd else True
    if bear_cross and big_bear and bear_macd_ok:
        return "PE"

    return None


def signal_exit_hit(cur: IndicatorBar, side: str) -> bool:
    if side == "CE":
        return cur.ema_fast < cur.ema_slow or cur.close < cur.ema_fast
    return cur.ema_fast > cur.ema_slow or cur.close > cur.ema_fast


def entry_skip_reason(prev: IndicatorBar, cur: IndicatorBar, settings: StrategySettings) -> str:
    side = entry_signal(prev, cur, settings)
    if side:
        return f"{side} big-bar cross entry"
    bull_cross = prev.ema_fast <= prev.ema_slow and cur.ema_fast > cur.ema_slow
    bear_cross = prev.ema_fast >= prev.ema_slow and cur.ema_fast < cur.ema_slow
    if bull_cross or bear_cross:
        return "Cross without big bar (body >= ATR mult on cross or prior bar)"
    return "No fresh EMA 9/20 cross"
