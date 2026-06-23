from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _parse_postgres_url(url: str) -> URL:
    """Parse postgres URLs; passwords may contain @ when split from the right."""
    raw = url
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    elif raw.startswith("postgresql+psycopg2://"):
        raw = "postgresql://" + raw[len("postgresql+psycopg2://") :]
    elif not raw.startswith("postgresql://"):
        raise ValueError("Unsupported database URL scheme")

    base, _, query = raw.partition("?")
    _, _, rest = base.partition("://")
    at_idx = rest.rfind("@")
    if at_idx == -1:
        raise ValueError("DATABASE_URL must include user@host (or set DATABASE_PASSWORD separately)")

    credentials = rest[:at_idx]
    location = rest[at_idx + 1 :]
    user, _, password = credentials.partition(":")

    slash_idx = location.find("/")
    if slash_idx == -1:
        host_port = location
        database = "postgres"
    else:
        host_port = location[:slash_idx]
        database = location[slash_idx + 1 :] or "postgres"

    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 5432

    query_dict: dict[str, str] = {}
    if query:
        for part in query.split("&"):
            if "=" in part:
                key, value = part.split("=", 1)
                query_dict[key] = value
    if "sslmode" not in query_dict:
        query_dict["sslmode"] = "require"

    return URL.create(
        drivername="postgresql+psycopg2",
        username=user or None,
        password=password or None,
        host=host,
        port=port,
        database=database,
        query=query_dict,
    )


def _prepare_database_url(url: str) -> tuple[str, dict]:
    if url.startswith("sqlite"):
        return url, {"check_same_thread": False}

    sql_url = _parse_postgres_url(url)
    override_password = get_settings().database_password
    if override_password:
        sql_url = sql_url.set(password=override_password)

    return sql_url.render_as_string(hide_password=False), {}


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
