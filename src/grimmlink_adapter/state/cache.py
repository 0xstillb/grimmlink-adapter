"""Local SQLite cache managers for book hashes, tokens, and multi-shelf ownership."""

import logging
from typing import Any

from grimmlink_adapter.models.internal import (
    BookHashEntry,
    CachedToken,
    ManagedFileRecord,
    MetadataDedupeRecord,
    ProgressSnapshot,
)
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


class ProgressStateCache:
    """Scoped progress snapshots for optimistic conflict checks and replay."""

    @staticmethod
    async def get(owner_key: str, book_hash: str) -> ProgressSnapshot | None:
        async with get_connection() as conn, conn.execute(
            """
            SELECT book_hash, book_id, book_file_id, format, native_location,
                   current_page, total_pages, display_percent, official_fraction,
                   device, device_id, timestamp_epoch, updated_at, source
            FROM progress_state
            WHERE owner_key = ? AND book_hash = ?
            """,
            (owner_key, book_hash),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return ProgressSnapshot(
            book_hash=row["book_hash"],
            book_id=row["book_id"],
            book_file_id=row["book_file_id"],
            format=row["format"],
            native_location=row["native_location"],
            current_page=row["current_page"],
            total_pages=row["total_pages"],
            display_percent=row["display_percent"],
            official_fraction=row["official_fraction"],
            device=row["device"],
            device_id=row["device_id"],
            timestamp_epoch=row["timestamp_epoch"],
            updated_at=row["updated_at"],
            source=row["source"],
        )

    @staticmethod
    async def put(owner_key: str, snapshot: ProgressSnapshot) -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO progress_state (
                    owner_key, book_hash, book_id, book_file_id, format,
                    native_location, current_page, total_pages, display_percent,
                    official_fraction, device, device_id, timestamp_epoch,
                    updated_at, source, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(owner_key, book_hash) DO UPDATE SET
                    book_id = excluded.book_id,
                    book_file_id = excluded.book_file_id,
                    format = excluded.format,
                    native_location = excluded.native_location,
                    current_page = excluded.current_page,
                    total_pages = excluded.total_pages,
                    display_percent = excluded.display_percent,
                    official_fraction = excluded.official_fraction,
                    device = excluded.device,
                    device_id = excluded.device_id,
                    timestamp_epoch = excluded.timestamp_epoch,
                    updated_at = excluded.updated_at,
                    source = excluded.source,
                    cached_at = CURRENT_TIMESTAMP
                """,
                (
                    owner_key,
                    snapshot.book_hash,
                    snapshot.book_id,
                    snapshot.book_file_id,
                    snapshot.format,
                    snapshot.native_location,
                    snapshot.current_page,
                    snapshot.total_pages,
                    snapshot.display_percent,
                    snapshot.official_fraction,
                    snapshot.device,
                    snapshot.device_id,
                    snapshot.timestamp_epoch,
                    snapshot.updated_at.isoformat() if snapshot.updated_at else None,
                    snapshot.source,
                ),
            )
            await conn.commit()

    @staticmethod
    async def set_manual_status(
        owner_key: str, book_hash: str, status: str, timestamp_epoch: int,
    ) -> None:
        """Remember a manual status timestamp for future progress precedence.

        The marker is retained across progress writes so a later replay cannot
        replace a newer WebUI/manual status.
        """
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO progress_state (owner_key, book_hash, manual_status, manual_status_epoch)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(owner_key, book_hash) DO UPDATE SET
                    manual_status = excluded.manual_status,
                    manual_status_epoch = excluded.manual_status_epoch,
                    cached_at = CURRENT_TIMESTAMP
                """,
                (owner_key, book_hash, status, timestamp_epoch),
            )
            await conn.commit()

    @staticmethod
    async def set_manual_status_for_book(
        owner_key: str, book_id: int, status: str, timestamp_epoch: int,
    ) -> int:
        """Record a manual status on every cached hash for one scoped book."""
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO manual_status_state (owner_key, book_id, manual_status, manual_status_epoch)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(owner_key, book_id) DO UPDATE SET
                    manual_status = excluded.manual_status,
                    manual_status_epoch = excluded.manual_status_epoch,
                    cached_at = CURRENT_TIMESTAMP
                """,
                (owner_key, book_id, status, timestamp_epoch),
            )
            cursor = await conn.execute(
                """
                UPDATE progress_state
                SET manual_status = ?, manual_status_epoch = ?, cached_at = CURRENT_TIMESTAMP
                WHERE owner_key = ? AND book_id = ?
                """,
                (status, timestamp_epoch, owner_key, book_id),
            )
            await conn.commit()
            return int(cursor.rowcount or 0)

    @staticmethod
    async def manual_status_is_newer(
        owner_key: str, book_hash: str, progress_epoch: int | None, book_id: int | None = None,
    ) -> bool:
        async with get_connection() as conn:
            async with conn.execute(
                "SELECT manual_status_epoch FROM progress_state WHERE owner_key = ? AND book_hash = ?",
                (owner_key, book_hash),
            ) as cursor:
                hash_row = await cursor.fetchone()
            book_row = None
            if book_id is not None:
                async with conn.execute(
                    "SELECT manual_status_epoch FROM manual_status_state WHERE owner_key = ? AND book_id = ?",
                    (owner_key, book_id),
                ) as cursor:
                    book_row = await cursor.fetchone()
        epochs = [
            row["manual_status_epoch"]
            for row in (hash_row, book_row)
            if row is not None and row["manual_status_epoch"] is not None
        ]
        manual_epoch = max(epochs) if epochs else None
        return manual_epoch is not None and (progress_epoch is None or manual_epoch > progress_epoch)


class MetadataSyncStore:
    """SQLite state store for metadata remote mapping, deduplication history, and scoped cursors."""

    @staticmethod
    async def get_remote_id(
        owner_key: str, book_id: int, item_type: str, local_id_or_dedupe_key: str
    ) -> int | None:
        async with get_connection() as conn, conn.execute(
            """
            SELECT remote_id FROM metadata_remote_mappings
            WHERE owner_key = ? AND book_id = ? AND item_type = ?
              AND (local_id = ? OR dedupe_key = ?)
            """,
            (owner_key, book_id, item_type, local_id_or_dedupe_key, local_id_or_dedupe_key),
        ) as cursor:
            row = await cursor.fetchone()
            return int(row["remote_id"]) if row else None

    @staticmethod
    async def set_remote_mapping(
        owner_key: str, book_id: int, item_type: str, local_id: str, remote_id: int, dedupe_key: str
    ) -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO metadata_remote_mappings (
                    owner_key, book_id, item_type, local_id, remote_id, dedupe_key, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(owner_key, book_id, item_type, local_id) DO UPDATE SET
                    remote_id = excluded.remote_id,
                    dedupe_key = excluded.dedupe_key,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (owner_key, book_id, item_type, local_id, remote_id, dedupe_key),
            )
            await conn.commit()

    @staticmethod
    async def delete_remote_mapping(
        owner_key: str, book_id: int, item_type: str, local_id_or_dedupe_key: str
    ) -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                DELETE FROM metadata_remote_mappings
                WHERE owner_key = ? AND book_id = ? AND item_type = ?
                  AND (local_id = ? OR dedupe_key = ?)
                """,
                (owner_key, book_id, item_type, local_id_or_dedupe_key, local_id_or_dedupe_key),
            )
            await conn.commit()

    @staticmethod
    async def get_applied_record(
        owner_key: str, book_id: int, item_type: str, dedupe_key: str
    ) -> MetadataDedupeRecord | None:
        async with get_connection() as conn, conn.execute(
            """
            SELECT owner_key, book_id, item_type, dedupe_key, content_hash, device,
                   device_id, source_scale, source_value, official_value, official_id,
                   payload_json, is_deleted, synced_at
            FROM metadata_applied_history
            WHERE owner_key = ? AND book_id = ? AND item_type = ? AND dedupe_key = ?
            """,
            (owner_key, book_id, item_type, dedupe_key),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return MetadataDedupeRecord(
                    owner_key=row["owner_key"],
                    book_id=row["book_id"],
                    item_type=row["item_type"],
                    dedupe_key=row["dedupe_key"],
                    content_hash=row["content_hash"],
                    device=row["device"],
                    device_id=row["device_id"],
                    source_scale=row["source_scale"],
                    source_value=row["source_value"],
                    official_value=row["official_value"],
                    official_id=row["official_id"],
                    payload_json=row["payload_json"],
                    is_deleted=bool(row["is_deleted"]),
                    synced_at=row["synced_at"],
                )
        return None

    @staticmethod
    async def get_latest_applied_for_type(
        owner_key: str, book_id: int, item_type: str
    ) -> MetadataDedupeRecord | None:
        async with get_connection() as conn, conn.execute(
            """
            SELECT owner_key, book_id, item_type, dedupe_key, content_hash, device,
                   device_id, source_scale, source_value, official_value, official_id,
                   payload_json, is_deleted, synced_at
            FROM metadata_applied_history
            WHERE owner_key = ? AND book_id = ? AND item_type = ?
            ORDER BY synced_at DESC LIMIT 1
            """,
            (owner_key, book_id, item_type),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return MetadataDedupeRecord(
                    owner_key=row["owner_key"],
                    book_id=row["book_id"],
                    item_type=row["item_type"],
                    dedupe_key=row["dedupe_key"],
                    content_hash=row["content_hash"],
                    device=row["device"],
                    device_id=row["device_id"],
                    source_scale=row["source_scale"],
                    source_value=row["source_value"],
                    official_value=row["official_value"],
                    official_id=row["official_id"],
                    payload_json=row["payload_json"],
                    is_deleted=bool(row["is_deleted"]),
                    synced_at=row["synced_at"],
                )
        return None

    @staticmethod
    async def get_applied_by_official_id(
        owner_key: str, book_id: int, official_id: int
    ) -> MetadataDedupeRecord | None:
        async with get_connection() as conn, conn.execute(
            """
            SELECT owner_key, book_id, item_type, dedupe_key, content_hash, device,
                   device_id, source_scale, source_value, official_value, official_id,
                   payload_json, is_deleted, synced_at
            FROM metadata_applied_history
            WHERE owner_key = ? AND book_id = ? AND official_id = ?
            ORDER BY synced_at DESC LIMIT 1
            """,
            (owner_key, book_id, official_id),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return MetadataDedupeRecord(
                    owner_key=row["owner_key"],
                    book_id=row["book_id"],
                    item_type=row["item_type"],
                    dedupe_key=row["dedupe_key"],
                    content_hash=row["content_hash"],
                    device=row["device"],
                    device_id=row["device_id"],
                    source_scale=row["source_scale"],
                    source_value=row["source_value"],
                    official_value=row["official_value"],
                    official_id=row["official_id"],
                    payload_json=row["payload_json"],
                    is_deleted=bool(row["is_deleted"]),
                    synced_at=row["synced_at"],
                )
        return None

    @staticmethod
    async def record_applied(record: MetadataDedupeRecord) -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO metadata_applied_history (
                    owner_key, book_id, item_type, dedupe_key, content_hash, device,
                    device_id, source_scale, source_value, official_value, official_id,
                    payload_json, is_deleted, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(owner_key, book_id, item_type, dedupe_key) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    device = excluded.device,
                    device_id = excluded.device_id,
                    source_scale = excluded.source_scale,
                    source_value = excluded.source_value,
                    official_value = excluded.official_value,
                    official_id = excluded.official_id,
                    payload_json = excluded.payload_json,
                    is_deleted = excluded.is_deleted,
                    synced_at = CURRENT_TIMESTAMP
                """,
                (
                    record.owner_key,
                    record.book_id,
                    record.item_type,
                    record.dedupe_key,
                    record.content_hash,
                    record.device,
                    record.device_id,
                    record.source_scale,
                    record.source_value,
                    record.official_value,
                    record.official_id,
                    record.payload_json,
                    1 if record.is_deleted else 0,
                ),
            )
            await conn.commit()

    @staticmethod
    async def mark_applied_deleted(
        owner_key: str, book_id: int, item_type: str, dedupe_key: str
    ) -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                UPDATE metadata_applied_history
                SET is_deleted = 1, synced_at = CURRENT_TIMESTAMP
                WHERE owner_key = ? AND book_id = ? AND item_type = ? AND dedupe_key = ?
                """,
                (owner_key, book_id, item_type, dedupe_key),
            )
            await conn.commit()

    @staticmethod
    async def get_scoped_cursor(
        server: str, owner_key: str, book_id: int, book_file_id: int, item_type: str
    ) -> str | None:
        async with get_connection() as conn, conn.execute(
            """
            SELECT last_cursor FROM scoped_metadata_cursors
            WHERE server = ? AND owner_key = ? AND book_id = ?
              AND book_file_id = ? AND item_type = ?
            """,
            (server, owner_key, book_id, book_file_id, item_type),
        ) as cursor:
            row = await cursor.fetchone()
            return str(row["last_cursor"]) if row else None

    @staticmethod
    async def set_scoped_cursor(
        server: str, owner_key: str, book_id: int, book_file_id: int, item_type: str, last_cursor: str
    ) -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO scoped_metadata_cursors (
                    server, owner_key, book_id, book_file_id, item_type, last_cursor, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(server, owner_key, book_id, book_file_id, item_type) DO UPDATE SET
                    last_cursor = excluded.last_cursor,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (server, owner_key, book_id, book_file_id, item_type, last_cursor),
            )
            legacy_key = f"{server}:{owner_key}:{book_id}:{book_file_id}:{item_type}"
            await conn.execute(
                """
                INSERT INTO metadata_cursors (cursor_key, last_cursor, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(cursor_key) DO UPDATE SET
                    last_cursor = excluded.last_cursor,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (legacy_key, last_cursor),
            )
            await conn.commit()

    @staticmethod
    async def list_applied_history(
        owner_key: str,
        book_id: int,
        since: str | None = None,
        limit: int = 50,
        item_type: str | None = None,
        exclude_device_id: str | None = None,
    ) -> list[MetadataDedupeRecord]:
        query = [
            "SELECT owner_key, book_id, item_type, dedupe_key, content_hash, device,",
            "       device_id, source_scale, source_value, official_value, official_id,",
            "       payload_json, is_deleted, synced_at",
            "FROM metadata_applied_history",
            "WHERE owner_key = ? AND book_id = ? AND is_deleted = 0",
        ]
        params: list[Any] = [owner_key, book_id]
        if item_type:
            query.append("AND item_type = ?")
            params.append(item_type)
        if since:
            query.append("AND synced_at >= ?")
            params.append(since)
        if exclude_device_id:
            query.append("AND (device_id IS NULL OR device_id != ?)")
            params.append(exclude_device_id)
        query.append("ORDER BY synced_at ASC LIMIT ?")
        params.append(limit)

        sql = " ".join(query)
        results: list[MetadataDedupeRecord] = []
        async with get_connection() as conn, conn.execute(sql, tuple(params)) as cursor:
            for row in await cursor.fetchall():
                results.append(
                    MetadataDedupeRecord(
                        owner_key=row["owner_key"],
                        book_id=row["book_id"],
                        item_type=row["item_type"],
                        dedupe_key=row["dedupe_key"],
                        content_hash=row["content_hash"],
                        device=row["device"],
                        device_id=row["device_id"],
                        source_scale=row["source_scale"],
                        source_value=row["source_value"],
                        official_value=row["official_value"],
                        official_id=row["official_id"],
                        payload_json=row["payload_json"],
                        is_deleted=bool(row["is_deleted"]),
                        synced_at=row["synced_at"],
                    )
                )
        return results
