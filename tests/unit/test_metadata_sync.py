"""Unit tests for Session 07 — Metadata Sync (Rating, Bookmark, Annotation, Dedupe, Cursor, Outbox)."""

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from grimmlink_adapter.models.grimmlink import (
    GrimmlinkAnnotationPayload,
    GrimmlinkBookmarkPayload,
    GrimmlinkLocationPayload,
    GrimmlinkMetadataSyncRequest,
    GrimmlinkRatingPayload,
)
from grimmlink_adapter.models.internal import OutboxAction
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import OfficialBadResponseError, OfficialTimeoutError
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.metadata_service import (
    MetadataService,
    is_valid_cfi,
    normalize_rating,
)
from grimmlink_adapter.services.request_auth import VerifiedBearer
from grimmlink_adapter.state.cache import MetadataSyncStore
from grimmlink_adapter.state.database import get_connection
from grimmlink_adapter.state.migrations import apply_migrations
from grimmlink_adapter.state.outbox import OutboxManager


@pytest.fixture(autouse=True)
async def setup_test_db() -> None:
    """Ensure all migrations up to 009 are applied for each test."""
    await apply_migrations()
    async with get_connection() as conn:
        await conn.execute("DELETE FROM metadata_remote_mappings")
        await conn.execute("DELETE FROM metadata_applied_history")
        await conn.execute("DELETE FROM scoped_metadata_cursors")
        await conn.execute("DELETE FROM metadata_cursors")
        await conn.execute("DELETE FROM outbox")
        await conn.execute("DELETE FROM idempotency_keys")
        await conn.commit()


@pytest.fixture
def mock_creds() -> ClientCredentials:
    return ClientCredentials(username="testuser", md5_key="mock_md5_key")


@pytest.fixture
def verified_bearer() -> VerifiedBearer:
    return VerifiedBearer("mock_jwt_token", user_id=42)


# ============================================================================
# 1. Rating Conversion, Reset & Conversion Drift Prevention
# ============================================================================


def test_rating_normalization_helper() -> None:
    # 1-10 scale maps to 1-5
    assert normalize_rating(10, source_scale=10) == 5
    assert normalize_rating(8, source_scale=10) == 4
    assert normalize_rating(7, source_scale=10) == 4
    assert normalize_rating(1, source_scale=10) == 1
    assert normalize_rating(5.0, source_scale=10) == 2.5
    assert normalize_rating(9.0, source_scale=10) == 4.5
    assert MetadataService()._normalize_rating_input(
        GrimmlinkRatingPayload(value=5.0, scale=10), 1
    ).official_rating == 3
    assert MetadataService()._normalize_rating_input(
        GrimmlinkRatingPayload(value=9.0, scale=10), 1
    ).official_rating == 5
    assert normalize_rating(0, source_scale=10) is None  # Reset
    assert normalize_rating(-1, source_scale=10) is None  # Reset

    # 1-5 scale maps to 1-5
    assert normalize_rating(5, source_scale=5) == 5
    assert normalize_rating(4, source_scale=5) == 4
    assert normalize_rating(1, source_scale=5) == 1


