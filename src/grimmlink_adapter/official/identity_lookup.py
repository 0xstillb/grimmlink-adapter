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


_FILE_ID_QUERY = _CURRENT_HASH_QUERY.replace("bf.current_hash = %s", "bf.id = %s")


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

    async def lookup_file_id(self, file_id: int) -> list[GrimmoryIdentityCandidate]:
        """Return the active primary file identity for a verified book file ID."""
        return await self._query(_FILE_ID_QUERY, file_id)

    async def diagnose(self) -> dict[str, object]:
        """Verify credentials and SELECT access to the exact schema used for hash resolution."""
        if not self.config.GRIMMORY_DB_ENABLED:
            return {
                "name": "grimmoryIdentityDatabase",
                "status": "warning",
                "summary": "Read-only Grimmory database lookup is disabled; unknown hashes cannot be resolved on first sync.",
                "action": "Enable GRIMMORY_DB_ENABLED and configure a dedicated SELECT-only account, then restart the Adapter.",
                "details": {"enabled": False},
            }

        host = self.config.GRIMMORY_DB_HOST
        username = self.config.GRIMMORY_DB_USER
        password = self.config.GRIMMORY_DB_PASSWORD
        database = self.config.GRIMMORY_DB_NAME
        if (
            not host or not host.strip() or not username or not username.strip()
            or password is None or not password.get_secret_value()
            or not database or not database.strip()
        ):
            return {
                "name": "grimmoryIdentityDatabase",
                "status": "fail",
                "summary": "Read-only database lookup is enabled, but required connection settings are incomplete.",
                "action": "Set GRIMMORY_DB_HOST, GRIMMORY_DB_NAME, GRIMMORY_DB_USER, and GRIMMORY_DB_PASSWORD, then restart the Adapter.",
                "details": {"enabled": True, "settingsComplete": False},
            }

        connection: aiomysql.Connection | None = None
        try:
            ssl_context = ssl.create_default_context(cafile=self.config.GRIMMORY_DB_SSL_CA) if self.config.GRIMMORY_DB_SSL_CA else None
            connection = await asyncio.wait_for(
                aiomysql.connect(
                    host=host,
                    port=self.config.GRIMMORY_DB_PORT,
                    user=username,
                    password=password.get_secret_value(),
                    db=database,
                    connect_timeout=self.config.GRIMMORY_DB_CONNECT_TIMEOUT,
                    autocommit=True,
                    charset="utf8mb4",
                    cursorclass=aiomysql.DictCursor,
                    ssl=ssl_context,
                ),
                timeout=self.config.GRIMMORY_DB_CONNECT_TIMEOUT + 1.0,
            )
            await asyncio.wait_for(
                self._fetch_rows(connection, _CURRENT_HASH_QUERY, "__grimmlink_diagnostic_probe__"),
                timeout=self.config.GRIMMORY_DB_QUERY_TIMEOUT,
            )
            return {
                "name": "grimmoryIdentityDatabase",
                "status": "ok",
                "summary": "Database connection and required read-only hash query succeeded.",
                "details": {
                    "enabled": True,
                    "settingsComplete": True,
                    "query": "book_file current_hash with book/library joins",
                    "writesPerformed": False,
                },
            }
        except Exception as exc:
            error_code: str | int = type(exc).__name__
            if getattr(exc, "args", None) and isinstance(exc.args[0], int):
                error_code = exc.args[0]
            if error_code in (1044, 1045, 1142, 1143):
                action = "Grant SELECT on book, book_file, library, and library_path to this account; do not grant write permissions."
                summary = "Database credentials or SELECT permissions were rejected."
            elif error_code == 1049:
                action = "Check GRIMMORY_DB_NAME against the Grimmory database name."
                summary = "The configured Grimmory database name was not found."
            elif error_code in (2003, 2005, 2006, 2013, "TimeoutError", "gaierror", "ConnectionRefusedError"):
                action = "Check the database host, port, DNS, firewall, and container network."
                summary = "The Adapter could not reach the Grimmory database."
            else:
                action = "Check connection settings, TLS configuration, and that the database schema matches Grimmory 3.5 or later."
                summary = "The read-only identity query failed."
            return {
                "name": "grimmoryIdentityDatabase",
                "status": "fail",
                "summary": summary,
                "action": action,
                "details": {"enabled": True, "settingsComplete": True, "errorCode": error_code},
            }
        finally:
            if connection is not None:
                connection.close()

    async def _query(self, query: str, book_hash: str | int) -> list[GrimmoryIdentityCandidate]:
        if not self.config.GRIMMORY_DB_ENABLED:
            return []

        host = self.config.GRIMMORY_DB_HOST
        username = self.config.GRIMMORY_DB_USER
        password = self.config.GRIMMORY_DB_PASSWORD
        if (
            not host or not host.strip() or not username or not username.strip()
            or password is None or not password.get_secret_value()
            or not self.config.GRIMMORY_DB_NAME.strip()
        ):
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
        book_hash: str | int,
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
