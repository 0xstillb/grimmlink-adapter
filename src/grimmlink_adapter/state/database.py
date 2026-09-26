"""SQLite connection and session management using aiosqlite."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from grimmlink_adapter.config import settings

logger = logging.getLogger(__name__)

_DB_PATH: Path | None = None


def get_db_path() -> Path:
    """Get the active database file path."""
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = settings.sqlite_db_path_resolved
    return _DB_PATH


def set_db_path(path: Path) -> None:
    """Override database path (primarily used in tests)."""
    global _DB_PATH
    _DB_PATH = path
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def get_connection() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Provide an async SQLite connection with WAL mode and row factory enabled."""
    db_file = get_db_path()
    async with aiosqlite.connect(db_file) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode = WAL;")
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.execute("PRAGMA busy_timeout = 5000;")
        yield conn
