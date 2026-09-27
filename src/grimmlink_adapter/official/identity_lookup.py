"""Exact, read-only hash lookup in the stock Grimmory MariaDB schema."""

from __future__ import annotations

import asyncio
import logging
import ssl
from dataclasses import dataclass
from typing import Any, cast

import aiomysql  # type: ignore[import-untyped]

from grimmlink_adapter.config import Settings, settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GrimmoryIdentityCandidate:
    """Hash-to-book identity fields read from Grimmory's database."""

    book_id: int
    book_file_id: int
    current_hash: str
    initial_hash: str


class GrimmoryIdentityLookupError(Exception):
    """The configured read-only identity source could not be queried safely."""


_CURRENT_HASH_QUERY = """
SELECT b.id AS book_id,
       bf.id AS book_file_id,
       bf.current_hash,
       bf.initial_hash
FROM book_file AS bf
JOIN book AS b ON b.id = bf.book_id
JOIN library AS l ON l.id = b.library_id
JOIN library_path AS lp ON lp.id = b.library_path_id
WHERE bf.current_hash = %s
  AND bf.is_book = 1
  AND (b.deleted IS NULL OR b.deleted = 0)
"""

_INITIAL_HASH_QUERY = """
SELECT b.id AS book_id,
       bf.id AS book_file_id,
       bf.current_hash,
       bf.initial_hash
FROM book_file AS bf
JOIN book AS b ON b.id = bf.book_id
JOIN library AS l ON l.id = b.library_id
JOIN library_path AS lp ON lp.id = b.library_path_id
WHERE bf.initial_hash = %s
  AND bf.is_book = 1
  AND (b.deleted IS NULL OR b.deleted = 0)
"""


class GrimmoryIdentityLookup:
    """Query exact hashes using a dedicated, externally provisioned read-only DB user.

    This class issues SELECT statements only. The MariaDB account must also be
    restricted to SELECT on book, book_file, library, and library_path.
    """

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or settings

    @property
    def enabled(self) -> bool:
        return self.config.GRIMMORY_DB_ENABLED

    async def lookup_current_hash(self, book_hash: str) -> list[GrimmoryIdentityCandidate]:
        """Return every active book row matching current_hash exactly."""
        return await self._query(_CURRENT_HASH_QUERY, book_hash)

    async def lookup_initial_hash(self, book_hash: str) -> list[GrimmoryIdentityCandidate]:
        """Return every active book row matching initial_hash exactly."""
        return await self._query(_INITIAL_HASH_QUERY, book_hash)

    async def _query(self, query: str, book_hash: str) -> list[GrimmoryIdentityCandidate]:
        if not self.config.GRIMMORY_DB_ENABLED:
            return []

        host = self.config.GRIMMORY_DB_HOST
        username = self.config.GRIMMORY_DB_USER
        password = self.config.GRIMMORY_DB_PASSWORD
        if not host or not username or password is None or not self.config.GRIMMORY_DB_NAME:
            raise GrimmoryIdentityLookupError(
                "Grimmory read-only DB lookup is enabled but its connection settings are incomplete"
            )

        conn: aiomysql.Connection | None = None
        try:
            ssl_context: ssl.SSLContext | None = None
            if self.config.GRIMMORY_DB_SSL_CA:
                ssl_context = ssl.create_default_context(cafile=self.config.GRIMMORY_DB_SSL_CA)
            connection = await asyncio.wait_for(
                aiomysql.connect(
                    host=host,
                    port=self.config.GRIMMORY_DB_PORT,
                    user=username,
                    password=password.get_secret_value(),
                    db=self.config.GRIMMORY_DB_NAME,
                    connect_timeout=self.config.GRIMMORY_DB_CONNECT_TIMEOUT,
                    autocommit=True,
                    charset="utf8mb4",
                    cursorclass=aiomysql.DictCursor,
                    ssl=ssl_context,
                ),
                timeout=self.config.GRIMMORY_DB_CONNECT_TIMEOUT + 1.0,
            )
            conn = connection
            rows = await asyncio.wait_for(
                self._fetch_rows(connection, query, book_hash),
                timeout=self.config.GRIMMORY_DB_QUERY_TIMEOUT,
            )
            return [self._candidate(row) for row in rows]
        except GrimmoryIdentityLookupError:
            raise
        except Exception as exc:
            # Driver exceptions can include connection details; never return them.
            logger.warning("Grimmory read-only identity lookup failed (%s)", type(exc).__name__)
            raise GrimmoryIdentityLookupError(
                "Grimmory read-only identity lookup failed"
            ) from exc
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    async def _fetch_rows(
        connection: aiomysql.Connection,
        query: str,
        book_hash: str,
    ) -> list[dict[str, Any]]:
        async with connection.cursor() as cursor:
            await cursor.execute(query, (book_hash,))
            return cast(list[dict[str, Any]], await cursor.fetchall())

    @staticmethod
    def _candidate(row: dict[str, Any]) -> GrimmoryIdentityCandidate:
        try:
            book_id = int(row["book_id"])
            book_file_id = int(row["book_file_id"])
            current_hash = str(row["current_hash"] or "")
            initial_hash = str(row["initial_hash"] or "")
        except (KeyError, TypeError, ValueError) as exc:
            raise GrimmoryIdentityLookupError(
                "Grimmory returned a malformed identity row"
            ) from exc
        if book_id <= 0 or book_file_id <= 0:
            raise GrimmoryIdentityLookupError("Grimmory returned an invalid book identity")
        return GrimmoryIdentityCandidate(
            book_id=book_id,
            book_file_id=book_file_id,
            current_hash=current_hash,
            initial_hash=initial_hash,
        )
