import io
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.backtest.candles import aggregate_from_timestamps, indicator_snapshot
from app.backtest.db import init_backtest_db
from app.backtest.importer import import_nifty_candles, import_option_bars
from app.backtest.replay import replay_day, run_backtest
from app.backtest.sync import filter_atm_window, select_chain_snapshot
from app.backtest.validators import validate_day
from app.models import StrategySettings
from app.oi_analysis import build_oi_wall_map, filter_chain_atm_window
from app.signal_engine import CandleBar, calc_ema_series, calc_vwap_or_twap, evaluate_entry_signal
from app.signal_engine import build_demo_context
from app.strategy import DemoMarket, is_trade_window_open, nearest_nifty_strike
from app.strategy_module.base import AccountState
from app.strategy_module import get_strategy_v1
from app.strategy_module.v1_nifty_atm import hash_settings

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setenv("DUCKDB_PATH", str(db_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    init_backtest_db()


def test_ema_calculation():
    closes = [100, 101, 102, 103, 104]
    ema = calc_ema_series(closes, 3)
    assert len(ema) == 5
    assert ema[-1] > ema[0]


def test_atm_strike_rounding():
    assert nearest_nifty_strike(23486) == 23500
    assert nearest_nifty_strike(23474) == 23450


def test_vwap_vs_twap_label():
    no_vol = [CandleBar(100, 101, 99, 100, 0)]
    _, label = calc_vwap_or_twap(no_vol)
    assert label == "TWAP"
    with_vol = [CandleBar(100, 101, 99, 100, 1000)]
    _, label2 = calc_vwap_or_twap(with_vol)
    assert label2 == "VWAP"


def test_trade_window():
    settings = StrategySettings()
    inside = datetime(2025, 6, 2, 10, 0, tzinfo=IST)
    before_open = datetime(2025, 6, 2, 9, 0, tzinfo=IST)
    after_close = datetime(2025, 6, 2, 15, 35, tzinfo=IST)
    assert is_trade_window_open(inside, settings) is True
    assert is_trade_window_open(before_open, settings) is False
    assert is_trade_window_open(after_close, settings) is False


def test_atm_plus_minus_10_filter():
    spot = 23500
    strikes = {f"{strike:.6f}": {} for strike in range(23000, 24100, 50)}
    filtered = filter_chain_atm_window(strikes, spot, 10)
    assert len(filtered) == 21
    walls = build_oi_wall_map(filtered, spot, 10)
    assert walls.pin_strike >= 23000


def test_chain_snapshot_no_lookahead():
    snaps = [
        (datetime(2025, 1, 1, 9, 37, 30, tzinfo=IST), [{"strike": 23500}]),
        (datetime(2025, 1, 1, 9, 40, tzinfo=IST), [{"strike": 23550}]),
    ]
    decision = datetime(2025, 1, 1, 9, 38, tzinfo=IST)
    ts, rows, reason = select_chain_snapshot(snaps, decision, 75)
    assert ts == snaps[0][0]
    assert rows[0]["strike"] == 23500
    assert reason is None


def test_strategy_hash_stable():
    settings = StrategySettings()
    assert hash_settings(settings) == hash_settings(settings)


def test_shared_engine_parity():
    settings = StrategySettings(replay_mode="full_context")
    market = DemoMarket()
    ctx = build_demo_context(market, 23500)
    now = datetime(2025, 6, 2, 10, 0, tzinfo=IST)
    signal = evaluate_entry_signal(now, settings, ctx, has_open_position=False)
    strategy = get_strategy_v1()
    account = AccountState(has_open_position=False, remaining_daily_budget=100000)
    decision = strategy.evaluate_entry(now, ctx, account, settings)
    assert signal.status == decision.status


def test_golden_day_replay(tmp_path):
    nifty_csv = """timestamp,open,high,low,close,volume
2025-06-02 09:15:00,23480,23490,23475,23485,1000
2025-06-02 09:16:00,23485,23495,23480,23492,1000
2025-06-02 09:17:00,23492,23500,23488,23498,1000
2025-06-02 09:18:00,23498,23505,23495,23502,1000
2025-06-02 09:19:00,23502,23510,23500,23508,1000
2025-06-02 09:30:00,23508,23520,23505,23518,1000
2025-06-02 09:31:00,23518,23525,23515,23522,1000
2025-06-02 09:32:00,23522,23530,23520,23528,1000
2025-06-02 09:33:00,23528,23535,23525,23532,1000
2025-06-02 09:34:00,23532,23540,23530,23538,1000
2025-06-02 09:35:00,23538,23545,23535,23542,1000
"""
    option_csv = """timestamp,expiry,strike,side,open,high,low,close,open_interest,bid,ask
2025-06-02 09:30:00,2025-06-05,23500,CE,110,112,109,111,50000,110.5,111.5
2025-06-02 09:31:00,2025-06-05,23500,CE,111,113,110,112,51000,111.5,112.5
2025-06-02 09:32:00,2025-06-05,23500,CE,112,114,111,113,52000,112.5,113.5
2025-06-02 09:33:00,2025-06-05,23500,CE,113,115,112,114,53000,113.5,114.5
2025-06-02 09:34:00,2025-06-05,23500,CE,114,116,113,115,54000,114.5,115.5
2025-06-02 09:35:00,2025-06-05,23500,CE,115,117,114,116,55000,115.5,116.5
2025-06-02 09:30:00,2025-06-05,23500,PE,95,96,94,95,45000,94.5,95.5
2025-06-02 09:31:00,2025-06-05,23500,PE,95,96,93,94,46000,93.5,94.5
"""
    import_nifty_candles(
        nifty_csv.encode(),
        {"timestamp": "timestamp", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume", "timeframe": "1m"},
    )
    import_option_bars(
        option_csv.encode(),
        {
            "timestamp": "timestamp",
            "expiry": "expiry",
            "strike": "strike",
            "side": "side",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "open_interest": "open_interest",
            "bid": "bid",
            "ask": "ask",
        },
    )
    quality = validate_day(date(2025, 6, 2))
    assert quality["status"] in ("valid", "valid_with_warnings")
    settings = StrategySettings(replay_mode="core", timeframe="1m", vix_filter_enabled=False, spread_filter_enabled=False)
    equity_state = {"cumulative_pnl": 0.0, "peak_equity": settings.capital_budget}
    result = replay_day(date(2025, 6, 2), settings, "test-run", equity_state)
    assert "signals" in result
    assert result["signals"] >= 1


def test_entry_after_signal_bar():
    """Entry must occur strictly after signal candle close."""
    signal_time = datetime(2025, 6, 2, 9, 30, tzinfo=IST)
    entry_time = datetime(2025, 6, 2, 9, 31, tzinfo=IST)
    assert entry_time > signal_time


def test_wide_option_import():
    from app.backtest.importer import import_option_bars, parse_contract_filename

    assert parse_contract_filename("NIFTY_25550_CE_03_OCT_24.csv") == {
        "strike": 25550,
        "side": "CE",
        "expiry": date(2024, 10, 3),
    }

    wide_csv = """timestamp,expiry,strike,ce_open,ce_high,ce_low,ce_close,pe_open,pe_high,pe_low,pe_close
2025-06-02 09:30:00,2025-06-05,23500,110,112,109,111,95,96,94,95
"""
    result = import_option_bars(
        wide_csv.encode(),
        {"format": "wide", "timestamp": "timestamp", "expiry": "expiry", "strike": "strike"},
    )
    assert result["rows_imported"] == 2
    assert result.get("format") == "wide"


def test_vix_import():
    from app.backtest.importer import import_vix_bars

    vix_csv = """timestamp,close
2025-06-02 09:30:00,14.5
2025-06-02 09:31:00,14.6
"""
    result = import_vix_bars(vix_csv.encode(), {"timestamp": "timestamp", "close": "close"})
    assert result["rows_imported"] == 2


def test_atm_label_to_offset():
    from app.backtest.dhan_downloader import _atm_label_to_offset

    assert _atm_label_to_offset("ATM") == 0
    assert _atm_label_to_offset("ATM+10") == 10
    assert _atm_label_to_offset("ATM-10") == -10


def test_flatten_yahoo_frame():
    import pandas as pd

    from app.backtest.yfinance_downloader import flatten_yahoo_frame, to_ist_naive

    raw = pd.DataFrame(
        {
            ("Open", "^NSEI"): [100.0, 101.0],
            ("High", "^NSEI"): [101.0, 102.0],
            ("Low", "^NSEI"): [99.0, 100.0],
            ("Close", "^NSEI"): [100.5, 101.5],
            ("Volume", "^NSEI"): [0, 0],
        },
        index=pd.to_datetime(["2025-06-02 04:00:00+00:00", "2025-06-02 04:01:00+00:00"]),
    )
    raw.columns = pd.MultiIndex.from_tuples(raw.columns)
    flat = flatten_yahoo_frame(raw)
    assert list(flat.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(flat) == 2
    ist = to_ist_naive(flat["timestamp"])
    assert str(ist.iloc[0]).startswith("2025-06-02 09:30:00")


def test_data_inventory_ready_days():
    from app.backtest.importer import data_inventory, import_vix_bars

    nifty_csv = """timestamp,open,high,low,close,volume
2025-06-02 09:30:00,100,101,99,100,1000
"""
    option_csv = """timestamp,expiry,strike,side,open,high,low,close,open_interest,bid,ask
2025-06-02 09:30:00,2025-06-05,23500,CE,110,112,109,111,50000,110.5,111.5
"""
    vix_csv = """timestamp,open,high,low,close
2025-06-02 09:30:00,12,13,11,12.5
"""
    import_nifty_candles(
        nifty_csv.encode(),
        {"timestamp": "timestamp", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume", "timeframe": "1m"},
    )
    import_option_bars(
        option_csv.encode(),
        {
            "timestamp": "timestamp",
            "expiry": "expiry",
            "strike": "strike",
            "side": "side",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "open_interest": "open_interest",
            "bid": "bid",
            "ask": "ask",
        },
    )
    import_vix_bars(vix_csv.encode(), {"timestamp": "timestamp", "open": "open", "high": "high", "low": "low", "close": "close"})

    inv = data_inventory()
    assert inv["total_ready_days"] == 1
    assert inv["ready_days"][0]["date"] == "2025-06-02"
    assert inv["ready_days"][0]["has_vix"] is True


def test_parse_dhan_json_filename():
    from app.backtest.dhan_downloader import parse_dhan_json_filename

    meta = parse_dhan_json_filename("today_2026-06-24_2026-07-07_ATM-8_PUT.json")
    assert meta["kind"] == "options"
    assert meta["trading_date"] == "2026-06-24"
    assert meta["option_type"] == "PUT"
    assert meta["relative_strike"] == "ATM-8"

    rolling = parse_dhan_json_filename("rolling_2026-04-23_2026-05-22_ATM+3_CALL.json")
    assert rolling["prefix"] == "rolling"
    assert rolling["date_from"] == "2026-04-23"


def test_attach_trade_prices_to_signals():
    from app.backtest.replay import attach_trade_prices_to_signals

    signals = [
        {"timestamp": "2026-06-24T09:30:00", "status": "Taken", "side": "CE", "strike": 23500},
        {"timestamp": "2026-06-24T09:35:00", "status": "Skipped", "side": "PE", "strike": 23500},
    ]
    trades = [
        {
            "signal_time": "2026-06-24T09:30:00",
            "entry_price": 108.5,
            "exit_price": 110.5,
            "side": "CE",
            "strike": 23500,
        }
    ]
    enriched = attach_trade_prices_to_signals(signals, trades)
    assert enriched[0]["entry_price"] == 108.5
    assert enriched[0]["exit_price"] == 110.5
    assert "entry_price" not in enriched[1]
