"""Outbox queue and idempotency tracking for upstream Official Grimmory calls."""

import json
import logging
import uuid
from typing import Any

from grimmlink_adapter.models.internal import OutboxAction
from grimmlink_adapter.state.database import get_connection

logger = logging.getLogger(__name__)


class IdempotencyManager:
    """Manages request idempotency keys to prevent duplicate execution across retries."""

    @staticmethod
    async def get_response(idempotency_key: str) -> tuple[int, str] | None:
        """Look up recorded response for an idempotency key."""
        async with get_connection() as conn, conn.execute(
            """
                SELECT response_status, response_body
                FROM idempotency_keys
                WHERE idempotency_key = ?
                """,
            (idempotency_key,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return (row["response_status"], row["response_body"])
        return None

    @staticmethod
    async def record_response(
        idempotency_key: str,
        action: str,
        response_status: int,
        response_body: str,
    ) -> None:
        """Store response for an idempotency key."""
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO idempotency_keys (
                    idempotency_key, action, response_status, response_body, created_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (idempotency_key, action, response_status, response_body),
            )
            await conn.commit()


class OutboxManager:
    """Outbox queue for reliable asynchronous dispatch to Official Grimmory."""

    @staticmethod
    async def enqueue(action_type: str, payload: dict[str, Any], idempotency_key: str | None = None) -> str:
        """Enqueue an action for upstream dispatch."""
        item_id = str(uuid.uuid4())
        if idempotency_key is None:
            idempotency_key = f"{action_type}:{item_id}"

        payload_json = json.dumps(payload)
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO outbox (
                    id, action_type, payload, idempotency_key, status, retry_count, max_retries, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'PENDING', 0, 5, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (item_id, action_type, payload_json, idempotency_key),
            )
            await conn.commit()
        return item_id

    @staticmethod
    async def get_pending(limit: int = 20) -> list[OutboxAction]:
        """Fetch pending actions sorted by creation time."""
        results: list[OutboxAction] = []
        async with get_connection() as conn:
            async with conn.execute(
                """
                SELECT id, action_type, payload, idempotency_key, status, retry_count, max_retries, last_error, created_at, updated_at
                FROM outbox
                WHERE status = 'PENDING' AND retry_count < max_retries
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                for row in await cursor.fetchall():
                    results.append(
                        OutboxAction(
                            id=row["id"],
                            action_type=row["action_type"],
                            payload=json.loads(row["payload"]),
                            idempotency_key=row["idempotency_key"],
                            status=row["status"],
                            retry_count=row["retry_count"],
                            max_retries=row["max_retries"],
                            last_error=row["last_error"],
                            created_at=row["created_at"],
                            updated_at=row["updated_at"],
                        )
                    )
        return results

    @staticmethod
    async def get_by_idempotency_key(idempotency_key: str) -> OutboxAction | None:
        """Return an existing action so a retry reuses the same outbox item."""
        async with get_connection() as conn:
            async with conn.execute(
                """
                SELECT id, action_type, payload, idempotency_key, status, retry_count,
                       max_retries, last_error, created_at, updated_at
                FROM outbox WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return OutboxAction(
            id=row["id"], action_type=row["action_type"], payload=json.loads(row["payload"]),
            idempotency_key=row["idempotency_key"], status=row["status"],
            retry_count=row["retry_count"], max_retries=row["max_retries"],
            last_error=row["last_error"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    async def requeue(item_id: str) -> None:
        """Make a previously exhausted action eligible for an explicit retry."""
        async with get_connection() as conn:
            await conn.execute(
                "UPDATE outbox SET status = 'PENDING', retry_count = 0, last_error = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (item_id,),
            )
            await conn.commit()

    @staticmethod
    async def mark_completed(item_id: str) -> None:
        """Mark outbox action as completed."""
        async with get_connection() as conn:
            await conn.execute(
                "UPDATE outbox SET status = 'COMPLETED', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (item_id,),
            )
            await conn.commit()

    @staticmethod
    async def mark_failed(item_id: str, error: str) -> None:
        """Record error and increment retry count; mark failed if max retries exceeded."""
        async with get_connection() as conn:
            await conn.execute(
                """
                UPDATE outbox
                SET retry_count = retry_count + 1,
                    last_error = ?,
                    status = CASE WHEN retry_count + 1 >= max_retries THEN 'FAILED' ELSE 'PENDING' END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error, item_id),
            )
            await conn.commit()