@pytest.mark.asyncio
async def test_rating_conversion_and_reset(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 1-10 to 1-5 conversion, explicit reset, and prevention of conversion drift."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.update_personal_rating = AsyncMock(return_value={})  # type: ignore[method-assign]
    client.reset_personal_rating = AsyncMock(return_value=None)  # type: ignore[method-assign]
    client.get_personal_rating = AsyncMock(return_value=4)  # type: ignore[method-assign]
    client.get_bookmarks_for_book = AsyncMock(return_value=[])  # type: ignore[method-assign]

    service = MetadataService(official_client=client)

    # Push rating 7 (scale 10) -> maps to 4/5 on Official
    req = GrimmlinkMetadataSyncRequest(
        bookId=10,
        rating=GrimmlinkRatingPayload(value=7, scale=10, dedupeKey="rating:10:koreader"),
    )
    resp = await service.sync_metadata(req, creds=mock_creds)
    assert resp.ok is True
    assert resp.appliedCount == 1
    assert resp.results is not None
    assert resp.results.rating is not None
    assert resp.results.rating.status == "SUCCESS"
    client.update_personal_rating.assert_awaited_once_with(10, 4, verified_bearer)

    # Pull rating: prevent conversion drift!
    # Official has 4. Without drift prevention, 4 * 2 = 8 (drifted from 7).
    # With drift prevention, local applied history preserved 7 (scale 10)!
    pull_resp = await service.pull_metadata(book_id=10, creds=mock_creds)
    assert pull_resp.ok is True
    assert len(pull_resp.items) == 1
    rating_item = pull_resp.items[0]
    assert rating_item.type == "rating"
    assert rating_item.payload["value"] == 7  # Exactly preserved! No conversion drift!
    assert rating_item.payload["scale"] == 10

    # Explicit reset: rating value = 0
    reset_req = GrimmlinkMetadataSyncRequest(
        bookId=10,
        rating=GrimmlinkRatingPayload(value=0, scale=10, dedupeKey="rating:10:koreader"),
    )
    reset_resp = await service.sync_metadata(reset_req, creds=mock_creds)
    assert reset_resp.ok is True
    assert reset_resp.appliedCount == 1
    client.reset_personal_rating.assert_awaited_once_with(10, verified_bearer)


# ============================================================================
# 2. Duplicate Bookmark Deduplication
# ============================================================================


@pytest.mark.asyncio
async def test_duplicate_bookmark_deduplication(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate bookmark push returns DUPLICATE status without calling upstream twice."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_bookmark = AsyncMock(return_value={"id": 101, "bookId": 10})  # type: ignore[method-assign]
    client.get_bookmarks_for_book = AsyncMock(return_value=[])  # type: ignore[method-assign]
    service = MetadataService(official_client=client)

    bm_payload = GrimmlinkBookmarkPayload(
        dedupeKey="bm:10:p50",
        title="Chapter 1",
        notes="Interesting idea",
        page=50,
        location=GrimmlinkLocationPayload(cfi="epubcfi(/6/2!/4/2:0)"),
    )
    req = GrimmlinkMetadataSyncRequest(bookId=10, bookmarks=[bm_payload])

    # First push -> creates upstream
    resp1 = await service.sync_metadata(req, creds=mock_creds)
    assert resp1.appliedCount == 1
    assert resp1.skippedCount == 0
    assert resp1.results.bookmarks[0].status == "SUCCESS"
    assert resp1.results.bookmarks[0].id == "bm-101"
    client.create_bookmark.assert_awaited_once()

    # Second push with identical content -> DUPLICATE, no second upstream call
    resp2 = await service.sync_metadata(req, creds=mock_creds)
    assert resp2.appliedCount == 0
    assert resp2.skippedCount == 1
    assert resp2.results.bookmarks[0].status == "DUPLICATE"
    assert resp2.results.bookmarks[0].id == "bm-101"
    assert client.create_bookmark.await_count == 1  # Unchanged!


# ============================================================================
# 3. Bookmark Deletion Only After Confirmed Success
# ============================================================================


@pytest.mark.asyncio
async def test_bookmark_deletion_only_after_confirmed_success(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a bookmark calls Official DELETE; local mapping is deleted ONLY after confirmed success."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_bookmark = AsyncMock(return_value={"id": 202, "bookId": 10})  # type: ignore[method-assign]
    client.get_bookmarks_for_book = AsyncMock(return_value=[])  # type: ignore[method-assign]
    client.delete_bookmark = AsyncMock(return_value=True)  # type: ignore[method-assign]
    service = MetadataService(official_client=client)

    # 1. Create bookmark
    bm = GrimmlinkBookmarkPayload(dedupeKey="bm:del:1", title="To Delete", page=20)
    await service.sync_metadata(GrimmlinkMetadataSyncRequest(bookId=10, bookmarks=[bm]), creds=mock_creds)

    remote_id = await MetadataSyncStore.get_remote_id("42", 10, "bookmark", "bm:del:1")
    assert remote_id == 202

    # 2. Simulate upstream failure during delete
    client.delete_bookmark.side_effect = OfficialBadResponseError(
        "Server Error", status_code=500, method="DELETE", url="/bookmarks/202"
    )
    del_bm = GrimmlinkBookmarkPayload(dedupeKey="bm:del:1", deleted=True)
    with pytest.raises(OfficialBadResponseError):
        await service.sync_metadata(GrimmlinkMetadataSyncRequest(bookId=10, bookmarks=[del_bm]), creds=mock_creds)

    # Local mapping must STILL be preserved because upstream did not confirm success!
    remote_id_after_fail = await MetadataSyncStore.get_remote_id("42", 10, "bookmark", "bm:del:1")
    assert remote_id_after_fail == 202

    # 3. Upstream confirms success (200/204)
    client.delete_bookmark.side_effect = None
    client.delete_bookmark.return_value = True
    del_resp = await service.sync_metadata(
        GrimmlinkMetadataSyncRequest(bookId=10, bookmarks=[del_bm]), creds=mock_creds
    )
    assert del_resp.appliedCount == 1

    # Now local mapping is deleted!
    remote_id_after_success = await MetadataSyncStore.get_remote_id("42", 10, "bookmark", "bm:del:1")
    assert remote_id_after_success is None


# ============================================================================
# 4. Unsupported Annotation Fields & Location Preservation
# ============================================================================


def test_is_valid_cfi() -> None:
    assert is_valid_cfi("epubcfi(/6/4[chap117]!/4/2/10/1:0)") is True
    # XPointer pos0 format is NOT valid CFI
    assert is_valid_cfi("/6/4[chap117]!/4/2/10/1:0") is False
    assert is_valid_cfi(None) is False
    assert is_valid_cfi("12345") is False


@pytest.mark.asyncio
async def test_unsupported_annotation_fields_never_corrupt_location(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsupported fields (drawer, style, pos0/pos1) are preserved in SQLite and never corrupt CFI."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_bookmark = AsyncMock(return_value={"id": 303, "bookId": 15})  # type: ignore[method-assign]
    client.get_bookmarks_for_book = AsyncMock(return_value=[])  # type: ignore[method-assign]
    service = MetadataService(official_client=client)

    ann = GrimmlinkAnnotationPayload(
        dedupeKey="ann:xpointer:test",
        type="highlight",
        text="A famous quote",
        note="User note",
        color="#FFEB3B",
        drawer="lighten",  # unsupported upstream
        style="highlight",  # unsupported upstream
        chapter="Chapter 117",  # unsupported upstream
        page=500,
        location=GrimmlinkLocationPayload(
            pos0="/6/4[chap117]!/4/2/10/1:0",  # XPointer
            pos1="/6/4[chap117]!/4/2/10/1:64",
            pageno=500,
            # Notice cfi is None or raw XPointer
        ),
    )
    req = GrimmlinkMetadataSyncRequest(bookId=15, annotations=[ann])
    resp = await service.sync_metadata(req, creds=mock_creds)
    assert resp.appliedCount == 1

    # Verify what was sent to Official create_bookmark
    client.create_bookmark.assert_awaited_once()
    call_payload = client.create_bookmark.call_args[0][0]
    assert call_payload["bookId"] == 15
    assert call_payload["title"] == "A famous quote"
    assert call_payload["notes"] == "User note"
    assert call_payload["color"] == "#FFEB3B"
    assert call_payload["pageNumber"] == 500
    # Crucial invariant: cfi must NOT be populated with raw XPointer!
    assert call_payload["cfi"] is None

    # Verify that unsupported fields were preserved in SQLite
    applied = await MetadataSyncStore.get_applied_record("42", 15, "annotation", "ann:xpointer:test")
    assert applied is not None
    assert applied.payload_json is not None
    stored_payload = json.loads(applied.payload_json)
    assert stored_payload["drawer"] == "lighten"
    assert stored_payload["style"] == "highlight"
    assert stored_payload["pos0"] == "/6/4[chap117]!/4/2/10/1:0"
    assert stored_payload["pos1"] == "/6/4[chap117]!/4/2/10/1:64"


# ============================================================================
# 5. Same-Device Skip on Pull
# ============================================================================


@pytest.mark.asyncio
async def test_same_device_skip_on_pull(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pulling metadata skips items authored by the requesting device."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_bookmark = AsyncMock(side_effect=[{"id": 401}, {"id": 402}])  # type: ignore[method-assign]
    client.get_bookmarks_for_book = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {"id": 401, "title": "Device A Item"},
            {"id": 402, "title": "Device B Item"},
        ]
    )
    client.get_personal_rating = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service = MetadataService(official_client=client)

    # Push item from Device A
    bm_a = GrimmlinkBookmarkPayload(dedupeKey="bm:devA", title="Device A Item", page=10)
    await service.sync_metadata(
        GrimmlinkMetadataSyncRequest(bookId=20, device="Device A", deviceId="dev-A", bookmarks=[bm_a]),
        creds=mock_creds,
    )

    # Push item from Device B
    bm_b = GrimmlinkBookmarkPayload(dedupeKey="bm:devB", title="Device B Item", page=20)
    await service.sync_metadata(
        GrimmlinkMetadataSyncRequest(bookId=20, device="Device B", deviceId="dev-B", bookmarks=[bm_b]),
        creds=mock_creds,
    )

    # Pull as Device A -> must skip dev-A item and return only dev-B item
    pull_a = await service.pull_metadata(book_id=20, device_id="dev-A", creds=mock_creds)
    assert len(pull_a.items) == 1
    assert pull_a.items[0].dedupeKey == "bm:devB"

    # Pull as Device B -> must skip dev-B item and return only dev-A item
    pull_b = await service.pull_metadata(book_id=20, device_id="dev-B", creds=mock_creds)
    assert len(pull_b.items) == 1
    assert pull_b.items[0].dedupeKey == "bm:devA"


# ============================================================================
# 6. Restart & Retry Idempotency
# ============================================================================


@pytest.mark.asyncio
async def test_restart_and_retry_idempotency() -> None:
    """Outbox actions survive process restart and execute idempotently."""
    client = OfficialGrimmoryClient()
    client.update_personal_rating = AsyncMock(return_value={})  # type: ignore[method-assign]

    service1 = MetadataService(official_client=client)

    # Enqueue rating update to outbox
    await OutboxManager.enqueue(
        action_type="RATING_UPDATE",
        payload={"bookId": 30, "rating": 5},
        idempotency_key="rating:user1:30:v1",
    )

    # First execution
    pending = await OutboxManager.get_pending()
    assert len(pending) == 1
    res1 = await service1.execute_outbox_metadata_action(pending[0], bearer_token="test_bearer")
    assert res1["status"] == "SUCCESS"
    client.update_personal_rating.assert_awaited_once_with(30, 5, "test_bearer")

    # Simulate restart: new MetadataService instance
    service2 = MetadataService(official_client=client)

    # Re-executing with same idempotency key must not call client again
    res2 = await service2.execute_outbox_metadata_action(pending[0], bearer_token="test_bearer")
    assert res2["status"] == "ALREADY_COMPLETED"
    assert client.update_personal_rating.await_count == 1


# ============================================================================
# 7. Cursor Isolation by (Server, User, Book, File, Type)
# ============================================================================


@pytest.mark.asyncio
async def test_cursor_isolation_by_server_user_book_file_type() -> None:
    """Cursors are strictly scoped and never global."""
    server1 = "http://grimmory1:8000"
    server2 = "http://grimmory2:8000"
    user1 = "u1"
    user2 = "u2"

    # Set cursor for server1, user1, book 100, file 10, bookmark
    await MetadataSyncStore.set_scoped_cursor(server1, user1, 100, 10, "bookmark", "cursor-s1-u1-b100-f10-bm")

    # Verify retrieval
    val = await MetadataSyncStore.get_scoped_cursor(server1, user1, 100, 10, "bookmark")
    assert val == "cursor-s1-u1-b100-f10-bm"

    # Check that another book is isolated (None)
    assert await MetadataSyncStore.get_scoped_cursor(server1, user1, 101, 10, "bookmark") is None

    # Check that another user is isolated (None)
    assert await MetadataSyncStore.get_scoped_cursor(server1, user2, 100, 10, "bookmark") is None

    # Check that another server is isolated (None)
    assert await MetadataSyncStore.get_scoped_cursor(server2, user1, 100, 10, "bookmark") is None

    # Check that another type is isolated (None)
    assert await MetadataSyncStore.get_scoped_cursor(server1, user1, 100, 10, "rating") is None

    # Check that another file is isolated (None)
    assert await MetadataSyncStore.get_scoped_cursor(server1, user1, 100, 11, "bookmark") is None


# ============================================================================
# 8. Timeout After Possible Commit Safety
# ============================================================================


@pytest.mark.asyncio
async def test_timeout_after_possible_commit() -> None:
    """When a timeout occurs after commit, retry detects existing remote record and adopts it."""
    client = OfficialGrimmoryClient()

    # Step 1: Upstream create timed out
    client.get_bookmarks_for_book = AsyncMock(return_value=[])  # type: ignore[method-assign]
    client.create_bookmark = AsyncMock(  # type: ignore[method-assign]
        side_effect=OfficialTimeoutError("Request timed out", method="POST", url="/bookmarks")
    )

    service = MetadataService(official_client=client)
    action = OutboxAction(
        id="act-1",
        action_type="BOOKMARK_CREATE",
        payload={"bookId": 50, "title": "Timeout Test", "pageNumber": 12, "cfi": None},
        idempotency_key="outbox:bm:create:50:p12",
    )

    with pytest.raises(OfficialTimeoutError):
        await service.execute_outbox_metadata_action(action, bearer_token="bearer_token")

    # Step 2: On retry, get_bookmarks_for_book shows the bookmark actually was committed on Official!
    client.get_bookmarks_for_book = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"id": 999, "title": "Timeout Test", "pageNumber": 12, "cfi": None}]
    )
    client.create_bookmark = AsyncMock()  # Must not be called!
    action.retry_count = 1

    retry_res = await service.execute_outbox_metadata_action(action, bearer_token="bearer_token")
    assert retry_res["status"] == "ALREADY_COMMITTED"
    assert retry_res["id"] == 999
    client.create_bookmark.assert_not_called()


