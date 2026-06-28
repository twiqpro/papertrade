"""EMA 9/20 ATM Limit Scalper - profit-focused variant.

This file is a standalone strategy variant for testing live/backtest behavior.
It keeps the same core logic as ``ema_atm_limit.py``:
- EMA 9/20 trend on NIFTY spot 1-minute bars.
- Resting BUY limit on the fixed ATM strike.
- Compounded equity sizing.
- No new orders or pending fills after 3:10 PM.

Tuning note: this variant had better tested P&L than the high win-rate setup,
but its win rate is much lower because it uses a better target/stop ratio.
"""


class Strategy:
    def setup(self, ctx):
        # --- signal / trade params -----------------------------------------
        ctx.params["ema_fast"] = 9
        ctx.params["ema_slow"] = 20
        ctx.params["ema_gap"] = 5
        ctx.params["strike_offset"] = 4
        ctx.params["target_pts"] = 3.0
        ctx.params["sl_pts"] = 2
        ctx.params["cooldown_mins"] = 10
        ctx.params["trading_capital"] = 100_000
        ctx.params["chart_interval"] = "1min"

        # --- order / session hygiene ---------------------------------------
        ctx.params["order_ttl_bars"] = 6
        ctx.params["warmup_bars"] = 20
        ctx.params["session_open"] = (9, 15)
        ctx.params["entry_cutoff_hhmm"] = (15, 10)
        ctx.params["square_off_hhmm"] = (15, 20)

        # --- internal state ------------------------------------------------
        self._ema_fast = None
        self._ema_slow = None
        self._bars_seen = 0
        self._session_date = None
        self._last_exit_time = None
        self._equity = float(ctx.params["trading_capital"])

        self._pending_limit = None
        self._pending_side = None
        self._pending_strike = None
        self._pending_age = 0

    @staticmethod
    def _update_ema(prev, price, n):
        if prev is None:
            return price
        alpha = 2 / (n + 1)
        return alpha * price + (1 - alpha) * prev

    @staticmethod
    def _limit_price(atm_close, offset):
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
        self._ema_fast = None
        self._ema_slow = None
        self._bars_seen = 0
        self._clear_pending()
        self._last_exit_time = None
        open_h, open_m = ctx.params.get("session_open", (9, 15))
        ctx.log(f"session reset {snapshot.timestamp.date()} @ {open_h:02d}:{open_m:02d}")

    def _on_new_session_bar(self, snapshot, ctx) -> bool:
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
        pos = ctx.position
        pnl = self._trade_pnl(pos.direction, pos.entry_price, exit_price, pos.qty)
        prev = self._equity
        self._equity = round(prev + pnl, 2)
        ctx.log(f"equity {prev:,.0f} -> {self._equity:,.0f} ({label}, pnl {pnl:+,.0f})")

    def on_bar(self, snapshot, ctx):
        new_session = self._on_new_session_bar(snapshot, ctx)
        if self._before_session_open(snapshot, ctx):
            return ctx.skip(reason="before 9:15 session open")

        spot = snapshot.spot
        self._ema_fast = self._update_ema(self._ema_fast, spot, ctx.params["ema_fast"])
        self._ema_slow = self._update_ema(self._ema_slow, spot, ctx.params["ema_slow"])
        self._bars_seen += 1
        f, s = self._ema_fast, self._ema_slow

        if new_session:
            return ctx.skip(reason="9:15 open - EMAs seeded; warming up")

        past_cutoff = self._is_past_entry_cutoff(snapshot, ctx)
        square_off = self._is_square_off(snapshot, ctx)

        if ctx.position is not None:
            opt = snapshot.option(ctx.position.strike, ctx.position.side)
            target = ctx.position.entry_price + ctx.params["target_pts"]
            stop = ctx.position.entry_price - ctx.params["sl_pts"]

            if square_off:
                exit_px = opt.close if opt.close is not None else ctx.position.entry_price
                self._compound_on_exit(exit_px, ctx, "square-off")
                self._last_exit_time = snapshot.timestamp
                return ctx.exit(reason="square-off (EOD)")

            if opt.low is not None and opt.low <= stop:
                self._compound_on_exit(stop, ctx, "SL")
                self._last_exit_time = snapshot.timestamp
                return ctx.exit(reason=f"SL hit (low {opt.low:.1f} <= {stop:.1f})")
            if opt.high is not None and opt.high >= target:
                self._compound_on_exit(target, ctx, "target")
                self._last_exit_time = snapshot.timestamp
                return ctx.exit(reason=f"target hit (high {opt.high:.1f} >= {target:.1f})")
            return ctx.hold()

        if self._pending_limit is not None:
            self._pending_age += 1

            if past_cutoff or square_off:
                lim = self._pending_limit
                self._clear_pending()
                tag = "after 3:10 entry cutoff" if past_cutoff else "square-off"
                return ctx.skip(reason=f"cancel limit {lim} ({tag})")

            if self._pending_age > ctx.params["order_ttl_bars"]:
                lim = self._pending_limit
                self._clear_pending()
                return ctx.skip(reason=f"cancel limit {lim} (TTL {ctx.params['order_ttl_bars']} bars)")

            gap = (f - s) if (f is not None and s is not None) else 0.0
            still_valid = (
                abs(gap) >= ctx.params["ema_gap"]
                and ((gap > 0) == (self._pending_side == "CE"))
            )
            if not still_valid:
                lim = self._pending_limit
                self._clear_pending()
                return ctx.skip(reason=f"cancel limit {lim} (EMA gap {gap:+.1f} invalid)")

            opt = snapshot.option(self._pending_strike, self._pending_side)
            if opt.low is None:
                return ctx.skip(reason=f"no {self._pending_side} quote at {self._pending_strike}; waiting")

            if opt.low <= self._pending_limit:
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
                    f"(bar low {opt.low:.1f}, qty {qty}, equity Rs {self._equity:,.0f})"
                )
                return ctx.enter(
                    side=side,
                    strike=strike,
                    entry_price=fill,
                    direction="BUY",
                    qty=qty,
                    reason=f"resting limit {fill} hit; qty {qty} (Rs {self._equity:,.0f}/{fill:.1f})",
                )

            return ctx.skip(reason=f"limit {self._pending_limit} resting ({self._pending_side} low {opt.low:.1f})")

        if past_cutoff:
            return ctx.skip(reason="after 3:10 - no new orders")
        if square_off:
            return ctx.skip(reason="square-off window; no new orders")

        if self._bars_seen < ctx.params["warmup_bars"]:
            return ctx.skip(
                reason=f"warming up 1m EMAs ({self._bars_seen}/{ctx.params['warmup_bars']} bars since 9:15)"
            )

        if self._equity <= 0:
            return ctx.skip(reason=f"equity depleted ({self._equity:,.0f})")

        if self._last_exit_time is not None:
            mins = (snapshot.timestamp - self._last_exit_time).total_seconds() / 60.0
            if mins < ctx.params["cooldown_mins"]:
                return ctx.skip(reason=f"cooldown ({mins:.1f}/{ctx.params['cooldown_mins']} min)")

        if f is None or s is None:
            return ctx.skip(reason="EMAs warming up")
        gap = f - s
        if abs(gap) < ctx.params["ema_gap"]:
            return ctx.skip(reason=f"EMA gap {gap:+.1f} < {ctx.params['ema_gap']}")
        side = "CE" if gap > 0 else "PE"

        strike = snapshot.atm_strike
        opt = snapshot.option(strike, side)
        if opt.close is None:
            return ctx.skip(reason=f"no {side} quote at ATM {strike}")

        limit = self._limit_price(opt.close, ctx.params["strike_offset"])
        if limit <= 0:
            return ctx.skip(reason=f"ATM premium too low to set limit ({opt.close:.1f})")

        preview_qty = self._qty_for_equity(limit)
        self._pending_limit = limit
        self._pending_side = side
        self._pending_strike = strike
        self._pending_age = 0
        ctx.log(
            f"arm limit {limit} on {side} {strike} "
            f"(ATM {opt.close:.1f}, qty~{preview_qty}, equity Rs {self._equity:,.0f}, EMA gap {gap:+.1f})"
        )
        return ctx.skip(
            reason=f"armed {side} limit {limit} at strike {strike}; "
            f"qty~{preview_qty} (equity Rs {self._equity:,.0f})"
        )
