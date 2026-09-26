"""Reading session recording service with idempotency support."""

import hashlib
import logging
from typing import Any

from fastapi import HTTPException, status

from grimmlink_adapter.models.grimmlink import (
    GrimmlinkReadingSessionBatchRequest,
    GrimmlinkReadingSessionBatchResponse,
    GrimmlinkReadingSessionSingleRequest,
)
from grimmlink_adapter.official.client import OfficialGrimmoryClient

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
        """Reject session reads until Official session retrieval is implemented."""
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Reading-session retrieval is unavailable until Official sessions are implemented.",
        )

    async def record_session(self, session: GrimmlinkReadingSessionSingleRequest, username: str = "default") -> bool:
        """Record a single reading session.

        Mutations to Official Grimmory are disabled in Session 00 (scaffold).
        Activated in Session 08 (Reading Sessions & Idempotency).
        Idempotency keys must not be recorded until upstream mutation succeeds.
        """
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Reading session recording is not supported in Session 00 scaffold; will be implemented in Session 08.",
        )

    async def record_sessions_batch(
        self,
        request: GrimmlinkReadingSessionBatchRequest,
        username: str = "default",
    ) -> GrimmlinkReadingSessionBatchResponse:
        """Record batch of reading sessions.

        Mutations to Official Grimmory are disabled in Session 00 (scaffold).
        Activated in Session 08 (Reading Sessions & Idempotency).
        """
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Reading session batch recording is not supported in Session 00 scaffold; will be implemented in Session 08.",
        )
