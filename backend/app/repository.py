from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select

from .db import db_enabled, session_scope
from .models import Signal, Trade
from .models_db import SignalRow, TradeRow


IST = ZoneInfo("Asia/Kolkata")


def save_signal(
    signal: Signal,
    session_date: date,
    market_regime: Optional[str] = None,
    nifty_spot: Optional[float] = None,
    pcr: Optional[float] = None,
) -> None:
    if not db_enabled():
        return
    with session_scope() as session:
        existing = session.get(SignalRow, signal.id)
        if existing is not None:
            return
        row = SignalRow(
            id=signal.id,
            session_date=session_date,
            timestamp=signal.timestamp,
            time=signal.time,
            signal=signal.signal,
            side=signal.side,
            ema_gap=signal.ema_gap,
            status=signal.status,
            reason=signal.reason,
            strike=signal.strike,
            option_ltp=signal.option_ltp,
            market_regime=market_regime,
            nifty_spot=nifty_spot,
            pcr=pcr,
            created_at=datetime.now(IST),
        )
        session.merge(row)


def save_trade(
    trade: Trade,
    session_date: date,
    lots: Optional[int] = None,
    target_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    trail_stop_price: Optional[float] = None,
    regime_at_entry: Optional[str] = None,
) -> None:
    if not db_enabled():
        return
    if trade.result == "Open":
        return
    with session_scope() as session:
        existing = session.get(TradeRow, trade.id)
        if existing is not None:
            return
        row = TradeRow(
            id=trade.id,
            session_date=session_date,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            contract=trade.contract,
            side=trade.side,
            quantity=trade.quantity,
            lots=lots,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            result=trade.result,
            pnl=trade.pnl,
            target_price=target_price or trade.target_price,
            stop_price=stop_price or trade.stop_price,
            trail_stop_price=trail_stop_price or trade.trail_stop_price,
            regime_at_entry=regime_at_entry,
            created_at=datetime.now(IST),
        )
        session.merge(row)


def list_signals(session_date: date, limit: int = 500) -> list[Signal]:
    if not db_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(
            select(SignalRow)
            .where(SignalRow.session_date == session_date)
            .order_by(SignalRow.timestamp.desc())
            .limit(limit)
        ).all()
        return [
            Signal(
                id=row.id,
                timestamp=row.timestamp,
                time=row.time,
                signal=row.signal,
                side=row.side,  # type: ignore[arg-type]
                ema_gap=row.ema_gap,
                status=row.status,  # type: ignore[arg-type]
                reason=row.reason,
                strike=row.strike,
                option_ltp=row.option_ltp,
            )
            for row in reversed(rows)
        ]


def list_trades(session_date: date, limit: int = 200) -> list[Trade]:
    if not db_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(
            select(TradeRow)
            .where(TradeRow.session_date == session_date)
            .order_by(TradeRow.created_at.desc())
            .limit(limit)
        ).all()
        return [
            Trade(
                id=row.id,
                entry_time=row.entry_time,
                exit_time=row.exit_time,
                contract=row.contract,
                side=row.side,  # type: ignore[arg-type]
                quantity=row.quantity,
                entry_price=row.entry_price,
                exit_price=row.exit_price,
                result=row.result,  # type: ignore[arg-type]
                pnl=row.pnl,
                target_price=row.target_price,
                stop_price=row.stop_price,
                trail_stop_price=row.trail_stop_price,
            )
            for row in reversed(rows)
        ]


def daily_summary(from_date: date, to_date: date) -> list[dict]:
    if not db_enabled():
        return []
    with session_scope() as session:
        rows = session.execute(
            select(
                TradeRow.session_date,
                func.count(TradeRow.id),
                func.sum(TradeRow.pnl),
                func.sum(case((TradeRow.pnl > 0, 1), else_=0)),
                func.sum(case((TradeRow.pnl < 0, 1), else_=0)),
            )
            .where(TradeRow.session_date >= from_date, TradeRow.session_date <= to_date)
            .group_by(TradeRow.session_date)
            .order_by(TradeRow.session_date.desc())
        ).all()
        result = []
        for session_date, total, gross, wins, losses in rows:
            total = int(total or 0)
            wins = int(wins or 0)
            losses = int(losses or 0)
            result.append(
                {
                    "date": session_date.isoformat(),
                    "total_trades": total,
                    "winning_trades": wins,
                    "losing_trades": losses,
                    "win_rate": (wins / total * 100) if total else 0.0,
                    "gross_pnl": float(gross or 0),
                }
            )
        return result
