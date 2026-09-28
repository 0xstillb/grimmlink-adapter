"""Reading progress synchronization routes."""

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.grimmlink import KoreaderProgressPayload
from grimmlink_adapter.security.auth_extractor import ClientCredentials, require_client_credentials
from grimmlink_adapter.services.progress_service import ProgressService

router = APIRouter(prefix="/syncs", tags=["Progress Sync"])
_progress_service = ProgressService()


@router.get("/progress/{book_hash}", response_model=KoreaderProgressPayload)
async def get_progress(
    book_hash: str,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> Response | KoreaderProgressPayload:
    """Retrieve reading progress for a given book hash."""
    if settings.GRIMMLINK_NATIVE_PROGRESS_PROXY and creds.username and creds.md5_key and not creds.bearer_token:
        return JSONResponse(content=await _progress_service.get_native_progress(book_hash, creds))
    return await _progress_service.get_progress(book_hash, creds)


@router.put("/progress")
async def update_progress(
    progress: KoreaderProgressPayload,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> dict[str, object]:
    """Submit updated reading progress."""
    return await _progress_service.update_progress(progress, creds)
