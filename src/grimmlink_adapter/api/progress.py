"""Reading progress synchronization routes."""

from fastapi import APIRouter, Depends

from grimmlink_adapter.models.grimmlink import KoreaderProgressPayload
from grimmlink_adapter.security.auth_extractor import ClientCredentials, require_client_credentials
from grimmlink_adapter.services.progress_service import ProgressService

router = APIRouter(prefix="/syncs", tags=["Progress Sync"])
_progress_service = ProgressService()


@router.get("/progress/{book_hash}", response_model=KoreaderProgressPayload)
async def get_progress(
    book_hash: str,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> KoreaderProgressPayload:
    """Retrieve reading progress for a given book hash."""
    return await _progress_service.get_progress(book_hash)


@router.put("/progress")
async def update_progress(
    progress: KoreaderProgressPayload,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> dict[str, str]:
    """Submit updated reading progress."""
    await _progress_service.update_progress(progress)
    return {"status": "progress updated"}
