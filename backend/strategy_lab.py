"""
Simple NIFTY Options Strategy — EMA 9/20 trend + big bar + continuation entry
=============================================================================

v2 — fixes the "clear trend, ZERO trades" bug found on 2026-06-24.

WHAT WAS BROKEN (v1)
--------------------
v1 armed on the EMA CROSS EVENT, then required a pullback within a few bars, then
needed a NEW cross to try again. In a strong one-directional trend:
  - price never pulled back  -> setup timed out
  - EMAs never re-crossed     -> never re-armed
  => it sat FLAT through the entire move. The cleaner the trend, the surer it missed.

THE FIX
-------
Key off the TREND STATE, not the cross event:
  - Trend = EMA 9 vs EMA 20 (persistent; recomputed every bar).
  - A big bar in the trend direction "confirms" the leg (momentum is real).
  - While the trend holds and a big bar has confirmed it, you stay ELIGIBLE.
  - ENTRY = a bar that breaks the recent N-bar high (CE) / low (PE) in the trend
    direction, as long as price isn't over-extended from EMA 9.
      * This single rule catches BOTH cases: a pullback-and-resume breaks the prior
        swing high on the resume bar, AND a runaway trend breaks it on continuation.
  - Re-arms as many times as the trend lasts (with a cooldown after each exit).

EXITS (unchanged idea): structural stop -> breakeven at +1R -> trail by EMA 9; hard
exit on trend flip or session cutoff. Decisions on the INDEX; you hold the ATM option.

NOTE: feed EMA 20 as `ema_slow` (your export was carrying EMA 15 — different crosses
than your TradingView chart). CAVEAT: this now fires far more often; backtest it
out-of-sample and check that win rate rises with signal quality. Not investment advice.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float
    ema9: float        # EMA 9 of close
    ema_slow: float    # EMA 20 of close  (feed 20, not 15)
    atr: float         # ATR(14)
    index: int         # bar number within the session


@dataclass
class Config:
    # big bar = momentum confirmation for the leg
    big_bar_atr_mult: float = 1.5
    big_bar_body_ratio: float = 0.60
    # entry = break of recent structure in trend direction
    breakout_lookback: int = 3        # break the high/low of the prior N bars
    max_extension_atr: float = 1.5    # anti-chase: entry within this * ATR of EMA9
    # stop = structural swing
    swing_lookback: int = 5           # stop beyond the low/high of the last N bars
    stop_buffer_atr: float = 0.10
    # trade management
    breakeven_at_R: float = 1.0
    cooldown_bars: int = 2            # bars to wait after an exit before re-entering
    session_cutoff_index: Optional[int] = None


@dataclass
class Trade:
    side: str
    entry_index: int
    entry_price: float
    stop: float
    risk: float
    be_armed: bool = False


class Strategy:
    def __init__(self, cfg: Config = Config()):
        self.cfg = cfg
        self.trend: Optional[str] = None        # "CE" (up) | "PE" (down)
        self.had_big_bar = False                # a big bar confirmed THIS trend leg
        self.recent: List[Bar] = []             # rolling window of recent bars
        self.trade: Optional[Trade] = None
        self.last_exit_index = -10_000

    def _trend_of(self, b: Bar) -> Optional[str]:
        if b.ema9 > b.ema_slow:
            return "CE"
        if b.ema9 < b.ema_slow:
            return "PE"
        return None

    def _is_big_bar(self, b: Bar, side: str) -> bool:
        rng = b.high - b.low
        if rng <= 0 or b.atr <= 0:
            return False
        if rng < self.cfg.big_bar_atr_mult * b.atr:
            return False
        if abs(b.close - b.open) < self.cfg.big_bar_body_ratio * rng:
            return False
        if side == "CE" and b.close <= b.open:
            return False
        if side == "PE" and b.close >= b.open:
            return False
        return True

    def on_bar(self, b: Bar):
        """Returns ('ENTER', side, price, stop) | ('EXIT', reason, price) | None."""
        action = None
        cfg = self.cfg

        # --- trend state (persistent) ---
        new_trend = self._trend_of(b)
        if new_trend != self.trend:
            self.trend = new_trend
            self.had_big_bar = False            # new leg must be reconfirmed by a big bar
            # a trend flip is also the hard exit, handled below if in a trade

        # --- manage an open trade ---
        if self.trade:
            t = self.trade
            if cfg.session_cutoff_index is not None and b.index >= cfg.session_cutoff_index:
                action = ("EXIT", "session_cutoff", b.close); self._close(b)
            elif (t.side == "CE" and b.ema9 < b.ema_slow) or (t.side == "PE" and b.ema9 > b.ema_slow):
                action = ("EXIT", "trend_flip", b.close); self._close(b)
            else:
                profit = (b.close - t.entry_price) if t.side == "CE" else (t.entry_price - b.close)
                if not t.be_armed and profit >= cfg.breakeven_at_R * t.risk:
                    t.stop = t.entry_price; t.be_armed = True
                if t.be_armed and ((t.side == "CE" and b.close < b.ema9) or
                                   (t.side == "PE" and b.close > b.ema9)):
                    action = ("EXIT", "ema9_trail", b.close); self._close(b)
                elif (t.side == "CE" and b.low <= t.stop) or (t.side == "PE" and b.high >= t.stop):
                    action = ("EXIT", "stop", t.stop); self._close(b)
            self._push(b)
            return action

        # --- look for an entry (flat) ---
        if self.trend and self._is_big_bar(b, self.trend):
            self.had_big_bar = True             # confirm the leg

        eligible = (
            self.trend is not None
            and self.had_big_bar
            and b.index - self.last_exit_index > cfg.cooldown_bars
            and len(self.recent) >= cfg.breakout_lookback
        )
        if eligible:
            prior = self.recent[-cfg.breakout_lookback:]
            not_extended = abs(b.close - b.ema9) <= cfg.max_extension_atr * b.atr
            if self.trend == "CE":
                trigger = b.close > max(p.high for p in prior) and b.close > b.open
            else:
                trigger = b.close < min(p.low for p in prior) and b.close < b.open
            if trigger and not_extended:
                action = self._enter(self.trend, b)

        self._push(b)
        return action

    def _enter(self, side, b: Bar):
        cfg = self.cfg
        window = self.recent[-cfg.swing_lookback:] + [b]
        buf = cfg.stop_buffer_atr * b.atr
        if side == "CE":
            stop = min(p.low for p in window) - buf
        else:
            stop = max(p.high for p in window) + buf
        entry = b.close
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        self.trade = Trade(side=side, entry_index=b.index, entry_price=entry, stop=stop, risk=risk)
        return ("ENTER", side, entry, round(stop, 2))

    def _close(self, b: Bar):
        self.trade = None
        self.last_exit_index = b.index

    def _push(self, b: Bar):
        self.recent.append(b)
        if len(self.recent) > 50:
            self.recent.pop(0)


# ---------------------------------------------------------------------------
#   strat = Strategy(Config(session_cutoff_index=74))
#   for bar in bars:                 # bar.ema_slow must be EMA 20
#       act = strat.on_bar(bar)
#       if act and act[0]=="ENTER": buy_atm_option(act[1])      # CE / PE
#       elif act and act[0]=="EXIT": close_option()
# ---------------------------------------------------------------------------