"""SQLite connection helpers and idempotent migrations."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = Path(__file__).resolve().parent / "schema.sql"
DEFAULT_DB = ROOT / "data" / "pokemon_collection.db"


def db_path() -> Path:
    """Database file; override with POKEMON_DB=/path/to.db (tests use this)."""
    return Path(os.environ.get("POKEMON_DB", DEFAULT_DB))


def connect(path: Path | None = None) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI may run the dependency and the endpoint on different
    # worker threads; safe here because every request gets its own connection.
    con = sqlite3.connect(path or db_path(), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


# Columns added after the initial schema: (table, column, DDL). Guarded so they run once.
_EXTRA_COLUMNS: list[tuple[str, str, str]] = [
    # ("collection", "purchase_price_cad", "REAL"),
    ("cards", "is_custom", "INTEGER DEFAULT 0"),
    (
        "sets",
        "is_custom",
        "INTEGER DEFAULT 0",
    ),  # 1 = created by the user through POST /api/sets  # 1 = added by the user through POST /api/cards
]


def migrate(path: Path | None = None) -> None:
    """Apply schema.sql (all CREATE ... IF NOT EXISTS) and guarded ALTERs."""
    con = connect(path)
    try:
        con.executescript(SCHEMA.read_text(encoding="utf-8"))
        for table, column, ddl in _EXTRA_COLUMNS:
            cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        con.commit()
    finally:
        con.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request, committed on success."""
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def rows(cur: sqlite3.Cursor | list) -> list[dict]:
    return [dict(r) for r in cur]
