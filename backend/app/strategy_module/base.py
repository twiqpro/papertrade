from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional, Protocol

from ..models import OptionSide, StrategySettings, TradeResult
from ..signal_engine import MarketContext, OptionQuote


@dataclass
class AccountState:
    has_open_position: bool = False
    remaining_daily_budget: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    halted: bool = False
    cooldown_until_bar: int = -1
    bar_index: int = 0
    session_equity: float = 0.0


@dataclass
class Decision:
    timestamp: datetime
    strategy_id: str
    strategy_version: str
    status: Literal["Taken", "Skipped"]
    side: Optional[OptionSide]
    expiry: str
    strike: int
    signal_layer: str
    reason: str
    ema_gap: float
    ema_9: float
    ema_15: float
    spot: float
    vwap: float
    vwap_label: str
    atr_14: float
    session_high: float
    session_low: float
    market_regime: str
    call_wall: float
    put_wall: float
    pin_strike: float
    pcr: float
    gamma_flip: float
    gamma_regime: str
    india_vix: Optional[float]
    atm_ce_price: float
    atm_pe_price: float
    option_ltp: Optional[float]
    lots: int = 0
    data_timestamps: dict = field(default_factory=dict)
    data_quality_flags: list[str] = field(default_factory=list)
    signal_id: str = ""


@dataclass
class Position:
    id: str
    side: OptionSide
    strike: int
    expiry: str
    quantity: int
    lots: int
    entry_price: float
    entry_time: datetime
    target_price: float
    base_stop_price: float
    time_stop_at: datetime
    trail_enabled: bool
    peak_ltp: float
    trail_armed: bool
    trail_stop_price: Optional[float]
    regime_at_entry: str
    signal_time: Optional[datetime] = None
    index_entry: Optional[float] = None
    index_stop: Optional[float] = None
    index_risk: Optional[float] = None
    index_be_armed: bool = False


@dataclass
class ExitDecision:
    result: TradeResult
    exit_price: float
    exit_time: datetime
    reason: str


class Strategy(Protocol):
    strategy_id: str
    strategy_version: str

    def required_features(self) -> set[str]:
        ...

    def evaluate_entry(
        self,
        timestamp: datetime,
        market_context: MarketContext,
        account_state: AccountState,
        settings: StrategySettings,
    ) -> Decision:
        ...

    def create_position(
        self,
        decision: Decision,
        execution_quote: OptionQuote,
        settings: StrategySettings,
        context: MarketContext,
    ) -> Position:
        ...

    def evaluate_exit(
        self,
        timestamp: datetime,
        position: Position,
        option_bar_high: float,
        option_bar_low: float,
        option_bar_close: float,
        option_quote: OptionQuote,
        market_context: MarketContext,
        settings: StrategySettings,
    ) -> Optional[ExitDecision]:
        ...


def strategy_hash(strategy_id: str, strategy_version: str, settings: StrategySettings) -> str:
    payload = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "settings": json.loads(settings.model_dump_json()),
    }
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
