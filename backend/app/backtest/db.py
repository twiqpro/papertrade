from __future__ import annotations

from pathlib import Path

import duckdb

from ..config import get_settings

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def ensure_data_dirs() -> Path:
    settings = get_settings()
    root = Path(settings.data_dir)
    for sub in ("raw/dhan", "raw/csv-imports", "manifests", "exports"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def get_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open DuckDB. Always uses read-write mode — DuckDB cannot mix RO/RW on one file."""
    ensure_data_dirs()
    settings = get_settings()
    db_path = Path(settings.duckdb_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def init_backtest_db() -> None:
    conn = get_connection()
    try:
        conn.execute(_SCHEMA_PATH.read_text())
        conn.execute(
            """
            INSERT INTO lot_size_schedule (effective_from, lot_size)
            SELECT '2000-01-01', 65
            WHERE NOT EXISTS (SELECT 1 FROM lot_size_schedule)
            """
        )
    finally:
        conn.close()


def db_status() -> dict:
    settings = get_settings()
    path = Path(settings.duckdb_path)
    if not path.exists():
        return {"ok": False, "path": str(path), "message": "Database not initialized"}
    try:
        conn = get_connection(read_only=True)
        tables = conn.execute("SHOW TABLES").fetchall()
        conn.close()
        return {"ok": True, "path": str(path), "tables": [row[0] for row in tables]}
    except Exception as error:
        return {"ok": False, "path": str(path), "message": str(error)}
