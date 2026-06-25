from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


Timeframe = Literal["1m", "3m", "5m"]
SessionMode = Literal["running", "paused"]
SignalStatus = Literal["Taken", "Skipped"]
TradeResult = Literal["Target", "Stop", "Trail", "Time Exit", "Open"]
OptionSide = Literal["CE", "PE"]
ExitMode = Literal["fast_scalp", "balanced"]
ExpiryDayPolicy = Literal["conservative", "aggressive"]
ReplayMode = Literal["core", "full_context"]
VwapLabel = Literal["VWAP", "TWAP"]


class StrategySettings(BaseModel):
    capital_budget: float = Field(100000, ge=0)
    daily_risk: float = Field(100000, ge=0)
    per_trade_risk_cap: float = Field(100000, ge=0)
    use_full_capital: bool = False
    lots_per_trade: int = Field(1, ge=1)
    target_rupees: float = Field(2, gt=0)
    stop_loss_rupees: float = Field(10, gt=0)
    ema_gap_min_points: float = Field(3, ge=0)
    min_candle_body_ratio: float = Field(0.5, ge=0, le=1)
    max_trades_per_day: int = Field(9999, ge=1)
    max_consecutive_losses: int = Field(2, ge=1)
    timeframe: Timeframe = "5m"
    trade_start: str = "09:30"
    trade_end: str = "14:30"
    square_off_time: str = "15:15"
    ema_fast: int = Field(9, ge=2)
    ema_slow: int = Field(20, ge=3)
    macd_fast: int = Field(12, ge=2)
    macd_slow: int = Field(26, ge=3)
    macd_signal_period: int = Field(9, ge=2)
    min_ema_sep_pct: float = Field(0.0001, ge=0)
    min_ema_slope_pts: float = Field(3.0, ge=0)
    sl_pct: float = Field(0.30, gt=0, le=1)
    target_pct: float = Field(0.60, gt=0)
    target_pct_enabled: bool = True
    trail_trigger_pct: float = Field(0.25, ge=0)
    trail_gap_pct: float = Field(0.15, ge=0)
    use_signal_exit: bool = True
    time_stop_candles: int = Field(2, ge=1)
    reentry_cooldown_candles: int = Field(1, ge=0)
    fill_slippage_rupees: float = Field(0, ge=0)
    exit_slippage_rupees: float = Field(0, ge=0)
    brokerage_per_lot_round_trip: float = Field(40, ge=0)
    max_bid_ask_spread: float = Field(1.5, ge=0)
    spread_filter_enabled: bool = True
    vix_filter_enabled: bool = True
    cooldown_enabled: bool = False
    chain_staleness_seconds: int = Field(75, ge=1)
    replay_mode: ReplayMode = "full_context"
    option_chain_window: int = Field(10, ge=1)
    wall_headroom_points: float = Field(12, ge=0)
    wall_break_lookback: int = Field(3, ge=1)
    strong_trend_gap: float = Field(8, ge=0)
    pin_band_points: float = Field(15, ge=0)
    gamma_range_atr_ratio: float = Field(0.7, ge=0)
    pcr_filter_enabled: bool = True
    pcr_ce_block: float = Field(0.7, ge=0)
    pcr_pe_block: float = Field(1.3, ge=0)
    reversal_enabled: bool = False
    dynamic_exits_enabled: bool = False
    target_trend_multiplier: float = Field(2.5, gt=0)
    stop_trend_multiplier: float = Field(1.0, gt=0)
    trail_enabled: bool = False
    trail_trigger_rupees: float = Field(2.0, ge=0)
    trail_distance_rupees: float = Field(2.0, ge=0)
    max_india_vix: float = Field(22.0, ge=0)
    exit_mode: ExitMode = "fast_scalp"
    expiry_day_policy: ExpiryDayPolicy = "aggressive"
    expiry_day_entry_cutoff: str = "10:45"
    atm_source: Literal["spot", "futures"] = "spot"
    expiry_rule: Literal["current_weekly", "next_weekly_on_expiry"] = "current_weekly"


class MarketState(BaseModel):
    timestamp: datetime
    session_mode: SessionMode
    market_clock: str
    trade_window_open: bool
    nifty_spot: float
    ema_9: float
    ema_15: float
    ema_gap: float
    vwap: Optional[float] = None
    vwap_label: Optional[VwapLabel] = None
    call_wall: Optional[float] = None
    put_wall: Optional[float] = None
    pin_strike: Optional[float] = None
    pcr: Optional[float] = None
    gamma_regime: Optional[str] = None
    market_regime: Optional[str] = None
    gamma_flip: Optional[float] = None
    trade_allowed: bool
    preferred_side: Optional[OptionSide]
    atm_strike: int
    atm_ce_ltp: float
    atm_pe_ltp: float
    open_position: Optional[str]
    broker: str
    data_mode: Literal["demo", "dhan"]
    feed_status: Literal["live", "stale", "demo"] = "demo"
    feed_message: Optional[str] = None
    option_expiry: Optional[str] = None
    open_position_detail: Optional[str] = None
    trades_today: int = 0
    remaining_daily_budget: float = 0.0
    session_halted: bool = False
    halt_reason: Optional[str] = None


class Signal(BaseModel):
    id: str
    timestamp: datetime
    time: str
    signal: str
    side: Optional[OptionSide]
    ema_gap: float
    status: SignalStatus
    reason: str
    strike: int
    option_ltp: Optional[float]


class Trade(BaseModel):
    id: str
    entry_time: str
    exit_time: Optional[str]
    contract: str
    side: OptionSide
    quantity: int
    entry_price: float
    exit_price: Optional[float]
    result: TradeResult
    pnl: float
    target_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_stop_price: Optional[float] = None
    trail_armed: Optional[bool] = None


class Summary(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_pnl: float
    max_drawdown: float
    affordable_lots: int
    lot_size: int


class DashboardPayload(BaseModel):
    settings: StrategySettings
    state: MarketState
    summary: Summary
    signals: list[Signal]
    trades: list[Trade]


class BacktestRunRequest(BaseModel):
    settings: StrategySettings
    from_date: str
    to_date: str
    cost_preset: str = "base"
