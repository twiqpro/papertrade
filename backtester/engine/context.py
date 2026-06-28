"""Strategy context: position tracking and bar actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .snapshot import STRIKE_STEP, MarketSnapshot

VALID_SIDES = {"CE", "PE"}
VALID_DIRECTIONS = {"BUY", "SELL"}


@dataclass
class Position:
    side: str
    strike: int
    direction: str
    entry_price: float
    entry_time: datetime
    qty: int


class _ActionResult:
    """Allows `return ctx.skip(...)` style in strategies."""

    def __init__(self, ctx: "Context"):
        self._ctx = ctx


class Context:
    def __init__(self, snapshot: MarketSnapshot | None = None):
        self.params: dict[str, Any] = {}
        self.position: Position | None = None
        self._snapshot = snapshot
        self._pending: dict | None = None
        self._notes: list[str] = []
        self._implicit_hold = False

    def bind_snapshot(self, snapshot: MarketSnapshot) -> None:
        self._snapshot = snapshot
        self._pending = None
        self._notes = []
        self._implicit_hold = False

    def atm_offset(self, n: int) -> int:
        if self._snapshot is None:
            raise RuntimeError("No snapshot bound")
        return self._snapshot.atm_strike + int(n) * STRIKE_STEP

    def _resolve_strike(self, strike: int | str) -> int:
        if self._snapshot is None:
            raise RuntimeError("No snapshot bound")
        if isinstance(strike, str) and strike.upper() == "ATM":
            return self._snapshot.atm_strike
        if isinstance(strike, int):
            return strike
        raise ValueError(f"Invalid strike: {strike}")

    def log(self, msg: str) -> None:
        self._notes.append(str(msg))

    def enter(
        self,
        side: str,
        strike: int | str,
        direction: str = "BUY",
        qty: int = 1,
        reason: str = "",
        entry_price: float | None = None,
        fill_price: float | None = None,
    ) -> _ActionResult:
        if self.position is not None:
            raise RuntimeError("Cannot enter: position already open")
        side = side.upper()
        direction = direction.upper()
        if side not in VALID_SIDES:
            raise ValueError(f"side must be CE or PE, got {side}")
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"direction must be BUY or SELL, got {direction}")
        resolved = self._resolve_strike(strike)
        limit = entry_price if entry_price is not None else fill_price
        self._pending = {
            "decision": "ENTER",
            "side": side,
            "direction": direction,
            "strike": resolved,
            "qty": int(qty),
            "reason": reason,
        }
        if limit is not None:
            self._pending["entry_price"] = float(limit)
        return _ActionResult(self)

    def exit(self, reason: str = "") -> _ActionResult:
        if self.position is None:
            raise RuntimeError("Cannot exit: no open position")
        self._pending = {
            "decision": "EXIT",
            "reason": reason,
        }
        return _ActionResult(self)

    def skip(self, reason: str = "") -> _ActionResult:
        self._pending = {
            "decision": "SKIP",
            "reason": reason or "skipped",
        }
        return _ActionResult(self)

    def hold(self) -> _ActionResult:
        self._pending = {
            "decision": "HOLD",
            "reason": "",
        }
        return _ActionResult(self)

    def consume_pending(self) -> dict:
        if self._pending is None:
            self._implicit_hold = True
            self._pending = {"decision": "HOLD", "reason": ""}
        action = dict(self._pending)
        action["notes"] = "; ".join(self._notes) if self._notes else ""
        action["implicit_hold"] = self._implicit_hold
        self._pending = None
        return action
