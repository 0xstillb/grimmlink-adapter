"""Reading progress normalization and calculation service."""

import logging

from fastapi import HTTPException, status

from grimmlink_adapter.models.grimmlink import KoreaderProgressPayload
from grimmlink_adapter.official.client import OfficialGrimmoryClient

logger = logging.getLogger(__name__)


def calculate_display_percentage(current_page: int, total_pages: int) -> float:
    """Calculate percentage from page numbers accurately.

    Invariant:
    currentPage / totalPages * 100
    Example: 55 / 16653 ≈ 0.33027%, NOT 33.0%.
    """
    if total_pages <= 0 or current_page < 0:
        return 0.0
    pct = (current_page / total_pages) * 100.0
    return round(min(100.0, max(0.0, pct)), 4)


class ProgressService:
    """Normalizes and translates KOReader reading progress."""

    def __init__(self, official_client: OfficialGrimmoryClient | None = None) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()

    async def get_progress(self, book_hash: str) -> KoreaderProgressPayload:
        """Reject progress reads until Official data and native locations are mapped."""
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Progress reads are unavailable until Official progress sync is implemented.",
        )

    async def update_progress(self, progress: KoreaderProgressPayload) -> None:
        """Normalize and queue or forward progress update.

        Mutations to Official Grimmory are disabled in Session 00 (scaffold).
        Activated in Session 06 (Progress Normalization).
        """
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Progress update mutation is not supported in Session 00 scaffold; will be implemented in Session 06.",
        )
