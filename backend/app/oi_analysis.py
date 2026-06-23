from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .signal_engine import CandleBar
    from .models import StrategySettings

MarketRegime = Literal["TRENDING", "RANGING", "NEUTRAL"]
GammaContext = Literal["NEG_GAMMA", "POS_GAMMA"]


@dataclass
class OiWallMap:
    call_wall: float
    put_wall: float
    pin_strike: float
    pcr: float
    total_call_oi: int
    total_put_oi: int


def build_oi_wall_map(option_chain: dict, spot: float) -> OiWallMap:
    strikes = option_chain.get("oc") or {}
    if not strikes:
        rounded = round(spot / 50) * 50
        return OiWallMap(call_wall=rounded, put_wall=rounded, pin_strike=rounded, pcr=1.0, total_call_oi=0, total_put_oi=0)

    call_candidates: list[tuple[float, int]] = []
    put_candidates: list[tuple[float, int]] = []
    pin_candidates: list[tuple[float, int]] = []
    total_call_oi = 0
    total_put_oi = 0

    for strike_key, row in strikes.items():
        strike = float(strike_key)
        ce = row.get("ce") or {}
        pe = row.get("pe") or {}
        ce_oi = int(ce.get("oi") or 0)
        pe_oi = int(pe.get("oi") or 0)
        total_call_oi += ce_oi
        total_put_oi += pe_oi
        pin_candidates.append((strike, ce_oi + pe_oi))
        if strike >= spot:
            call_candidates.append((strike, ce_oi))
        if strike <= spot:
            put_candidates.append((strike, pe_oi))

    call_wall = max(call_candidates, key=lambda item: item[1])[0] if call_candidates else spot
    put_wall = max(put_candidates, key=lambda item: item[1])[0] if put_candidates else spot
    pin_strike = max(pin_candidates, key=lambda item: item[1])[0] if pin_candidates else round(spot / 50) * 50
    pcr = (total_put_oi / total_call_oi) if total_call_oi > 0 else 1.0
    return OiWallMap(
        call_wall=call_wall,
        put_wall=put_wall,
        pin_strike=pin_strike,
        pcr=pcr,
        total_call_oi=total_call_oi,
        total_put_oi=total_put_oi,
    )


def estimate_gamma_flip(option_chain: dict, spot: float, walls: OiWallMap) -> float:
    """Proxy gamma flip from chain greeks; falls back to pin strike."""
    strikes = option_chain.get("oc") or {}
    if not strikes:
        return walls.pin_strike

    cumulative = 0.0
    flip_candidates: list[tuple[float, float]] = []
    for strike_key in sorted(strikes.keys(), key=float):
        strike = float(strike_key)
        row = strikes[strike_key]
        ce = row.get("ce") or {}
        pe = row.get("pe") or {}
        ce_gamma = float((ce.get("greeks") or {}).get("gamma") or 0)
        pe_gamma = float((pe.get("greeks") or {}).get("gamma") or 0)
        ce_oi = int(ce.get("oi") or 0)
        pe_oi = int(pe.get("oi") or 0)
        net = (ce_gamma * ce_oi) - (pe_gamma * pe_oi)
        previous = cumulative
        cumulative += net
        if previous * cumulative < 0 or (previous == 0 and cumulative != 0):
            flip_candidates.append((strike, abs(cumulative)))

    if flip_candidates:
        return min(flip_candidates, key=lambda item: item[1])[0]
    return walls.pin_strike


def range_compressed(session_high: float, session_low: float, atr_14: float, gamma_range_atr_ratio: float) -> bool:
    if atr_14 <= 0:
        return False
    realized_range = session_high - session_low
    return realized_range < (gamma_range_atr_ratio * atr_14)


def classify_regime(
    ema_gap: float,
    session_high: float,
    session_low: float,
    atr_14: float,
    strong_trend_gap: float,
    gamma_range_atr_ratio: float,
) -> MarketRegime:
    compressed = range_compressed(session_high, session_low, atr_14, gamma_range_atr_ratio)
    trending = ema_gap >= strong_trend_gap and not compressed
    if trending:
        return "TRENDING"
    if compressed:
        return "RANGING"
    return "NEUTRAL"


def gamma_context(spot: float, gamma_flip: float) -> GammaContext:
    if spot < gamma_flip:
        return "NEG_GAMMA"
    return "POS_GAMMA"


def ema9_rising(ema_9_history: list[float]) -> bool:
    if len(ema_9_history) < 3:
        return False
    return ema_9_history[-1] > ema_9_history[-3]


