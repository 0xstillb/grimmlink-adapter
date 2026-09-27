"""Shelf synchronization and removal routes."""


from fastapi import APIRouter, Depends, Query

from grimmlink_adapter.models.grimmlink import (
    GrimmlinkBookSummary,
    GrimmlinkShelfRemovalResponse,
    GrimmlinkShelfSummary,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials, require_client_credentials
from grimmlink_adapter.services.shelf_service import ShelfService

router = APIRouter(prefix="/shelves", tags=["Shelves"])
_shelf_service = ShelfService()


@router.get("", response_model=list[GrimmlinkShelfSummary])
async def list_shelves(
    type: str | None = Query(None, description="Shelf type filter: regular or magic"),
    creds: ClientCredentials = Depends(require_client_credentials),
) -> list[GrimmlinkShelfSummary]:
    """List regular and magic shelves."""
    return await _shelf_service.list_shelves(type, creds)


@router.get("/{shelf_id}/books", response_model=list[GrimmlinkBookSummary])
async def list_regular_shelf_books(
    shelf_id: int,
    limit: int | None = Query(None),
    offset: int | None = Query(None),
    cursor: str | None = Query(None),
    creds: ClientCredentials = Depends(require_client_credentials),
) -> list[GrimmlinkBookSummary]:
    """List books in a regular shelf."""
    return await _shelf_service.list_shelf_books("regular", shelf_id, creds, limit, offset, cursor)


@router.get("/{shelf_type}/{shelf_id}/books", response_model=list[GrimmlinkBookSummary])
async def list_shelf_books_by_type(
    shelf_type: str,
    shelf_id: int,
    limit: int | None = Query(None),
    offset: int | None = Query(None),
    cursor: str | None = Query(None),
    creds: ClientCredentials = Depends(require_client_credentials),
) -> list[GrimmlinkBookSummary]:
    """List books in a shelf by type (regular or magic)."""
    return await _shelf_service.list_shelf_books(shelf_type, shelf_id, creds, limit, offset, cursor)


@router.post("/{shelf_id}/books/{book_id}/remove", response_model=GrimmlinkShelfRemovalResponse)
async def remove_book_from_regular_shelf(
    shelf_id: int,
    book_id: int,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> GrimmlinkShelfRemovalResponse:
    """Remove a book from a regular shelf."""
    return await _shelf_service.remove_book_from_shelf("regular", shelf_id, book_id, creds)


@router.post("/{shelf_type}/{shelf_id}/books/{book_id}/remove", response_model=GrimmlinkShelfRemovalResponse)
async def remove_book_from_shelf_by_type(
    shelf_type: str,
    shelf_id: int,
    book_id: int,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> GrimmlinkShelfRemovalResponse:
    """Remove a book from a shelf by type. Refuses magic shelves."""
    return await _shelf_service.remove_book_from_shelf(shelf_type, shelf_id, book_id, creds)
