"""Book resolution and download service."""

import logging

from grimmlink_adapter.models.grimmlink import GrimmlinkBookSummary
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.state.cache import BookHashCache

logger = logging.getLogger(__name__)

SUPPORTED_READ_STATUSES = ["UNREAD", "READING", "COMPLETED", "ABANDONED"]


class BookService:
    """Handles book lookup by hash and metadata summarization."""

    def __init__(self, official_client: OfficialGrimmoryClient | None = None) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()

    async def get_book_by_hash(self, book_hash: str) -> GrimmlinkBookSummary | None:
        """Look up book by hash, consulting local cache first."""
        cached = await BookHashCache.get(book_hash)
        if cached:
            return GrimmlinkBookSummary(
                bookId=cached.book_id,
                bookFileId=cached.book_file_id,
                title=cached.title,
                fileName=cached.filename,
                fileFormat=cached.format,
                fileSize=cached.file_size,
                bookHash=cached.book_hash,
            )

        # In Session 00 (scaffold), if not in cache, return None or mock
        return None

    def get_supported_read_statuses(self) -> list[str]:
        """Return list of supported read status strings."""
        return list(SUPPORTED_READ_STATUSES)
