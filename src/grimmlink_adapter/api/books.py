"""Book resolution and download routes."""

from fastapi import APIRouter, Depends, HTTPException, status

from grimmlink_adapter.models.grimmlink import GrimmlinkReadStatusRequest
from grimmlink_adapter.models.official import OfficialBookDTO
from grimmlink_adapter.security.auth_extractor import ClientCredentials, require_client_credentials

router = APIRouter(prefix="/books", tags=["Books"])


@router.get("/by-hash/{book_hash}", response_model=OfficialBookDTO)
async def get_book_by_hash(
    book_hash: str,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> OfficialBookDTO:
    """Defer hash lookup until user and book access can be verified upstream."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Book hash lookup is unavailable until Session 03 verifies user and book access.",
    )


@router.get("/{book_id}/download")
async def download_book(
    book_id: int,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> None:
    """Reject downloads until Official file streaming is implemented."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Book download is unavailable until Official file streaming is implemented.",
    )


@router.get("/read-statuses")
async def get_supported_read_statuses(
    creds: ClientCredentials = Depends(require_client_credentials),
) -> dict[str, list[str]]:
    """Reject status discovery until Official status support is verified."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Read-status discovery is unavailable until Official status mapping is implemented.",
    )


@router.put("/{book_id}/status")
async def update_read_status(
    book_id: int,
    request: GrimmlinkReadStatusRequest | None = None,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> None:
    """Reject read-status changes until the Official mutation is implemented."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Read-status updates are unavailable until the Official mutation is implemented.",
    )
