"""Book resolution and download routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from grimmlink_adapter.models.grimmlink import GrimmlinkBookSummary, GrimmlinkReadStatusRequest
from grimmlink_adapter.security.auth_extractor import ClientCredentials, require_client_credentials
from grimmlink_adapter.services.book_service import BookService

router = APIRouter(prefix="/books", tags=["Books"])
_book_service = BookService()


@router.get("/by-hash/{book_hash}", response_model=GrimmlinkBookSummary)
async def get_book_by_hash(
    book_hash: str,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> GrimmlinkBookSummary:
    """Resolve book metadata by its file content hash."""
    book = await _book_service.get_book_by_hash(book_hash)
    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Book with hash {book_hash} not found",
        )
    return book


@router.get("/{book_id}/download")
async def download_book(
    book_id: int,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> Response:
    """Stream book file download."""
    # Scaffold endpoint
    return Response(content=b"", media_type="application/octet-stream")


@router.get("/read-statuses")
async def get_supported_read_statuses(
    creds: ClientCredentials = Depends(require_client_credentials),
) -> dict[str, list[str]]:
    """Return list of supported read status values."""
    return {"statuses": _book_service.get_supported_read_statuses()}


@router.put("/{book_id}/status")
async def update_read_status(
    book_id: int,
    request: GrimmlinkReadStatusRequest | None = None,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> dict[str, Any]:
    """Update read status of a book."""
    return {
        "bookId": book_id,
        "status": request.status if request else "UNREAD",
        "updated": True,
    }
