"""Reading progress normalization and calculation service."""

import logging

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
        """Fetch reading progress for a given book hash."""
        # Baseline scaffold return
        return KoreaderProgressPayload(
            book_hash=book_hash,
            percentage=0.0,
            current_page=1,
            total_pages=1,
        )

    async def update_progress(self, progress: KoreaderProgressPayload) -> None:
        """Normalize and queue or forward progress update."""
        if progress.current_page is not None and progress.total_pages is not None:
            # Enforce accurate display percentage calculation invariant
            progress.percentage = calculate_display_percentage(
                progress.current_page, progress.total_pages
            )
        logger.debug("Progress updated for book_hash=%s", progress.book_hash)
