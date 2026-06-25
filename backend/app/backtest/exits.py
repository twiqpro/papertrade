from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..models import StrategySettings
from ..signal_engine import OptionQuote
from ..strategy_module.base import ExitDecision, Position
from ..strategy_module.v1_nifty_atm import NiftyAtmStrategyV1


def evaluate_bar_exit(
    strategy: NiftyAtmStrategyV1,
    timestamp: datetime,
    position: Position,
    bar_open: float,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    quote: OptionQuote,
    market_context,
    settings: StrategySettings,
) -> Optional[ExitDecision]:
    """Bar-based exit check (stop before target on same bar)."""
    return strategy.evaluate_exit(
        timestamp,
        position,
        bar_high,
        bar_low,
        bar_close,
        quote,
        market_context,
        settings,
    )
