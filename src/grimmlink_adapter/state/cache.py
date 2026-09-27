"""Local SQLite cache managers for book hashes, tokens, and multi-shelf ownership."""

import logging

from grimmlink_adapter.models.internal import BookHashEntry, CachedToken, ManagedFileRecord
from grimmlink_adapter.state.database import get_connection

logger = logging.getLogger(__name__)


class BookHashCache:
    """Cache resolving book hashes to Official Grimmory Book IDs."""

    @staticmethod
    async def get(book_hash: str) -> BookHashEntry | None:
        """Look up book hash in cache."""
        async with get_connection() as conn:
            async with conn.execute(
                """
                SELECT book_hash, book_id, book_file_id, format, file_size, filename, title, cached_at
                FROM book_hash_cache
                WHERE book_hash = ?
                """,
                (book_hash,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return BookHashEntry(
                        book_hash=row["book_hash"],
                        book_id=row["book_id"],
                        book_file_id=row["book_file_id"],
                        format=row["format"],
                        file_size=row["file_size"],
                        filename=row["filename"],
                        title=row["title"],
                        cached_at=row["cached_at"],
                    )
        return None

    @staticmethod
    async def put(entry: BookHashEntry) -> None:
        """Insert or update book hash mapping."""
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO book_hash_cache (
                    book_hash, book_id, book_file_id, format, file_size, filename, title, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(book_hash) DO UPDATE SET
                    book_id = excluded.book_id,
                    book_file_id = excluded.book_file_id,
                    format = excluded.format,
                    file_size = excluded.file_size,
                    filename = excluded.filename,
                    title = excluded.title,
                    cached_at = CURRENT_TIMESTAMP
                """,
                (
                    entry.book_hash,
                    entry.book_id,
                    entry.book_file_id,
                    entry.format,
                    entry.file_size,
                    entry.filename,
                    entry.title,
                ),
            )
            await conn.commit()


class TokenCache:
    """Cache storing Official JWT Bearer tokens linked to GrimmLink credentials."""

    @staticmethod
    async def get(username: str) -> CachedToken | None:
        """Retrieve cached token by username."""
        async with get_connection() as conn, conn.execute(
            """
                SELECT username, password_hash, access_token, refresh_token, expires_at, updated_at
                FROM auth_tokens
                WHERE username = ?
                """,
            (username,),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row["access_token"]:
                return CachedToken(
                    username=row["username"],
                    password_hash=row["password_hash"],
                    access_token=row["access_token"],
                    refresh_token=row["refresh_token"],
                    expires_at=row["expires_at"],
                    updated_at=row["updated_at"],
                )
        return None

    @staticmethod
    async def put(token: CachedToken) -> None:
        """Save or refresh token entry."""
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO auth_tokens (
                    username, password_hash, access_token, refresh_token, expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    token.username,
                    token.password_hash,
                    token.access_token,
                    token.refresh_token,
                    token.expires_at,
                ),
            )
            await conn.commit()


class ShelfOwnershipCache:
    """Tracks composite shelf ownership (book_id, shelf_id, shelf_type) to prevent accidental file deletion."""

    @staticmethod
    async def record_ownership(book_id: int, shelf_id: int, shelf_type: str, owner_key: str = "default") -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO shelf_ownership_cache (owner_key, book_id, shelf_id, shelf_type)
                VALUES (?, ?, ?, ?)
                """,
                (owner_key, book_id, shelf_id, shelf_type),
            )
            await conn.commit()

    @staticmethod
    async def remove_ownership(book_id: int, shelf_id: int, shelf_type: str, owner_key: str = "default") -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                DELETE FROM shelf_ownership_cache
                WHERE owner_key = ? AND book_id = ? AND shelf_id = ? AND shelf_type = ?
                """,
                (owner_key, book_id, shelf_id, shelf_type),
            )
            await conn.commit()

    @staticmethod
    async def is_tracked_in_other_shelves(
        book_id: int, exclude_shelf_id: int, exclude_shelf_type: str, owner_key: str = "default",
    ) -> bool:
        """Check if a book is tracked in any shelf OTHER than the one being removed."""
        async with get_connection() as conn, conn.execute(
            """
                SELECT COUNT(*) as count
                FROM shelf_ownership_cache
                WHERE owner_key = ? AND book_id = ? AND NOT (shelf_id = ? AND shelf_type = ?)
                """,
            (owner_key, book_id, exclude_shelf_id, exclude_shelf_type),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row and row["count"] > 0)

    @staticmethod
    async def is_tracked_by_any_owner(
        book_id: int, exclude_shelf_id: int, exclude_shelf_type: str, exclude_owner_key: str,
    ) -> bool:
        """Check global local references before deleting a shared managed file."""
        async with get_connection() as conn, conn.execute(
            """
            SELECT COUNT(*) AS count FROM shelf_ownership_cache
            WHERE book_id = ?
              AND NOT (owner_key = ? AND shelf_id = ? AND shelf_type = ?)
            """,
            (book_id, exclude_owner_key, exclude_shelf_id, exclude_shelf_type),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row and row["count"] > 0)

    @staticmethod
    async def replace_shelf_ownership(
        book_ids: list[int], shelf_id: int, shelf_type: str, owner_key: str = "default",
    ) -> None:
        """Replace one shelf's cached membership only after a complete read."""
        async with get_connection() as conn:
            await conn.execute(
                "DELETE FROM shelf_ownership_cache WHERE owner_key = ? AND shelf_id = ? AND shelf_type = ?",
                (owner_key, shelf_id, shelf_type),
            )
            await conn.executemany(
                """
                INSERT OR IGNORE INTO shelf_ownership_cache (owner_key, book_id, shelf_id, shelf_type)
                VALUES (?, ?, ?, ?)
                """,
                [(owner_key, book_id, shelf_id, shelf_type) for book_id in sorted(set(book_ids))],
            )
            await conn.commit()

    @staticmethod
    async def replace_snapshot(
        snapshot: list[tuple[int, str, list[int]]], owner_key: str = "default",
    ) -> None:
        """Atomically replace all regular/magic ownership rows after a full read."""
        async with get_connection() as conn:
            await conn.execute(
                "DELETE FROM shelf_ownership_cache WHERE owner_key = ? AND shelf_type IN ('regular', 'magic')",
                (owner_key,),
            )
            rows = [
                (owner_key, book_id, shelf_id, shelf_type)
                for shelf_id, shelf_type, book_ids in snapshot
                for book_id in sorted(set(book_ids))
            ]
            if rows:
                await conn.executemany(
                    """
                    INSERT OR IGNORE INTO shelf_ownership_cache (owner_key, book_id, shelf_id, shelf_type)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )
            await conn.commit()


class ManagedFileCache:
    """Tracks adapter-owned local files without treating SQLite as source of truth."""

    @staticmethod
    async def put(record: ManagedFileRecord) -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO managed_files (
                    owner_key, book_id, book_file_id, tracked_path, downloaded_by_grimmlink,
                    provider_reference_count, expected_size, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(owner_key, book_id, book_file_id) DO UPDATE SET
                    tracked_path = excluded.tracked_path,
                    downloaded_by_grimmlink = excluded.downloaded_by_grimmlink,
                    provider_reference_count = excluded.provider_reference_count,
                    expected_size = excluded.expected_size,
                    cached_at = CURRENT_TIMESTAMP
                """,
                (
                    record.owner_key, record.book_id,
                    record.book_file_id,
                    record.tracked_path,
                    int(record.downloaded_by_grimmlink),
                    record.provider_reference_count,
                    record.expected_size,
                ),
            )
            await conn.commit()

    @staticmethod
    async def get(book_id: int, book_file_id: int | None = None, owner_key: str = "default") -> ManagedFileRecord | None:
        async with get_connection() as conn:
            if book_file_id is None:
                cursor = await conn.execute(
                    "SELECT * FROM managed_files WHERE owner_key = ? AND book_id = ? ORDER BY book_file_id LIMIT 1",
                    (owner_key, book_id),
                )
            else:
                cursor = await conn.execute(
                    "SELECT * FROM managed_files WHERE owner_key = ? AND book_id = ? AND book_file_id = ?",
                    (owner_key, book_id, book_file_id),
                )
            row = await cursor.fetchone()
            if row is None:
                return None
            return ManagedFileRecord(
                owner_key=row["owner_key"], book_id=row["book_id"],
                book_file_id=row["book_file_id"],
                tracked_path=row["tracked_path"],
                downloaded_by_grimmlink=bool(row["downloaded_by_grimmlink"]),
                provider_reference_count=row["provider_reference_count"],
                expected_size=row["expected_size"],
                cached_at=row["cached_at"],
            )

    @staticmethod
    async def get_all(book_id: int, owner_key: str = "default") -> list[ManagedFileRecord]:
        """Return every tracked local file for a book."""
        async with get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM managed_files WHERE owner_key = ? AND book_id = ? ORDER BY book_file_id",
                (owner_key, book_id),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            ManagedFileRecord(
                owner_key=row["owner_key"], book_id=row["book_id"], book_file_id=row["book_file_id"],
                tracked_path=row["tracked_path"],
                downloaded_by_grimmlink=bool(row["downloaded_by_grimmlink"]),
                provider_reference_count=row["provider_reference_count"],
                expected_size=row["expected_size"], cached_at=row["cached_at"],
            )
            for row in rows
        ]

    @staticmethod
    async def has_provider_reference_for_path(tracked_path: str) -> bool:
        """Check provider references globally for a canonical shared path."""
        async with get_connection() as conn, conn.execute(
            "SELECT COUNT(*) AS count FROM managed_files WHERE tracked_path = ? AND provider_reference_count > 0",
            (tracked_path,),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row and row["count"] > 0)

    @staticmethod
    async def remove(book_id: int, book_file_id: int = 0, owner_key: str = "default") -> None:
        async with get_connection() as conn:
            await conn.execute(
                "DELETE FROM managed_files WHERE owner_key = ? AND book_id = ? AND book_file_id = ?",
                (owner_key, book_id, book_file_id),
            )
            await conn.commit()
