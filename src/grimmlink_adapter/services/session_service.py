"""Reading session recording service with idempotency support."""

import hashlib
import logging
from typing import Any

from grimmlink_adapter.models.grimmlink import (
    GrimmlinkItemResult,
    GrimmlinkReadingSessionBatchRequest,
    GrimmlinkReadingSessionBatchResponse,
    GrimmlinkReadingSessionItemRequest,
)
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.state.outbox import IdempotencyManager

logger = logging.getLogger(__name__)


def generate_session_idempotency_key(
    username: str,
    book_id: int,
    start_time: str,
    end_time: str,
    device_id: str | None = None,
) -> str:
    """Generate canonical idempotency key for reading sessions."""
    raw = f"{username}:{book_id}:{start_time}:{end_time}:{device_id or 'default'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SessionService:
    """Manages recording and batching of reading sessions."""

    def __init__(self, official_client: OfficialGrimmoryClient | None = None) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()

    async def get_reading_sessions(self, book_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """List reading sessions for a book."""
        return []

    async def record_session(self, session: GrimmlinkReadingSessionItemRequest, username: str = "default") -> bool:
        """Record a single reading session with idempotency check."""
        key = generate_session_idempotency_key(
            username=username,
            book_id=session.bookId,
            start_time=session.startTime,
            end_time=session.endTime,
            device_id=session.deviceId,
        )
        existing = await IdempotencyManager.get_response(key)
        if existing:
            logger.debug("Idempotent session skipped for key %s", key)
            return True

        await IdempotencyManager.record_response(
            idempotency_key=key,
            action="RECORD_SESSION",
            response_status=200,
            response_body="{}",
        )
        return True

    async def record_sessions_batch(
        self,
        request: GrimmlinkReadingSessionBatchRequest,
        username: str = "default",
    ) -> GrimmlinkReadingSessionBatchResponse:
        """Record batch of reading sessions with individual idempotency checks."""
        accepted = 0
        duplicates = 0
        results: list[GrimmlinkItemResult] = []

        for item in request.sessions:
            key = generate_session_idempotency_key(
                username=username,
                book_id=item.bookId,
                start_time=item.startTime,
                end_time=item.endTime,
                device_id=item.deviceId,
            )
            existing = await IdempotencyManager.get_response(key)
            if existing:
                duplicates += 1
                results.append(GrimmlinkItemResult(itemId=key, status="DUPLICATE"))
            else:
                accepted += 1
                await IdempotencyManager.record_response(
                    idempotency_key=key,
                    action="RECORD_SESSION",
                    response_status=200,
                    response_body="{}",
                )
                results.append(GrimmlinkItemResult(itemId=key, status="SUCCESS"))

        return GrimmlinkReadingSessionBatchResponse(
            totalReceived=len(request.sessions),
            acceptedCount=accepted,
            duplicateCount=duplicates,
            results=results,
        )
