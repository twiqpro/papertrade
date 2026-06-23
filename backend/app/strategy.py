from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from math import floor
from uuid import uuid4

from .models import Signal, StrategySettings, Trade


LOT_SIZE = 65


@dataclass
class DemoMarket:
    nifty_spot: float = 23486.25
    ema_9: float = 23481.8
    ema_15: float = 23474.4
    atm_ce_ltp: float = 113.3
    atm_pe_ltp: float = 96.75


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def is_trade_window_open(now: datetime, settings: StrategySettings) -> bool:
    return True


def nearest_nifty_strike(spot: float) -> int:
    return int(round(spot / 50) * 50)


def affordable_lots(capital: float, option_ltp: float) -> int:
    if option_ltp <= 0:
        return 0
    return max(0, floor(capital / (option_ltp * LOT_SIZE)))


def build_signal(now: datetime, settings: StrategySettings, market: DemoMarket) -> Signal:
    """Legacy demo-path signal. Live Dhan path uses signal_engine.evaluate_entry_signal."""
    ema_gap = abs(market.ema_9 - market.ema_15)
    strike = nearest_nifty_strike(market.nifty_spot)
    gap_ok = ema_gap >= settings.ema_gap_min_points
    side = "CE" if market.ema_9 > market.ema_15 else "PE"
    option_ltp = market.atm_ce_ltp if side == "CE" else market.atm_pe_ltp

    if not gap_ok:
        return Signal(
            id=str(uuid4()),
            timestamp=now,
            time=now.strftime("%H:%M"),
            signal="EMA trend check",
            side=side,
            ema_gap=ema_gap,
            status="Skipped",
            reason="EMA gap below threshold",
            strike=strike,
            option_ltp=option_ltp,
        )

    return Signal(
        id=str(uuid4()),
        timestamp=now,
        time=now.strftime("%H:%M"),
        signal="ATM entry",
        side=side,
        ema_gap=ema_gap,
        status="Taken",
        reason="EMA direction and gap confirmed",
        strike=strike,
        option_ltp=option_ltp,
    )


def demo_trades(settings: StrategySettings) -> list[Trade]:
    qty = max(LOT_SIZE, affordable_lots(settings.capital_budget, 112.4) * LOT_SIZE)
    qty = min(qty, settings.max_trades_per_day * LOT_SIZE)
    return [
        Trade(
            id="demo-1",
            entry_time="09:38",
            exit_time="09:40",
            contract="NIFTY 23500 CE",
            side="CE",
            quantity=qty,
            entry_price=108.2,
            exit_price=110.2,
            result="Target",
            pnl=settings.target_rupees * qty,
        ),
        Trade(
            id="demo-2",
            entry_time="09:57",
            exit_time="10:02",
            contract="NIFTY 23500 PE",
            side="PE",
            quantity=qty,
            entry_price=101.8,
            exit_price=91.8,
            result="Stop",
            pnl=-settings.stop_loss_rupees * qty,
        ),
    ]

