from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _prepare_database_url(url: str) -> tuple[str, dict]:
    if url.startswith("sqlite"):
        return url, {"check_same_thread": False}

    # Supabase dashboard strings often use postgres://; SQLAlchemy needs postgresql+psycopg2://
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://") :]
    elif url.startswith("postgresql://") and "+psycopg2" not in url.split("://", 1)[0]:
        url = "postgresql+psycopg2://" + url[len("postgresql://") :]

    # Supabase requires SSL for external hosts (Render, Vercel, etc.)
    if "sslmode=" not in url:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}sslmode=require"

    return url, {}


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    url, connect_args = _prepare_database_url(settings.database_url)
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


@lru_cache
def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def init_db() -> None:
    if not db_enabled():
        return
    try:
        from . import models_db  # noqa: F401

        Base.metadata.create_all(bind=get_engine())
        logger.info("Database tables ensured")
    except Exception:
        logger.exception("Database init failed; live dashboard will run without persistence")


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_enabled() -> bool:
    return bool(get_settings().database_url)


def database_status() -> tuple[bool, str | None]:
    settings = get_settings()
    if not settings.database_url:
        return False, "DATABASE_URL is not set"
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:
        return False, str(exc)[:240]
