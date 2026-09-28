"""Reading sessions synchronization routes."""

from typing import Any

from fastapi import APIRouter, Depends, Query, status

from grimmlink_adapter.models.grimmlink import (
    GrimmlinkReadingSessionBatchRequest,
    GrimmlinkReadingSessionBatchResponse,
    GrimmlinkReadingSessionSingleRequest,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials, require_client_credentials
from grimmlink_adapter.services.session_service import SessionService

router = APIRouter(prefix="/reading-sessions", tags=["Reading Sessions"])
_session_service = SessionService()


@router.get("", response_model=list[dict[str, Any]])
async def get_sessions(
    bookId: int = Query(..., description="Target book ID"),
    limit: int = Query(50, ge=1, le=500, description="Max session records to return"),
    creds: ClientCredentials = Depends(require_client_credentials),
) -> list[dict[str, Any]]:
    """Retrieve recorded reading sessions for a book."""
    return await _session_service.get_reading_sessions(bookId, limit, creds=creds)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def record_session(
    request: GrimmlinkReadingSessionSingleRequest,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> None:
    """Record a single reading session idempotently."""
    username = creds.username or "anonymous"
    await _session_service.record_session(request, creds=creds, username=username)


@router.post("/batch", response_model=GrimmlinkReadingSessionBatchResponse)
async def record_sessions_batch(
    request: GrimmlinkReadingSessionBatchRequest,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> GrimmlinkReadingSessionBatchResponse:
    """Record a batch of reading sessions idempotently."""
    username = creds.username or "anonymous"
    return await _session_service.record_sessions_batch(request, creds=creds, username=username)
