"""Metadata synchronization routes (ratings, bookmarks, annotations)."""


from fastapi import APIRouter, Depends, Query

from grimmlink_adapter.models.grimmlink import (
    GrimmlinkMetadataBatchResponse,
    GrimmlinkMetadataPullResponse,
    GrimmlinkMetadataSyncRequest,
    GrimmlinkMetadataSyncResponse,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials, require_client_credentials
from grimmlink_adapter.services.metadata_service import MetadataService

router = APIRouter(prefix="/syncs", tags=["Metadata Sync"])
_metadata_service = MetadataService()


@router.post("/metadata", response_model=GrimmlinkMetadataSyncResponse)
async def sync_metadata(
    request: GrimmlinkMetadataSyncRequest | None = None,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> GrimmlinkMetadataSyncResponse:
    """Push metadata updates (ratings, bookmarks, annotations)."""
    return await _metadata_service.sync_metadata(request)


@router.get("/metadata", response_model=GrimmlinkMetadataPullResponse)
async def pull_metadata(
    book_id: int | None = Query(None, alias="bookId"),
    book_hash: str | None = Query(None, alias="bookHash"),
    book_file_id: int | None = Query(None, alias="bookFileId"),
    since: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int | None = Query(None),
    type: str | None = Query(None),
    creds: ClientCredentials = Depends(require_client_credentials),
) -> GrimmlinkMetadataPullResponse:
    """Pull metadata updates since a given cursor or timestamp."""
    return await _metadata_service.pull_metadata(
        book_id=book_id,
        book_hash=book_hash,
        book_file_id=book_file_id,
        since=since,
        cursor=cursor,
        limit=limit,
        item_type=type,
    )


@router.post("/metadata/batch", response_model=GrimmlinkMetadataBatchResponse)
async def sync_metadata_batch(
    request: GrimmlinkMetadataSyncRequest | None = None,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> GrimmlinkMetadataBatchResponse:
    """Push batch metadata updates."""
    return await _metadata_service.sync_metadata_batch(request)
