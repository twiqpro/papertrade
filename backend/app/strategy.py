from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from math import floor
from uuid import uuid4

from .models import Signal, StrategySettings, Trade


LOT_SIZE = 65
MARKET_CLOSE = time(15, 30)


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
    current = now.time()
    start = parse_hhmm(settings.trade_start)
    end = parse_hhmm(getattr(settings, "trade_end", "14:30"))
    return start <= current <= end


def is_square_off_time(now: datetime, settings: StrategySettings) -> bool:
    return now.time() >= parse_hhmm(getattr(settings, "square_off_time", "15:15"))


def is_market_close(now: datetime) -> bool:
    return now.time() >= MARKET_CLOSE


def is_expiry_entry_allowed(now: datetime, settings: StrategySettings, is_expiry_day: bool) -> bool:
    if not is_expiry_day:
        return True
    if settings.expiry_day_policy == "aggressive":
        return is_trade_window_open(now, settings)
    cutoff = parse_hhmm(settings.expiry_day_entry_cutoff)
    return now.time() <= cutoff


def nearest_nifty_strike(spot: float) -> int:
    return int(round(spot / 50) * 50)


def affordable_lots(capital: float, option_ltp: float) -> int:
    if option_ltp <= 0:
        return 0
    return max(0, floor(capital / (option_ltp * LOT_SIZE)))


def build_signal(now: datetime, settings: StrategySettings, market: DemoMarket) -> Signal:
    """Deprecated — use signal_engine.evaluate_entry_signal with MarketContext."""
    from .signal_engine import build_demo_context, evaluate_entry_signal

    strike = nearest_nifty_strike(market.nifty_spot)
    ctx = build_demo_context(market, strike)
    return evaluate_entry_signal(now, settings, ctx, has_open_position=False)


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

