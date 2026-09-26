"""Unit tests verifying SQLite cache, outbox queue, and multi-shelf ownership safety."""

import pytest

from grimmlink_adapter.models.internal import BookHashEntry, CachedToken
from grimmlink_adapter.state.cache import BookHashCache, ShelfOwnershipCache, TokenCache
from grimmlink_adapter.state.outbox import IdempotencyManager, OutboxManager


@pytest.mark.asyncio
async def test_book_hash_cache_roundtrip() -> None:
    entry = BookHashEntry(
        book_hash="a1b2c3d4e5f67890",
        book_id=101,
        book_file_id=202,
        format="EPUB",
        file_size=1048576,
        filename="test_book.epub",
        title="Test Book Title",
    )
    await BookHashCache.put(entry)
    retrieved = await BookHashCache.get("a1b2c3d4e5f67890")
    assert retrieved is not None
    assert retrieved.book_id == 101
    assert retrieved.title == "Test Book Title"
    assert retrieved.format == "EPUB"

    missing = await BookHashCache.get("nonexistent_hash")
    assert missing is None


@pytest.mark.asyncio
async def test_token_cache_roundtrip() -> None:
    token = CachedToken(
        username="reader1",
        password_hash="md5_hash_sample",
        access_token="jwt_token_sample",
        refresh_token="jwt_refresh_sample",
    )
    await TokenCache.put(token)
    retrieved = await TokenCache.get("reader1")
    assert retrieved is not None
    assert retrieved.access_token == "jwt_token_sample"
    assert retrieved.refresh_token == "jwt_refresh_sample"


@pytest.mark.asyncio
async def test_multi_shelf_ownership_invariant() -> None:
    book_id = 555

    # Book exists on Shelf 1 (regular) and Shelf 2 (regular)
    await ShelfOwnershipCache.record_ownership(book_id, shelf_id=1, shelf_type="regular")
    await ShelfOwnershipCache.record_ownership(book_id, shelf_id=2, shelf_type="regular")

    # When removing from Shelf 1, check if book is still tracked elsewhere
    is_shared = await ShelfOwnershipCache.is_tracked_in_other_shelves(
        book_id=book_id, exclude_shelf_id=1, exclude_shelf_type="regular"
    )
    assert is_shared is True, "Book must remain protected because Shelf 2 also owns it"

    # Remove from Shelf 1
    await ShelfOwnershipCache.remove_ownership(book_id, shelf_id=1, shelf_type="regular")

    # Check again: now only Shelf 2 owns it, so excluding Shelf 2 should return False
    is_still_shared = await ShelfOwnershipCache.is_tracked_in_other_shelves(
        book_id=book_id, exclude_shelf_id=2, exclude_shelf_type="regular"
    )
    assert is_still_shared is False, "Book is no longer tracked in any other shelf"


@pytest.mark.asyncio
async def test_idempotency_manager() -> None:
    key = "session_idempotent_test_key_123"
    assert await IdempotencyManager.get_response(key) is None

    await IdempotencyManager.record_response(
        idempotency_key=key,
        action="RECORD_SESSION",
        response_status=202,
        response_body='{"status": "accepted"}',
    )

    result = await IdempotencyManager.get_response(key)
    assert result is not None
    status, body = result
    assert status == 202
    assert "accepted" in body


@pytest.mark.asyncio
async def test_outbox_queue_lifecycle() -> None:
    item_id = await OutboxManager.enqueue(
        action_type="PROGRESS_SYNC",
        payload={"book_id": 1, "percentage": 45.0},
        idempotency_key="progress_outbox_key_1",
    )

    pending = await OutboxManager.get_pending()
    assert any(item.id == item_id for item in pending)

    # Mark completed
    await OutboxManager.mark_completed(item_id)
    pending_after = await OutboxManager.get_pending()
    assert not any(item.id == item_id for item in pending_after)
