"""EMA 9/20 ATM Limit Scalper (corrected).

Signal
------
Direction comes from EMA(9) vs EMA(20) on NIFTY spot **1-minute** bars. EMAs
**reset at each day's 9:15 IST open** (no carry-over from prior sessions).
When the EMAs separate by at least ``ema_gap`` points we look at the current
ATM option premium (CE if EMA9 > EMA20, PE otherwise) and *arm a resting BUY
limit* ``strike_offset`` points below it. The limit is nudged off any multiple
of 5 so it never sits on a round number (103 instead of 105, 93 instead of 95).

Fill
----
The limit rests on a FIXED strike (the strike that was ATM when the order was
armed). On a LATER bar, if that option's low trades down to the limit, we fill
*at the limit price* -- not the bar close.

This is the key fix versus the original, which computed the limit from a bar's
own close and then required that same close to be at/below it. Because the
limit is ~2 points below the close by construction, that condition was
arithmetically impossible and the strategy never opened a single trade.

Exits
-----
+``target_pts`` target or -``sl_pts`` (₹5) stop on the option premium, measured from
the limit fill price. If both are touched in one bar we assume the stop hit
first (pessimistic). After any exit we wait ``cooldown_mins`` before arming the
next order. No new orders are armed or filled at or after ``entry_cutoff_hhmm``
(3:10 PM). Intraday only: any open position is squared off at or after
``square_off_hhmm`` (3:20 PM).

IMPORTANT ENGINE ASSUMPTION
---------------------------
``ctx.enter(..., entry_price=limit)`` must make the engine record the fill at
``limit``. The original engine note said it "fills at close"; if that is still
true and ``entry_price`` is ignored, the +2/-5 levels will be measured from
the wrong price and you will drift back toward the broken behaviour. Confirm
the engine honours ``entry_price`` before trusting results.

Position sizing (compounding)
-----------------------------
Starting equity is ``trading_capital`` (₹1 lac). After each exit, realized
P&L is added to ``_equity`` and the next trade's qty is ``int(equity / premium)``.
Example: start ₹1,00,000, +₹70,000 profit → next sizing uses ₹1,70,000.
Losses shrink equity the same way (e.g. down to ₹15,000 sizes the next trade
on that balance). Equity carries across sessions within a backtest run.
"""


