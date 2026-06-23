from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class SignalRow(Base):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_date: Mapped[date] = mapped_column(Date, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    time: Mapped[str] = mapped_column(String(8))
    signal: Mapped[str] = mapped_column(String(64))
    side: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    ema_gap: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    strike: Mapped[int] = mapped_column(Integer)
    option_ltp: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    market_regime: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    nifty_spot: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pcr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TradeRow(Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_date: Mapped[date] = mapped_column(Date, index=True)
    entry_time: Mapped[str] = mapped_column(String(8))
    exit_time: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    contract: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[int] = mapped_column(Integer)
    lots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    result: Mapped[str] = mapped_column(String(16))
    pnl: Mapped[float] = mapped_column(Float)
    target_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trail_stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    regime_at_entry: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
