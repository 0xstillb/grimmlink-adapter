"""Shelf synchronization and removal management service."""

import logging

from fastapi import HTTPException, status

from grimmlink_adapter.models.grimmlink import (
    GrimmlinkBookSummary,
    GrimmlinkShelfRemovalResponse,
    GrimmlinkShelfSummary,
)
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.state.cache import ShelfOwnershipCache

logger = logging.getLogger(__name__)


class ShelfService:
    """Manages shelf aggregation and book removals with multi-shelf safety."""

    def __init__(self, official_client: OfficialGrimmoryClient | None = None) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()

    async def list_shelves(self, shelf_type: str | None = None) -> list[GrimmlinkShelfSummary]:
        """List unified regular and magic shelves."""
        results: list[GrimmlinkShelfSummary] = []
        # Return scaffold baseline shelves
        if shelf_type in (None, "regular"):
            results.append(
                GrimmlinkShelfSummary(
                    id=1,
                    name="Default Shelf",
                    type="regular",
                    visibility="PRIVATE",
                    bookCount=0,
                )
            )
        if shelf_type in (None, "magic"):
            results.append(
                GrimmlinkShelfSummary(
                    id=100,
                    name="Currently Reading",
                    type="magic",
                    visibility="PRIVATE",
                    bookCount=0,
                    description="Rule-derived active reading shelf",
                )
            )
        return results

    async def list_shelf_books(
        self,
        shelf_type: str,
        shelf_id: int,
        limit: int | None = None,
        offset: int | None = None,
        cursor: str | None = None,
    ) -> list[GrimmlinkBookSummary]:
        """List books belonging to a given shelf."""
        return []

    async def remove_book_from_shelf(
        self,
        shelf_type: str,
        shelf_id: int,
        book_id: int,
    ) -> GrimmlinkShelfRemovalResponse:
        """Handle book removal from a shelf.

        Invariants:
        1. Magic shelf removal is strictly unsupported (rule-derived).
        2. Multi-shelf ownership check ensures local managed file is only removed
           if no other shelves track this book.
        """
        if shelf_type.lower() == "magic":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Manual removal from a magic shelf is not supported because magic shelves are rule-derived.",
            )

        # Multi-shelf safety check
        is_shared = await ShelfOwnershipCache.is_tracked_in_other_shelves(
            book_id=book_id,
            exclude_shelf_id=shelf_id,
            exclude_shelf_type=shelf_type,
        )

        await ShelfOwnershipCache.remove_ownership(
            book_id=book_id,
            shelf_id=shelf_id,
            shelf_type=shelf_type,
        )

        message = (
            "Book removed from shelf. Retained locally because it belongs to other shelves."
            if is_shared
            else "Book removed from shelf."
        )

        return GrimmlinkShelfRemovalResponse(
            success=True,
            message=message,
            shelfId=shelf_id,
            bookId=book_id,
            shelfType=shelf_type,
        )