@pytest.mark.asyncio
async def test_batch_sync_end_to_end(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end metadata batch sync: push and pull in one roundtrip."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.update_personal_rating = AsyncMock(return_value={})  # type: ignore[method-assign]
    client.create_bookmark = AsyncMock(return_value={"id": 777})  # type: ignore[method-assign]
    client.get_personal_rating = AsyncMock(return_value=4)  # type: ignore[method-assign]
    client.get_bookmarks_for_book = AsyncMock(return_value=[{"id": 777, "title": "Batch BM"}])  # type: ignore[method-assign]

    service = MetadataService(official_client=client)

    req = GrimmlinkMetadataSyncRequest(
        bookId=60,
        bookFileId=101,
        rating=GrimmlinkRatingPayload(value=8, scale=10, dedupeKey="rating:60:koreader"),
        bookmarks=[GrimmlinkBookmarkPayload(dedupeKey="bm:batch:1", title="Batch BM", page=10)],
    )

    batch_resp = await service.sync_metadata_batch(req, creds=mock_creds)
    assert batch_resp.ok is True
    assert batch_resp.push is not None
    assert batch_resp.push.appliedCount == 2
    assert batch_resp.pull is not None
    assert batch_resp.pull.bookId == 60
    assert len(batch_resp.pull.items) == 2  # 1 rating + 1 bookmark


@pytest.mark.asyncio
async def test_empty_request_returns_ok_zero_counts() -> None:
    """Empty request returns ok with 0 applied/skipped count."""
    service = MetadataService()
    resp = await service.sync_metadata(None)
    assert resp.ok is True
    assert resp.appliedCount == 0
    assert resp.skippedCount == 0


@pytest.mark.asyncio
async def test_rating_requires_value_or_explicit_reset_and_preserves_review(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.update_personal_rating = AsyncMock(return_value={})  # type: ignore[method-assign]
    client.reset_personal_rating = AsyncMock(return_value=None)  # type: ignore[method-assign]
    client.get_personal_rating = AsyncMock(return_value=4)  # type: ignore[method-assign]
    client.get_bookmarks_for_book = AsyncMock(return_value=[])  # type: ignore[method-assign]
    service = MetadataService(official_client=client)

    with pytest.raises(HTTPException) as caught:
        await service.sync_metadata(
            GrimmlinkMetadataSyncRequest(
                bookId=70, rating=GrimmlinkRatingPayload(review="must not reset")
            ),
            creds=mock_creds,
        )
    assert caught.value.status_code == 422
    client.reset_personal_rating.assert_not_awaited()

    await service.sync_metadata(
        GrimmlinkMetadataSyncRequest(
            bookId=70,
            rating=GrimmlinkRatingPayload(
                value=8, scale=10, review="kept locally", dedupeKey="custom-rating-key"
            ),
        ),
        creds=mock_creds,
    )
    pulled = await service.pull_metadata(book_id=70, creds=mock_creds)
    assert pulled.items[0].payload["value"] == 8
    assert pulled.items[0].payload["review"] == "kept locally"

    await service.sync_metadata(
        GrimmlinkMetadataSyncRequest(
            bookId=70,
            rating=GrimmlinkRatingPayload(value=8, reset=True, dedupeKey="custom-rating-key"),
        ),
        creds=mock_creds,
    )
    client.reset_personal_rating.assert_awaited_once_with(70, verified_bearer)


@pytest.mark.asyncio
async def test_annotation_unsupported_only_change_is_not_deduped(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_bookmark = AsyncMock(return_value={"id": 701})  # type: ignore[method-assign]
    client.update_bookmark = AsyncMock(return_value={})  # type: ignore[method-assign]
    service = MetadataService(official_client=client)

    first = GrimmlinkAnnotationPayload(
        dedupeKey="ann:unsupported-update",
        text="Quote",
        drawer="lighten",
        location=GrimmlinkLocationPayload(pos0="/old"),
    )
    await service.sync_metadata(
        GrimmlinkMetadataSyncRequest(bookId=71, annotations=[first]), creds=mock_creds
    )
    changed = first.model_copy(
        update={"drawer": "underscore", "location": GrimmlinkLocationPayload(pos0="/new")}
    )
    response = await service.sync_metadata(
        GrimmlinkMetadataSyncRequest(bookId=71, annotations=[changed]), creds=mock_creds
    )

    assert response.results.annotations[0].status == "SUCCESS"
    client.update_bookmark.assert_awaited_once()
    stored = await MetadataSyncStore.get_applied_record(
        "42", 71, "annotation", "ann:unsupported-update"
    )
    assert stored is not None
    assert json.loads(stored.payload_json)["pos0"] == "/new"


@pytest.mark.asyncio
async def test_unmapped_delete_is_not_reported_as_success(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.delete_bookmark = AsyncMock(return_value=True)  # type: ignore[method-assign]
    service = MetadataService(official_client=client)

    response = await service.sync_metadata(
        GrimmlinkMetadataSyncRequest(
            bookId=72,
            bookmarks=[GrimmlinkBookmarkPayload(dedupeKey="missing", deleted=True)],
        ),
        creds=mock_creds,
    )
    assert response.results.bookmarks[0].status == "FAILED"
    assert response.appliedCount == 0
    client.delete_bookmark.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_path_recovers_timeout_after_possible_bookmark_commit(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_bookmark = AsyncMock(  # type: ignore[method-assign]
        side_effect=OfficialTimeoutError("timed out", method="POST", url="/bookmarks")
    )
    client.get_bookmarks_for_book = AsyncMock(  # type: ignore[method-assign]
        return_value=[{
            "id": 730,
            "bookId": 73,
            "title": "Possibly committed",
            "notes": None,
            "pageNumber": 12,
            "cfi": None,
            "color": None,
        }]
    )
    service = MetadataService(official_client=client)
    request = GrimmlinkMetadataSyncRequest(
        bookId=73,
        bookmarks=[
            GrimmlinkBookmarkPayload(
                dedupeKey="bm:timeout-route", title="Possibly committed", page=12
            )
        ],
    )

    with pytest.raises(OfficialTimeoutError):
        await service.sync_metadata(request, creds=mock_creds)
    recovered = await service.sync_metadata(request, creds=mock_creds)

    assert recovered.results.bookmarks[0].id == "bm-730"
    assert client.create_bookmark.await_count == 1
    assert await MetadataSyncStore.get_remote_id(
        "42", 73, "bookmark", "bm:timeout-route"
    ) == 730


@pytest.mark.asyncio
async def test_cursor_pages_without_loss_and_rejects_cross_scope_reuse(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.get_personal_rating = AsyncMock(return_value=None)  # type: ignore[method-assign]
    client.get_bookmarks_for_book = AsyncMock(return_value=[  # type: ignore[method-assign]
        {"id": 1, "title": "One"},
        {"id": 2, "title": "Two"},
        {"id": 3, "title": "Three"},
    ])
    service = MetadataService(official_client=client)

    first = await service.pull_metadata(book_id=74, book_file_id=9, limit=2, creds=mock_creds)
    assert first.hasMore is True
    assert first.nextCursor.startswith("m1.")
    second = await service.pull_metadata(
        book_id=74, book_file_id=9, limit=2, cursor=first.nextCursor, creds=mock_creds
    )
    assert second.hasMore is False
    assert {item.id for item in first.items}.isdisjoint({item.id for item in second.items})
    assert len(first.items) + len(second.items) == 3
    resumed = await service.pull_metadata(
        book_id=74, book_file_id=9, limit=2, creds=mock_creds
    )
    assert resumed.since == second.nextCursor
    assert resumed.items == []

    with pytest.raises(HTTPException) as caught:
        await service.pull_metadata(
            book_id=75, book_file_id=9, limit=2, cursor=first.nextCursor, creds=mock_creds
        )
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_pull_uses_fresh_official_fields_and_preserves_local_unsupported_fields(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_bookmark = AsyncMock(return_value={"id": 760})  # type: ignore[method-assign]
    client.get_personal_rating = AsyncMock(return_value=None)  # type: ignore[method-assign]
    client.get_bookmarks_for_book = AsyncMock(return_value=[{  # type: ignore[method-assign]
        "id": 760,
        "title": "Edited in WebUI",
        "notes": "New note",
        "pageNumber": 99,
        "cfi": "epubcfi(/6/8)",
    }])
    service = MetadataService(official_client=client)
    await service.sync_metadata(
        GrimmlinkMetadataSyncRequest(
            bookId=76,
            deviceId="device-a",
            bookmarks=[
                GrimmlinkBookmarkPayload(
                    dedupeKey="bm:web-edit", title="Old", chapter="Local chapter", page=1
                )
            ],
        ),
        creds=mock_creds,
    )

    pulled = await service.pull_metadata(book_id=76, device_id="device-a", creds=mock_creds)
    assert len(pulled.items) == 1
    assert pulled.items[0].payload["title"] == "Edited in WebUI"
    assert pulled.items[0].payload["notes"] == "New note"
    assert pulled.items[0].payload["page"] == 99
    assert pulled.items[0].payload["chapter"] == "Local chapter"


@pytest.mark.asyncio
async def test_pull_emits_tombstone_for_confirmed_remote_deletion(
    mock_creds: ClientCredentials, verified_bearer: VerifiedBearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_bookmark = AsyncMock(return_value={"id": 770})  # type: ignore[method-assign]
    client.get_personal_rating = AsyncMock(return_value=None)  # type: ignore[method-assign]
    client.get_bookmarks_for_book = AsyncMock(return_value=[])  # type: ignore[method-assign]
    service = MetadataService(official_client=client)
    await service.sync_metadata(
        GrimmlinkMetadataSyncRequest(
            bookId=77,
            bookmarks=[GrimmlinkBookmarkPayload(dedupeKey="bm:deleted-web", title="Gone")],
        ),
        creds=mock_creds,
    )

    pulled = await service.pull_metadata(book_id=77, creds=mock_creds)
    assert len(pulled.items) == 1
    assert pulled.items[0].dedupeKey == "bm:deleted-web"
    assert pulled.items[0].payload == {"deleted": True}
    history = await MetadataSyncStore.get_applied_record(
        "42", 77, "bookmark", "bm:deleted-web"
    )
    assert history is not None and history.is_deleted is True

