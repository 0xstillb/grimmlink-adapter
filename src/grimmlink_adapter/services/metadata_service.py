"""Metadata synchronization service for bookmarks, annotations, and ratings."""

import logging

from grimmlink_adapter.models.grimmlink import (
    GrimmlinkMetadataBatchResponse,
    GrimmlinkMetadataPullResponse,
    GrimmlinkMetadataSyncRequest,
    GrimmlinkMetadataSyncResponse,
)
from grimmlink_adapter.official.client import OfficialGrimmoryClient

logger = logging.getLogger(__name__)


def normalize_rating(raw_rating: float, source_scale: int = 10) -> float:
    """Normalize rating from source scale (1-10 or 1-5) to 1-5 official scale."""
    if source_scale == 10:
        return round(raw_rating / 2.0, 1)
    return round(raw_rating, 1)


class MetadataService:
    """Handles metadata push/pull operations and item fanout."""

    def __init__(self, official_client: OfficialGrimmoryClient | None = None) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()

    async def sync_metadata(self, request: GrimmlinkMetadataSyncRequest | None) -> GrimmlinkMetadataSyncResponse:
        """Handle single metadata sync push."""
        items_count = len(request.items) if request and request.items else 0
        return GrimmlinkMetadataSyncResponse(
            status="ok",
            appliedCount=items_count,
            skippedCount=0,
            results=[],
        )

    async def sync_metadata_batch(
        self, request: GrimmlinkMetadataSyncRequest | None
    ) -> GrimmlinkMetadataBatchResponse:
        """Handle metadata batch sync push."""
        items_count = len(request.items) if request and request.items else 0
        return GrimmlinkMetadataBatchResponse(
            status="ok",
            totalItems=items_count,
            successCount=items_count,
            failureCount=0,
            results=[],
        )

    async def pull_metadata(
        self,
        book_id: int | None = None,
        book_hash: str | None = None,
        book_file_id: int | None = None,
        since: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        item_type: str | None = None,
    ) -> GrimmlinkMetadataPullResponse:
        """Handle metadata pull request."""
        return GrimmlinkMetadataPullResponse(items=[], nextCursor=None, hasMore=False)
