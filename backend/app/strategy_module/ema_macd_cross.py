"""
NIFTY 5m — EMA 9/20 cross + MACD confirmation, ATM option buying.
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
            )
        )
    return rows


def entry_signal(prev2: IndicatorBar, prev: IndicatorBar, cur: IndicatorBar, settings: StrategySettings) -> Optional[str]:
    """Cross on prev bar, confirm on cur. Returns 'CE', 'PE', or None."""
    sep_pts = abs(cur.ema_fast - cur.ema_slow)
    sep_ok = sep_pts >= settings.min_ema_sep_pct * cur.close

    bull_cross = prev2.ema_fast <= prev2.ema_slow and prev.ema_fast > prev.ema_slow
    bull_hold = cur.ema_fast > cur.ema_slow
    bull_slope_pts = cur.ema_fast - prev.ema_fast
    bull_slope = bull_slope_pts > 0
    bull_strong = bull_slope_pts >= settings.min_ema_slope_pts
    bull_close = cur.close > cur.ema_fast and cur.close > cur.ema_slow
    bull_macd = cur.macd > cur.macd_sig
    if bull_cross and bull_hold and bull_slope and bull_close and bull_macd and (sep_ok or bull_strong):
        return "CE"

    bear_cross = prev2.ema_fast >= prev2.ema_slow and prev.ema_fast < prev.ema_slow
    bear_hold = cur.ema_fast < cur.ema_slow
    bear_slope_pts = prev.ema_fast - cur.ema_fast
    bear_slope = bear_slope_pts > 0
    bear_strong = bear_slope_pts >= settings.min_ema_slope_pts
    bear_close = cur.close < cur.ema_fast and cur.close < cur.ema_slow
    bear_macd = cur.macd < cur.macd_sig
    if bear_cross and bear_hold and bear_slope and bear_close and bear_macd and (sep_ok or bear_strong):
        return "PE"

    return None


def signal_exit_hit(cur: IndicatorBar, side: str) -> bool:
    if side == "CE":
        return cur.ema_fast < cur.ema_slow or cur.close < cur.ema_fast
    return cur.ema_fast > cur.ema_slow or cur.close > cur.ema_fast


def entry_skip_reason(prev2: IndicatorBar, prev: IndicatorBar, cur: IndicatorBar, settings: StrategySettings) -> str:
    side = entry_signal(prev2, prev, cur, settings)
    if side:
        return f"{side} entry confirmed"
    bull_cross = prev2.ema_fast <= prev2.ema_slow and prev.ema_fast > prev.ema_slow
    bear_cross = prev2.ema_fast >= prev2.ema_slow and prev.ema_fast < prev.ema_slow
    if bull_cross or bear_cross:
        return "Cross seen — waiting for confirmation (close/MACD/anti-chop)"
    return "No fresh EMA 9/20 cross on prior bar"
