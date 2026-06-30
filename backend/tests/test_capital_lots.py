from app.models import StrategySettings
from app.signal_engine import PER_TRADE_MAX_LOSS_RS, capital_lots
from app.strategy import LOT_SIZE


def test_full_capital_respects_20k_stop_loss_cap():
    settings = StrategySettings(
        use_full_capital=True,
        capital_budget=5_000_000,
        stop_loss_rupees=20,
    )
    # Premium 100 -> capital alone would allow floor(5M / 6500) = 769 lots.
    lots = capital_lots(settings, 100.0)
    max_loss = lots * LOT_SIZE * settings.stop_loss_rupees
    assert max_loss <= PER_TRADE_MAX_LOSS_RS
    assert lots == 15  # floor(20000 / (65 * 20)) = 15


def test_full_capital_uses_capital_when_smaller_than_loss_cap():
    settings = StrategySettings(
        use_full_capital=True,
        capital_budget=100_000,
        stop_loss_rupees=20,
    )
    lots = capital_lots(settings, 150.0)
    assert lots == 10  # floor(100000 / 9750)
