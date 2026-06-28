class Strategy:
    def setup(self, ctx):
        ctx.params["oi_jump"] = 1.10   # require 10% OI build-up to enter

    def on_bar(self, snapshot, ctx):
        # exit logic first
        if ctx.position is not None:
            pnl_pts = snapshot.option(ctx.position.strike, ctx.position.side).close \
                      - ctx.position.entry_price
            if pnl_pts >= 20 or pnl_pts <= -10:
                return ctx.exit(reason=f"target/stop hit ({pnl_pts:.1f} pts)")
            return ctx.hold()

        # entry logic: buy ATM CE if OI building on CE
        atm_ce = snapshot.option(snapshot.atm_strike, "CE")
        if atm_ce.oi_chg is None or atm_ce.oi_chg <= 0:
            return ctx.skip(reason="ATM CE OI not building")
        if snapshot.spot <= snapshot.option(snapshot.atm_strike, "CE").open:
            return ctx.skip(reason="spot below entry threshold")

        return ctx.enter(side="CE", strike="ATM", direction="BUY",
                         qty=1, reason="ATM CE OI build-up + spot strength")