def ema9_falling(ema_9_history: list[float]) -> bool:
    if len(ema_9_history) < 3:
        return False
    return ema_9_history[-1] < ema_9_history[-3]


def choose_trend_side(ema_9: float, ema_15: float, ema_9_history: list[float]) -> Optional[Literal["CE", "PE"]]:
    trend_up = ema_9 > ema_15 and ema9_rising(ema_9_history)
    trend_dn = ema_9 < ema_15 and ema9_falling(ema_9_history)
    if trend_up:
        return "CE"
    if trend_dn:
        return "PE"
    return None


def wall_broken_recently(
    side: str,
    spot: float,
    walls: OiWallMap,
    candles: list[CandleBar],
    lookback: int,
) -> bool:
    if len(candles) < 2:
        return False
    recent = candles[-max(lookback, 2) :]
    prior_closes = [bar.close for bar in recent[:-1]]
    if side == "PE":
        wall = walls.put_wall
        return spot < wall and any(close >= wall for close in prior_closes)
    wall = walls.call_wall
    return spot > wall and any(close <= wall for close in prior_closes)


def headroom_ok(
    side: str,
    spot: float,
    walls: OiWallMap,
    candles: list[CandleBar],
    required: float,
    lookback: int,
) -> tuple[bool, str]:
    if side == "CE":
        room = walls.call_wall - spot
        wall_label = "Call wall"
    else:
        room = spot - walls.put_wall
        wall_label = "Put wall"

    if room >= required:
        return True, f"{wall_label} headroom {room:.1f} pts"

    if wall_broken_recently(side, spot, walls, candles, lookback):
        return True, f"{wall_label} break continuation (headroom {room:.1f} pts)"

    return False, f"{wall_label} headroom {room:.1f} pts < {required:.1f} required"


def pcr_ok(
    side: str,
    pcr: float,
    regime: MarketRegime,
    gamma: GammaContext,
    enabled: bool,
    ce_block: float,
    pe_block: float,
) -> tuple[bool, str]:
    if not enabled:
        return True, f"PCR {pcr:.2f} (filter off)"
    if regime == "TRENDING":
        return True, f"PCR {pcr:.2f} skipped (TRENDING)"
    if gamma == "NEG_GAMMA":
        return True, f"PCR {pcr:.2f} skipped (NEG_GAMMA)"

    if side == "CE" and pcr <= ce_block:
        return False, f"PCR {pcr:.2f} call-heavy — CE exhaustion (≤ {ce_block:.1f})"
    if side == "PE" and pcr >= pe_block:
        return False, f"PCR {pcr:.2f} put-heavy — PE exhaustion (≥ {pe_block:.1f})"
    return True, f"PCR {pcr:.2f} acceptable"


def pin_layer_ok(
    spot: float,
    walls: OiWallMap,
    pin_band: float,
    compressed: bool,
    regime: MarketRegime,
    gamma: GammaContext,
) -> tuple[bool, str]:
    near_pin = abs(spot - walls.pin_strike) <= pin_band
    if compressed and near_pin and regime != "TRENDING" and gamma != "NEG_GAMMA":
        return False, f"Pin strike {walls.pin_strike:.0f} within {pin_band:.0f} pts and range compressed"
    if compressed and near_pin and regime == "TRENDING":
        return True, f"Pin {walls.pin_strike:.0f} near but TRENDING — relaxed"
    if compressed and near_pin and gamma == "NEG_GAMMA":
        return True, f"Pin {walls.pin_strike:.0f} near but NEG_GAMMA — relaxed"
    return True, f"Pin strike {walls.pin_strike:.0f} clear"


def reversal_signal(
    spot: float,
    walls: OiWallMap,
    regime: MarketRegime,
    compressed: bool,
    enabled: bool,
    ce_block: float,
    pe_block: float,
    near_wall_points: float = 5.0,
) -> Optional[Literal["CE", "PE"]]:
    if not enabled or regime != "RANGING":
        return None
    at_put_wall = abs(spot - walls.put_wall) <= near_wall_points
    at_call_wall = abs(spot - walls.call_wall) <= near_wall_points
    if at_put_wall and compressed and walls.pcr <= ce_block:
        return "CE"
    if at_call_wall and compressed and walls.pcr >= pe_block:
        return "PE"
    return None


def regime_display_label(regime: MarketRegime, gamma: GammaContext) -> str:
    gamma_label = "neg-gamma" if gamma == "NEG_GAMMA" else "pos-gamma"
    return f"{regime.lower()} · {gamma_label}"
