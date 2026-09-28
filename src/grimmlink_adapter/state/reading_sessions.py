"""SQLite persistence and reconciliation store for reading sessions."""

from __future__ import annotations

import logging
from datetime import datetime

from grimmlink_adapter.models.internal import ReadingSessionRecord
from grimmlink_adapter.state.database import get_connection

logger = logging.getLogger(__name__)


def _parse_db_datetime(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None


class ReadingSessionStore:
    """Manages reading session state, idempotency, and reconciliation."""

    @staticmethod
    async def create_posting(record: ReadingSessionRecord) -> bool:
        """Atomically reserve a new key before any upstream mutation."""
        async with get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT OR IGNORE INTO reading_sessions_state (
                    idempotency_key, server, user_id, book_id, book_hash, book_type,
                    start_time, end_time, duration_seconds, device, device_id,
                    current_page, total_pages, start_progress, end_progress,
                    start_page, end_page, start_location, end_location, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'POSTING')
                """,
                (
                    record.idempotency_key, record.server, record.user_id, record.book_id,
                    record.book_hash, record.book_type, record.start_time, record.end_time,
                    record.duration_seconds, record.device, record.device_id,
                    record.current_page, record.total_pages, record.start_progress,
                    record.end_progress, record.start_page, record.end_page,
                    record.start_location, record.end_location,
                ),
            )
            await conn.commit()
            return cursor.rowcount == 1

    @staticmethod
    async def requeue_stale_posting(idempotency_key: str) -> bool:
        """Make an abandoned in-flight record eligible for a safe probe after a grace period."""
        async with get_connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE reading_sessions_state
                SET status = 'PENDING', updated_at = CURRENT_TIMESTAMP
                WHERE idempotency_key = ? AND status = 'POSTING'
                  AND updated_at <= datetime('now', '-2 minutes')
                """,
                (idempotency_key,),
            )
            await conn.commit()
            return cursor.rowcount == 1

    @staticmethod
    async def list_matching(
        server: str, user_id: str, book_id: int, start_time: str, end_time: str,
    ) -> list[tuple[str, str, int | None]]:
        """Find local owners of upstream IDs sharing an interval."""
        async with get_connection() as conn:
            async with conn.execute(
                """
                SELECT idempotency_key, status, official_session_id
                FROM reading_sessions_state
                WHERE server = ? AND user_id = ? AND book_id = ?
                  AND start_time = ? AND end_time = ?
                """,
                (server, user_id, book_id, start_time, end_time),
            ) as cursor:
                rows = await cursor.fetchall()
        return [(row["idempotency_key"], row["status"], row["official_session_id"]) for row in rows]

    @staticmethod
    async def record_session(record: ReadingSessionRecord) -> None:
        """Seed a state record without overwriting any existing write decision."""
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO reading_sessions_state (
                    idempotency_key, server, user_id, book_id, book_hash, book_type,
                    start_time, end_time, duration_seconds, device, device_id,
                    current_page, total_pages, start_progress, end_progress,
                    start_page, end_page, start_location, end_location,
                    official_session_id, status, retry_count, last_error,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    record.idempotency_key,
                    record.server,
                    record.user_id,
                    record.book_id,
                    record.book_hash,
                    record.book_type,
                    record.start_time,
                    record.end_time,
                    record.duration_seconds,
                    record.device,
                    record.device_id,
                    record.current_page,
                    record.total_pages,
                    record.start_progress,
                    record.end_progress,
                    record.start_page,
                    record.end_page,
                    record.start_location,
                    record.end_location,
                    record.official_session_id,
                    record.status,
                    record.retry_count,
                    record.last_error,
                ),
            )
            await conn.commit()

    @staticmethod
    async def get_by_idempotency_key(idempotency_key: str) -> ReadingSessionRecord | None:
        """Retrieve a session record by its canonical idempotency key."""
        async with get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM reading_sessions_state WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                return ReadingSessionRecord(
                    idempotency_key=row["idempotency_key"],
                    server=row["server"],
                    user_id=row["user_id"],
                    book_id=row["book_id"],
                    book_hash=row["book_hash"],
                    book_type=row["book_type"],
                    start_time=row["start_time"],
                    end_time=row["end_time"],
                    duration_seconds=row["duration_seconds"],
                    device=row["device"],
                    device_id=row["device_id"],
                    current_page=row["current_page"],
                    total_pages=row["total_pages"],
                    start_progress=row["start_progress"],
                    end_progress=row["end_progress"],
                    start_page=row["start_page"],
                    end_page=row["end_page"],
                    start_location=row["start_location"],
                    end_location=row["end_location"],
                    official_session_id=row["official_session_id"],
                    status=row["status"],
                    retry_count=row["retry_count"],
                    last_error=row["last_error"],
                    created_at=_parse_db_datetime(row["created_at"]),
                    updated_at=_parse_db_datetime(row["updated_at"]),
                )

    @staticmethod
    async def mark_committed(idempotency_key: str, official_session_id: int | None = None) -> None:
        """Mark a session as successfully committed to Official Grimmory."""
        async with get_connection() as conn:
            await conn.execute(
                """
                UPDATE reading_sessions_state
                SET status = 'COMMITTED',
                    official_session_id = COALESCE(?, official_session_id),
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE idempotency_key = ?
                """,
                (official_session_id, idempotency_key),
            )
            await conn.commit()

    @staticmethod
    async def mark_failed(idempotency_key: str, error: str) -> None:
        """Mark a session as failed or timed out, incrementing retry count."""
        async with get_connection() as conn:
            await conn.execute(
                """
                UPDATE reading_sessions_state
                SET status = 'PENDING',
                    retry_count = retry_count + 1,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE idempotency_key = ?
                """,
                (error, idempotency_key),
            )
            await conn.commit()

    @staticmethod
    async def list_pending(server: str, user_id: str, limit: int = 100) -> list[ReadingSessionRecord]:
        """Fetch pending sessions that need retry or reconciliation."""
        results: list[ReadingSessionRecord] = []
        async with get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM reading_sessions_state
                WHERE (status = 'PENDING'
                    OR (status = 'POSTING' AND updated_at <= datetime('now', '-2 minutes')))
                  AND server = ? AND user_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (server, user_id, limit),
            ) as cursor:
                for row in await cursor.fetchall():
                    results.append(
                        ReadingSessionRecord(
                            idempotency_key=row["idempotency_key"],
                            server=row["server"],
                            user_id=row["user_id"],
                            book_id=row["book_id"],
                            book_hash=row["book_hash"],
                            book_type=row["book_type"],
                            start_time=row["start_time"],
                            end_time=row["end_time"],
                            duration_seconds=row["duration_seconds"],
                            device=row["device"],
                            device_id=row["device_id"],
                            current_page=row["current_page"],
                            total_pages=row["total_pages"],
                            start_progress=row["start_progress"],
                            end_progress=row["end_progress"],
                            start_page=row["start_page"],
                            end_page=row["end_page"],
                            start_location=row["start_location"],
                            end_location=row["end_location"],
                            official_session_id=row["official_session_id"],
                            status=row["status"],
                            retry_count=row["retry_count"],
                            last_error=row["last_error"],
                            created_at=_parse_db_datetime(row["created_at"]),
                            updated_at=_parse_db_datetime(row["updated_at"]),
                        )
                    )
        return results
