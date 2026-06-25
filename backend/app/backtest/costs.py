from __future__ import annotations

from ..models import StrategySettings


PRESETS = {
    "ideal": {"entry_slippage_rupees": 0.0, "exit_slippage_rupees": 0.0, "brokerage_per_lot_round_trip": 40.0},
    "base": {"entry_slippage_rupees": 0.5, "exit_slippage_rupees": 0.5, "brokerage_per_lot_round_trip": 40.0},
    "stress": {"entry_slippage_rupees": 1.0, "exit_slippage_rupees": 1.0, "brokerage_per_lot_round_trip": 40.0},
}


def apply_cost_preset(settings: StrategySettings, preset: str) -> StrategySettings:
    values = PRESETS.get(preset)
    if not values:
        return settings
    return settings.model_copy(update=values)


def trade_pnl(entry_price: float, exit_price: float, quantity: int, lots: int, settings: StrategySettings) -> tuple[float, float]:
    gross = (exit_price - entry_price) * quantity
    net = gross - settings.brokerage_per_lot_round_trip * lots
    return gross, net
