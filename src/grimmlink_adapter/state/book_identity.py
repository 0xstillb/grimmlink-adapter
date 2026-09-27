"""Book identity mapping store backed by the ``book_identity`` SQLite table.

Provides server/user-scoped CRUD for the hash → (bookId, bookFileId)
resolution cache.  All queries are scoped by (server, user) to ensure
multi-account isolation.

Invariants:
  - book_file_id is NOT NULL (defaults to 0) — prevents NULL PK duplicates.
  - lookup_by_current_hash returns all matching rows to allow ambiguity detection.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from grimmlink_adapter.state.database import get_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BookIdentityRow:
    """A single row from the book_identity table."""

    server: str
    user: str
    current_hash: str
    initial_hash: str
    book_id: int
    book_file_id: int
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    format: str | None = None
    file_size: int | None = None
    filename: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> BookIdentityRow:
        """Construct from an aiosqlite Row dict."""
        authors_raw = row.get("authors")
        if isinstance(authors_raw, str):
            try:
                authors = [str(a) for a in json.loads(authors_raw)]
            except (json.JSONDecodeError, TypeError):
                authors = []
        elif isinstance(authors_raw, list):
            authors = [str(a) for a in authors_raw]
        else:
            authors = []

        return cls(
            server=str(row["server"]),
            user=str(row["user"]),
            current_hash=str(row["current_hash"]),
            initial_hash=str(row["initial_hash"]),
            book_id=int(row["book_id"]),
            book_file_id=int(row.get("book_file_id") or 0),
            title=str(row["title"]) if row.get("title") else None,
            authors=authors,
            format=str(row["format"]) if row.get("format") else None,
            file_size=int(row["file_size"]) if row.get("file_size") else None,
            filename=str(row["filename"]) if row.get("filename") else None,
        )


class BookIdentityStore:
    """CRUD operations on the ``book_identity`` table.

    Every method requires explicit (server, user) to enforce isolation.
    """

    # -- lookups ----------------------------------------------------------

    @staticmethod
    async def lookup_by_current_hash(
        server: str, user: str, current_hash: str
    ) -> list[BookIdentityRow]:
        """Primary lookup: all rows matching this currentHash (for ambiguity detection)."""
        async with get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM book_identity
                WHERE server = ? AND user = ? AND current_hash = ?
                """,
                (server, user, current_hash),
            ) as cursor:
                rows = list(await cursor.fetchall())
                return [BookIdentityRow.from_row(dict(r)) for r in rows]

    @staticmethod
    async def lookup_by_initial_hash(
        server: str, user: str, initial_hash: str
    ) -> list[BookIdentityRow]:
        """Fallback lookup: all rows sharing this initialHash."""
        async with get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM book_identity
                WHERE server = ? AND user = ? AND initial_hash = ?
                """,
                (server, user, initial_hash),
            ) as cursor:
                rows = list(await cursor.fetchall())
                return [BookIdentityRow.from_row(dict(r)) for r in rows]

    @staticmethod
    async def lookup_by_book_id(
        server: str, user: str, book_id: int
    ) -> list[BookIdentityRow]:
        """Retrieve all mappings for a specific book_id."""
        async with get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM book_identity
                WHERE server = ? AND user = ? AND book_id = ?
                """,
                (server, user, book_id),
            ) as cursor:
                rows = list(await cursor.fetchall())
                return [BookIdentityRow.from_row(dict(r)) for r in rows]

    # -- mutations --------------------------------------------------------

    @staticmethod
    async def upsert(
        *,
        server: str,
        user: str,
        current_hash: str,
        initial_hash: str,
        book_id: int,
        book_file_id: int,
        title: str | None = None,
        authors: list[str] | None = None,
        fmt: str | None = None,
        file_size: int | None = None,
        filename: str | None = None,
    ) -> None:
        """Insert or update a book identity mapping, refreshing verified_at.

        Primary key is (server, user, book_id, book_file_id).
        """
        authors_json = json.dumps(authors or [])
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO book_identity
                    (server, user, current_hash, initial_hash,
                     book_id, book_file_id, title, authors, format,
                     file_size, filename, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (server, user, book_id, book_file_id) DO UPDATE SET
                    current_hash = excluded.current_hash,
                    initial_hash = excluded.initial_hash,
                    title        = excluded.title,
                    authors      = excluded.authors,
                    format       = excluded.format,
                    file_size    = excluded.file_size,
                    filename     = excluded.filename,
                    verified_at  = CURRENT_TIMESTAMP
                """,
                (
                    server, user, current_hash, initial_hash,
                    book_id, book_file_id, title, authors_json, fmt,
                    file_size, filename,
                ),
            )
            await conn.commit()

        logger.debug(
            "Upserted book identity: %s/%s hash=%s → book=%s file=%s",
            server, user, current_hash, book_id, book_file_id,
        )

    @staticmethod
    async def invalidate(
        server: str, user: str, book_id: int, book_file_id: int,
    ) -> None:
        """Remove a stale mapping (book or file no longer accessible)."""
        async with get_connection() as conn:
            await conn.execute(
                """
                DELETE FROM book_identity
                WHERE server = ? AND user = ? AND book_id = ? AND book_file_id = ?
                """,
                (server, user, book_id, book_file_id),
            )
            await conn.commit()

        logger.info(
            "Invalidated stale book identity: %s/%s book=%s file=%s",
            server, user, book_id, book_file_id,
        )

    @staticmethod
    async def invalidate_all_for_user(server: str, user: str) -> int:
        """Remove all mappings for a server/user pair.  Returns count deleted."""
        async with get_connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM book_identity WHERE server = ? AND user = ?",
                (server, user),
            )
            await conn.commit()
            count = cursor.rowcount
        if count:
            logger.info("Invalidated %d identity mappings for %s/%s", count, server, user)
        return count