class Strategy:
    def setup(self, ctx):
        # --- signal / trade params -----------------------------------------
        ctx.params["ema_fast"] = 9
        ctx.params["ema_slow"] = 20
        ctx.params["ema_gap"] = 5          # min |EMA9 - EMA20| to take a side
        ctx.params["strike_offset"] = 2    # points below ATM premium for limit
        ctx.params["target_pts"] = 2.5       # +2.5 profit on the premium
        ctx.params["sl_pts"] = 3           # -3 stop on the premium
        ctx.params["cooldown_mins"] = 5    # wait after an exit before re-arming (5 min on 1m chart)
        ctx.params["trading_capital"] = 100_000  # ₹1 lac starting equity (compounds on P&L)
        ctx.params["chart_interval"] = "1min"  # use 1-min data in the data panel

        # --- order / session hygiene ---------------------------------------
        ctx.params["order_ttl_bars"] = 6   # cancel resting limit after 6 x 1-min bars
        ctx.params["warmup_bars"] = 20     # 20 one-min bars after 9:15 open
        ctx.params["session_open"] = (9, 15)   # EMAs reset at NIFTY open
        ctx.params["entry_cutoff_hhmm"] = (15, 10)  # no new orders / fills after 3:10
        ctx.params["square_off_hhmm"] = (15, 20)  # force-exit open positions at 3:20

        # --- internal state ------------------------------------------------
        self._ema_fast = None
        self._ema_slow = None
        self._bars_seen = 0
        self._session_date = None
        self._last_exit_time = None
        self._equity = float(ctx.params["trading_capital"])  # compounds after each exit

        # resting limit order (None when no order is live)
        self._pending_limit = None
        self._pending_side = None
        self._pending_strike = None
        self._pending_age = 0

    # ----------------------------------------------------------------------
    # helpers
    # ----------------------------------------------------------------------
    @staticmethod
    def _update_ema(prev, price, n):
        if prev is None:
            return price
        alpha = 2 / (n + 1)
        return alpha * price + (1 - alpha) * prev

    @staticmethod
    def _limit_price(atm_close, offset):
        """ATM premium minus offset, pushed off any round (multiple-of-5) price.

        Subtracting 2 from a multiple of 5 always lands on a non-multiple, so
        the loop runs at most once: 105 -> 103, 97 -> 95 -> 93.

        NOTE: this avoids ROUND numbers (…00, …05), which matches the spoken
        examples (103, 93) and "never 100/105/110". It does NOT force an *odd*
        number -- e.g. a 108 premium yields 106 (even, but non-round). If you
        truly want odd-only limits, change the step rule below.
        """
        raw = int(round(atm_close)) - offset
        while raw > 0 and raw % 5 == 0:
            raw -= 2
        return raw

    def _is_past_entry_cutoff(self, snapshot, ctx):
        t = snapshot.timestamp
        h, m = ctx.params["entry_cutoff_hhmm"]
        return (t.hour, t.minute) >= (h, m)

    def _is_square_off(self, snapshot, ctx):
        t = snapshot.timestamp
        h, m = ctx.params["square_off_hhmm"]
        return (t.hour, t.minute) >= (h, m)

    def _clear_pending(self):
        self._pending_limit = None
        self._pending_side = None
        self._pending_strike = None
        self._pending_age = 0

    def _reset_session(self, snapshot, ctx):
        """Fresh EMA 9/20 from the 9:15 open each trading day."""
        self._ema_fast = None
        self._ema_slow = None
        self._bars_seen = 0
        self._clear_pending()
        self._last_exit_time = None
        open_h, open_m = ctx.params.get("session_open", (9, 15))
        ctx.log(f"session reset {snapshot.timestamp.date()} @ {open_h:02d}:{open_m:02d}")

    def _on_new_session_bar(self, snapshot, ctx) -> bool:
        """Return True when this bar starts a new 9:15 session (new date)."""
        bar_date = snapshot.timestamp.date()
        if self._session_date != bar_date:
            self._session_date = bar_date
            self._reset_session(snapshot, ctx)
            return True
        return False

    def _before_session_open(self, snapshot, ctx) -> bool:
        open_h, open_m = ctx.params.get("session_open", (9, 15))
        t = snapshot.timestamp
        return (t.hour, t.minute) < (open_h, open_m)

    def _qty_for_equity(self, premium: float) -> int:
        """Lots/units sized from compounded equity: equity ÷ premium (min 1)."""
        if premium is None or premium <= 0:
            return 1
        if self._equity <= 0:
            return 0
        return max(1, int(self._equity / premium))

    @staticmethod
    def _trade_pnl(direction: str, entry: float, exit_price: float, qty: int) -> float:
        raw = exit_price - entry
        if direction.upper() == "SELL":
            raw = -raw
        return round(raw * qty, 2)

    def _compound_on_exit(self, exit_price: float, ctx, label: str) -> None:
        """Add realized P&L to equity so the next trade sizes on the new balance."""
        pos = ctx.position
        pnl = self._trade_pnl(pos.direction, pos.entry_price, exit_price, pos.qty)
        prev = self._equity
        self._equity = round(prev + pnl, 2)
        ctx.log(
            f"equity {prev:,.0f} → {self._equity:,.0f} "
            f"({label}, pnl {pnl:+,.0f})"
        )

    # ----------------------------------------------------------------------
    # main loop
    # ----------------------------------------------------------------------
    def on_bar(self, snapshot, ctx):
        # 0. New trading day -> reset EMAs at 9:15 session open.
        new_session = self._on_new_session_bar(snapshot, ctx)
        if self._before_session_open(snapshot, ctx):
            return ctx.skip(reason="before 9:15 session open")

        spot = snapshot.spot
        self._ema_fast = self._update_ema(self._ema_fast, spot, ctx.params["ema_fast"])
        self._ema_slow = self._update_ema(self._ema_slow, spot, ctx.params["ema_slow"])
        self._bars_seen += 1
        f, s = self._ema_fast, self._ema_slow

        if new_session:
            return ctx.skip(reason="9:15 open — EMAs seeded; warming up")

        past_cutoff = self._is_past_entry_cutoff(snapshot, ctx)
        square_off = self._is_square_off(snapshot, ctx)

        # 1. Manage an open position first.
        if ctx.position is not None:
            opt = snapshot.option(ctx.position.strike, ctx.position.side)
            target = ctx.position.entry_price + ctx.params["target_pts"]
            stop = ctx.position.entry_price - ctx.params["sl_pts"]

            # Forced intraday exit.
            if square_off:
                exit_px = opt.close if opt.close is not None else ctx.position.entry_price
                self._compound_on_exit(exit_px, ctx, "square-off")
                self._last_exit_time = snapshot.timestamp
                return ctx.exit(reason="square-off (EOD)")

            # Worst-case ordering: stop assumed hit before target in one bar.
            if opt.low is not None and opt.low <= stop:
                self._compound_on_exit(stop, ctx, "SL")
                self._last_exit_time = snapshot.timestamp
                return ctx.exit(reason=f"SL hit (low {opt.low:.1f} <= {stop:.1f})")
            if opt.high is not None and opt.high >= target:
                self._compound_on_exit(target, ctx, "target")
                self._last_exit_time = snapshot.timestamp
                return ctx.exit(reason=f"target hit (high {opt.high:.1f} >= {target:.1f})")
            return ctx.hold()

        # 2. A resting limit order is live -> try to fill / cancel it.
        if self._pending_limit is not None:
            self._pending_age += 1

            # Cancel if past entry cutoff or session is ending.
            if past_cutoff or square_off:
                lim = self._pending_limit
                self._clear_pending()
                tag = "after 3:10 entry cutoff" if past_cutoff else "square-off"
                return ctx.skip(reason=f"cancel limit {lim} ({tag})")

            # Cancel if it has rested too long.
            if self._pending_age > ctx.params["order_ttl_bars"]:
                lim = self._pending_limit
                self._clear_pending()
                return ctx.skip(reason=f"cancel limit {lim} (TTL {ctx.params['order_ttl_bars']} bars)")

            # Cancel if the trend that justified the order has gone.
            gap = (f - s) if (f is not None and s is not None) else 0.0
            still_valid = (
                abs(gap) >= ctx.params["ema_gap"]
                and ((gap > 0) == (self._pending_side == "CE"))
            )
            if not still_valid:
                lim = self._pending_limit
                self._clear_pending()
                return ctx.skip(reason=f"cancel limit {lim} (EMA gap {gap:+.1f} no longer supports {self._pending_side})")

            # Check the FIXED strike this order was placed on.
            opt = snapshot.option(self._pending_strike, self._pending_side)
            if opt.low is None:
                return ctx.skip(reason=f"no {self._pending_side} quote at {self._pending_strike}; waiting")

            if opt.low <= self._pending_limit:
                # Buy limit filled. Assume fill AT the limit price (conservative
                # for a buyer: if the bar gapped below, a real fill could be
                # cheaper, so this never understates our cost).
                fill = self._pending_limit
                side = self._pending_side
                strike = self._pending_strike
                qty = self._qty_for_equity(fill)
                if qty < 1:
                    self._clear_pending()
                    return ctx.skip(reason=f"insufficient equity ({self._equity:,.0f}) to size trade")
                self._clear_pending()
                ctx.log(
                    f"limit {fill} filled on {side} {strike} "
                    f"(bar low {opt.low:.1f}, qty {qty}, equity ₹{self._equity:,.0f})"
                )
                return ctx.enter(
                    side=side,
                    strike=strike,          # concrete strike, NOT "ATM"
                    entry_price=fill,       # fill at the limit (see engine note)
                    direction="BUY",
                    qty=qty,
                    reason=f"resting limit {fill} hit · qty {qty} (₹{self._equity:,.0f}/{fill:.1f})",
                )

            return ctx.skip(reason=f"limit {self._pending_limit} resting ({self._pending_side} low {opt.low:.1f})")

        # 3. No position, no resting order -> consider arming a new one.

        if past_cutoff:
            return ctx.skip(reason="after 3:10 — no new orders")
        if square_off:
            return ctx.skip(reason="square-off window; no new orders")

        if self._bars_seen < ctx.params["warmup_bars"]:
            return ctx.skip(
                reason=f"warming up 1m EMAs ({self._bars_seen}/{ctx.params['warmup_bars']} bars since 9:15)"
            )

        if self._equity <= 0:
            return ctx.skip(reason=f"equity depleted ({self._equity:,.0f})")

        # Cooldown after the last exit.
        if self._last_exit_time is not None:
            mins = (snapshot.timestamp - self._last_exit_time).total_seconds() / 60.0
            if mins < ctx.params["cooldown_mins"]:
                return ctx.skip(reason=f"cooldown ({mins:.1f}/{ctx.params['cooldown_mins']} min)")

        # EMA trend filter.
        if f is None or s is None:
            return ctx.skip(reason="EMAs warming up")
        gap = f - s
        if abs(gap) < ctx.params["ema_gap"]:
            return ctx.skip(reason=f"EMA gap {gap:+.1f} < {ctx.params['ema_gap']}")
        side = "CE" if gap > 0 else "PE"

        # Read the current ATM premium and arm the resting limit.
        strike = snapshot.atm_strike
        opt = snapshot.option(strike, side)
        if opt.close is None:
            return ctx.skip(reason=f"no {side} quote at ATM {strike}")

        limit = self._limit_price(opt.close, ctx.params["strike_offset"])
        if limit <= 0:
            return ctx.skip(reason=f"ATM premium too low to set limit ({opt.close:.1f})")

        preview_qty = self._qty_for_equity(limit)
        # Arm the order on THIS strike. It rests; fills happen on later bars.
        self._pending_limit = limit
        self._pending_side = side
        self._pending_strike = strike
        self._pending_age = 0
        ctx.log(
            f"arm limit {limit} on {side} {strike} "
            f"(ATM {opt.close:.1f}, qty~{preview_qty}, equity ₹{self._equity:,.0f}, EMA gap {gap:+.1f})"
        )
        return ctx.skip(
            reason=f"armed {side} limit {limit} at strike {strike} "
            f"· qty~{preview_qty} (equity ₹{self._equity:,.0f})"
        )