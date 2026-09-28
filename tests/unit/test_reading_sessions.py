"""Unit tests for Session 08 — Reading Sessions + Idempotency.

Verifies:
- Single session POST to Official Grimmory single POST.
- Batch session fan-out to deterministic Official single POSTs + result aggregation.
- Canonical idempotency key from preserved legacy identity: server/user/book/hash/start/end/device.
- Persist pending/committed state in SQLite and preserve unsupported legacy fields locally:
  book_hash, device, device_id, current_page, total_pages.
- Timeout after POST does not blindly create duplicate on retry (upstream probe).
- Restart recovery for pending sessions.
- Partial success in batch with per-item status.
- Same timestamps from different devices do not collide.
- Invalid duration (<= 0) and invalid order (endTime <= startTime) validation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.grimmlink import (
    GrimmlinkReadingSessionBatchRequest,
    GrimmlinkReadingSessionItemRequest,
    GrimmlinkReadingSessionSingleRequest,
)
from grimmlink_adapter.models.internal import ReadingSessionRecord
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import OfficialTimeoutError
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.request_auth import VerifiedBearer
from grimmlink_adapter.services.session_service import (
    SessionService,
    generate_session_idempotency_key,
    validate_session_timing,
)
from grimmlink_adapter.state.book_identity import BookIdentityStore
from grimmlink_adapter.state.database import get_connection
from grimmlink_adapter.state.migrations import apply_migrations
from grimmlink_adapter.state.reading_sessions import ReadingSessionStore


@pytest.fixture(autouse=True)
async def setup_test_db() -> None:
    """Ensure all migrations up to 010 are applied and clean tables before each test."""
    await apply_migrations()
    async with get_connection() as conn:
        await conn.execute("DELETE FROM reading_sessions_state")
        await conn.execute("DELETE FROM idempotency_keys")
        await conn.execute("DELETE FROM outbox")
        await conn.commit()


@pytest.fixture
def mock_creds() -> ClientCredentials:
    return ClientCredentials(username="testuser", md5_key="mock_md5_key")


@pytest.fixture
def verified_bearer() -> VerifiedBearer:
    return VerifiedBearer("mock_jwt_token", user_id=42)


# ============================================================================
# 1. Canonical Idempotency Key Tests
# ============================================================================


def test_canonical_idempotency_key_format() -> None:
    """Canonical idempotency key must preserve: server/user/book/hash/start/end/device."""
    server = "http://grimmory.local:8080"
    user = "42"
    book_id = 100
    book_hash = "d41d8cd98f00b204e9800998ecf8427e"
    start_time = "2026-09-28T10:00:00Z"
    end_time = "2026-09-28T10:30:00Z"
    device = "Kobo Clara"
    device_id = "kobo-uuid-123"

    key = generate_session_idempotency_key(
        server=server,
        user=user,
        book_id=book_id,
        book_hash=book_hash,
        start_time=start_time,
        end_time=end_time,
        device=device,
        device_id=device_id,
    )

    expected = "http://grimmory.local:8080/42/100/d41d8cd98f00b204e9800998ecf8427e/2026-09-28T10:00:00Z/2026-09-28T10:30:00Z/Kobo Clara:kobo-uuid-123"
    assert key == expected


def test_same_timestamps_different_devices_keys_are_distinct() -> None:
    """Same book/user/timestamps on different devices produce distinct idempotency keys."""
    key1 = generate_session_idempotency_key(
        server="http://grimmory:8080",
        user="1",
        book_id=42,
        book_hash="hash1",
        start_time="2026-09-28T10:00:00Z",
        end_time="2026-09-28T10:30:00Z",
        device="device-alpha",
    )
    key2 = generate_session_idempotency_key(
        server="http://grimmory:8080",
        user="1",
        book_id=42,
        book_hash="hash1",
        start_time="2026-09-28T10:00:00Z",
        end_time="2026-09-28T10:30:00Z",
        device="device-beta",
    )
    assert key1 != key2


# ============================================================================
# 2. Timing & Validation Tests
# ============================================================================


def test_validate_session_timing_valid() -> None:
    start_dt, end_dt = validate_session_timing(
        "2026-09-28T10:00:00Z", "2026-09-28T10:30:00Z", 1800
    )
    assert end_dt > start_dt


@pytest.mark.parametrize(
    ("start_time", "end_time", "duration"),
    [
        ("2026-09-28T10:30:00Z", "2026-09-28T10:00:00Z", 1800),  # endTime before startTime
        ("2026-09-28T10:00:00Z", "2026-09-28T10:00:00Z", 1800),  # endTime equals startTime
        ("2026-09-28T10:00:00Z", "2026-09-28T10:30:00Z", 0),  # zero duration
        ("2026-09-28T10:00:00Z", "2026-09-28T10:30:00Z", -60),  # negative duration
        ("invalid-time", "2026-09-28T10:30:00Z", 1800),  # malformed start
        ("2026-09-28T10:00:00Z", "invalid-time", 1800),  # malformed end
    ],
)
def test_validate_session_timing_invalid(start_time: str, end_time: str, duration: int) -> None:
    with pytest.raises(ValueError):
        validate_session_timing(start_time, end_time, duration)


# ============================================================================
# 3. Single Reading Session POST Tests
# ============================================================================


@pytest.mark.asyncio
async def test_record_single_session_success(
    monkeypatch: pytest.MonkeyPatch,
    mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    """Single session POST maps to Official POST and records COMMITTED state with unsupported fields."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    await BookIdentityStore.upsert(
        server=settings.GRIMMORY_BASE_URL.rstrip("/"),
        user=str(verified_bearer.user_id),
        current_hash="d41d8cd98f00b204e9800998ecf8427e",
        initial_hash="d41d8cd98f00b204e9800998ecf8427e",
        book_id=42,
        book_file_id=42,
        fmt="EPUB",
    )
    mock_client = OfficialGrimmoryClient()
    mock_client.create_reading_session = AsyncMock(return_value={"id": 501})  # type: ignore[method-assign]
    service = SessionService(mock_client)

    req = GrimmlinkReadingSessionSingleRequest(
        bookId=42,
        bookType="EPUB",
        bookHash="d41d8cd98f00b204e9800998ecf8427e",
        device="Kobo Clara",
        deviceId="kobo-uuid-123",
        startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T10:30:00Z",
        durationSeconds=1800,
        startProgress=40.0,
        endProgress=45.0,
        currentPage=100,
        totalPages=250,
    )

    success = await service.record_session(req, creds=mock_creds)
    assert success is True

    # 1. Official was called with supported fields only
    mock_client.create_reading_session.assert_awaited_once()  # type: ignore[attr-defined]
    called_payload, called_token = mock_client.create_reading_session.call_args[0]  # type: ignore[attr-defined]
    assert called_payload["bookId"] == 42
    assert called_payload["startTime"] == "2026-09-28T10:00:00Z"
    assert called_payload["endTime"] == "2026-09-28T10:30:00Z"
    assert called_payload["durationSeconds"] == 1800
    assert called_token == str(verified_bearer)
    # Unsupported fields must NOT be forwarded to Official
    assert "bookHash" not in called_payload
    assert "device" not in called_payload
    assert "deviceId" not in called_payload
    assert "currentPage" not in called_payload
    assert "totalPages" not in called_payload

    # 2. Local SQLite state preserves unsupported fields and is COMMITTED
    key = generate_session_idempotency_key(
        server=settings.GRIMMORY_BASE_URL.rstrip("/"),
        user=str(verified_bearer.user_id),
        book_id=42,
        book_hash="d41d8cd98f00b204e9800998ecf8427e",
        start_time="2026-09-28T10:00:00Z",
        end_time="2026-09-28T10:30:00Z",
        device="Kobo Clara",
        device_id="kobo-uuid-123",
    )
    rec = await ReadingSessionStore.get_by_idempotency_key(key)
    assert rec is not None
    assert rec.status == "COMMITTED"
    assert rec.official_session_id == 501
    assert rec.book_hash == "d41d8cd98f00b204e9800998ecf8427e"
    assert rec.device == "Kobo Clara"
    assert rec.device_id == "kobo-uuid-123"
    assert rec.current_page == 100
    assert rec.total_pages == 250


@pytest.mark.asyncio
async def test_stale_book_id_with_hash_uses_authoritative_mapping(
    monkeypatch: pytest.MonkeyPatch,
    mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    """A stale KOReader ID must not override a verified hash-to-book mapping."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    book_hash = "valid-hash-for-real-book"
    await BookIdentityStore.upsert(
        server=settings.GRIMMORY_BASE_URL.rstrip("/"),
        user=str(verified_bearer.user_id),
        current_hash=book_hash,
        initial_hash=book_hash,
        book_id=2042,
        book_file_id=7,
        fmt="EPUB",
    )

    mock_client = OfficialGrimmoryClient()
    mock_client.create_reading_session = AsyncMock(return_value={"id": 1501})  # type: ignore[method-assign]
    service = SessionService(mock_client)
    request = GrimmlinkReadingSessionSingleRequest(
        bookId=1100,
        bookHash=book_hash,
        startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T10:30:00Z",
        durationSeconds=1800,
    )

    assert await service.record_session(request, creds=mock_creds) is True
    payload = mock_client.create_reading_session.call_args.args[0]  # type: ignore[attr-defined]
    assert payload["bookId"] == 2042
    assert payload["bookId"] != request.bookId

    key = generate_session_idempotency_key(
        server=settings.GRIMMORY_BASE_URL.rstrip("/"),
        user=str(verified_bearer.user_id),
        book_id=2042,
        book_hash=book_hash,
        start_time=request.startTime,
        end_time=request.endTime,
    )
    record = await ReadingSessionStore.get_by_idempotency_key(key)
    assert record is not None
    assert record.book_id == 2042
    assert record.status == "COMMITTED"


@pytest.mark.asyncio
async def test_unresolvable_hash_is_rejected_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    """Unknown hash failures are auditable and excluded from pending recovery."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    mock_client = OfficialGrimmoryClient()
    mock_client.create_reading_session = AsyncMock()  # type: ignore[method-assign]
    service = SessionService(mock_client)
    request = GrimmlinkReadingSessionSingleRequest(
        bookId=1100,
        bookHash="missing-book-hash",
        startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T10:30:00Z",
        durationSeconds=1800,
    )

    with pytest.raises(HTTPException) as exc:
        await service.record_session(request, creds=mock_creds)
    assert exc.value.status_code == 422
    assert "rejected" in str(exc.value.detail).lower()
    mock_client.create_reading_session.assert_not_called()  # type: ignore[attr-defined]

    key = generate_session_idempotency_key(
        server=settings.GRIMMORY_BASE_URL.rstrip("/"),
        user=str(verified_bearer.user_id),
        book_id=1100,
        book_hash="missing-book-hash",
        start_time=request.startTime,
        end_time=request.endTime,
    )
    record = await ReadingSessionStore.get_by_idempotency_key(key)
    assert record is not None
    assert record.status == "REJECTED"
    assert record.official_session_id is None
    assert "No verified book" in (record.last_error or "")
    assert await ReadingSessionStore.list_pending(
        settings.GRIMMORY_BASE_URL.rstrip("/"), str(verified_bearer.user_id)
    ) == []


@pytest.mark.asyncio
async def test_record_single_session_invalid_duration_order_rejected(
    monkeypatch: pytest.MonkeyPatch,
    mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    """Single session POST rejects invalid duration or order upfront with HTTP 400."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    mock_client = OfficialGrimmoryClient()
    mock_client.create_reading_session = AsyncMock()  # type: ignore[method-assign]
    service = SessionService(mock_client)

    # 1. endTime < startTime
    req_bad_order = GrimmlinkReadingSessionSingleRequest(
        bookId=42,
        startTime="2026-09-28T11:00:00Z",
        endTime="2026-09-28T10:00:00Z",
        durationSeconds=1800,
    )
    with pytest.raises(HTTPException) as exc:
        await service.record_session(req_bad_order, creds=mock_creds)
    assert exc.value.status_code == 400
    assert "order" in exc.value.detail.lower()

    # 2. durationSeconds <= 0
    req_bad_dur = GrimmlinkReadingSessionSingleRequest(
        bookId=42,
        startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T11:00:00Z",
        durationSeconds=-10,
    )
    with pytest.raises(HTTPException) as exc:
        await service.record_session(req_bad_dur, creds=mock_creds)
    assert exc.value.status_code == 400
    assert "duration" in exc.value.detail.lower()

    # Upstream was never called
    mock_client.create_reading_session.assert_not_called()  # type: ignore[attr-defined]


# ============================================================================
# 4. Batch Reading Sessions POST & Partial Success Tests
# ============================================================================


@pytest.mark.asyncio
async def test_record_sessions_batch_fanout_and_aggregation(
    monkeypatch: pytest.MonkeyPatch,
    mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    """Batch fans out to deterministic single Official POSTs and aggregates results."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    await BookIdentityStore.upsert(
        server=settings.GRIMMORY_BASE_URL.rstrip("/"),
        user=str(verified_bearer.user_id),
        current_hash="hash123",
        initial_hash="hash123",
        book_id=42,
        book_file_id=42,
        fmt="EPUB",
    )
    mock_client = OfficialGrimmoryClient()
    mock_client.create_reading_session = AsyncMock(  # type: ignore[method-assign]
        side_effect=[{"id": 101}, {"id": 102}]
    )
    service = SessionService(mock_client)

    batch_req = GrimmlinkReadingSessionBatchRequest(
        bookId=42,
        bookHash="hash123",
        device="Kobo Clara",
        deviceId="kobo-dev-1",
        sessions=[
            GrimmlinkReadingSessionItemRequest(
                startTime="2026-09-28T10:00:00Z",
                endTime="2026-09-28T10:30:00Z",
                durationSeconds=1800,
            ),
            GrimmlinkReadingSessionItemRequest(
                startTime="2026-09-28T10:30:00Z",
                endTime="2026-09-28T11:00:00Z",
                durationSeconds=1800,
            ),
        ],
    )

    resp = await service.record_sessions_batch(batch_req, creds=mock_creds)
    assert resp.totalRequested == 2
    assert resp.successCount == 2
    assert len(resp.results) == 2
    assert resp.results[0].status == "created"
    assert resp.results[0].sessionId == 101
    assert resp.results[1].status == "created"
    assert resp.results[1].sessionId == 102
    assert mock_client.create_reading_session.call_count == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_record_sessions_batch_partial_success(
    monkeypatch: pytest.MonkeyPatch,
    mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    """Batch with valid and invalid items processes valid ones and marks errors without aborting."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    mock_client = OfficialGrimmoryClient()
    mock_client.create_reading_session = AsyncMock(return_value={"id": 201})  # type: ignore[method-assign]
    service = SessionService(mock_client)

    batch_req = GrimmlinkReadingSessionBatchRequest(
        bookId=42,
        sessions=[
            GrimmlinkReadingSessionItemRequest(  # Valid item 0
                startTime="2026-09-28T10:00:00Z",
                endTime="2026-09-28T10:30:00Z",
                durationSeconds=1800,
            ),
            GrimmlinkReadingSessionItemRequest(  # Invalid duration item 1
                startTime="2026-09-28T10:30:00Z",
                endTime="2026-09-28T11:00:00Z",
                durationSeconds=-5,
            ),
            GrimmlinkReadingSessionItemRequest(  # Invalid order item 2
                startTime="2026-09-28T12:00:00Z",
                endTime="2026-09-28T11:00:00Z",
                durationSeconds=3600,
            ),
        ],
    )

    resp = await service.record_sessions_batch(batch_req, creds=mock_creds)
    assert resp.totalRequested == 3
    assert resp.successCount == 1  # Only item 0 succeeded
    assert len(resp.results) == 3

    assert resp.results[0].status == "created"
    assert resp.results[0].sessionId == 201

    assert resp.results[1].status == "error"
    assert resp.results[1].sessionId is None
    assert "duration" in (resp.results[1].message or "").lower()

    assert resp.results[2].status == "error"
    assert resp.results[2].sessionId is None
    assert "order" in (resp.results[2].message or "").lower()

    # Only 1 upstream POST was attempted
    mock_client.create_reading_session.assert_awaited_once()  # type: ignore[attr-defined]


# ============================================================================
# 5. Duplicate Retry Tests (Single and Batch)
# ============================================================================


@pytest.mark.asyncio
async def test_duplicate_retry_single_does_not_call_upstream(
    monkeypatch: pytest.MonkeyPatch,
    mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    """Duplicate single session retry returns idempotent success without calling upstream."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    mock_client = OfficialGrimmoryClient()
    mock_client.create_reading_session = AsyncMock(return_value={"id": 301})  # type: ignore[method-assign]
    service = SessionService(mock_client)

    req = GrimmlinkReadingSessionSingleRequest(
        bookId=42,
        startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T10:30:00Z",
        durationSeconds=1800,
    )

    # First attempt: succeeds
    res1 = await service.record_session(req, creds=mock_creds)
    assert res1 is True
    assert mock_client.create_reading_session.call_count == 1  # type: ignore[attr-defined]

    # Second attempt (duplicate retry): returns True without extra POST
    res2 = await service.record_session(req, creds=mock_creds)
    assert res2 is True
    assert mock_client.create_reading_session.call_count == 1  # Still 1!


@pytest.mark.asyncio
async def test_duplicate_retry_batch_returns_duplicate_status(
    monkeypatch: pytest.MonkeyPatch,
    mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    """Duplicate batch retry returns status 'duplicate' with original session IDs."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    mock_client = OfficialGrimmoryClient()
    mock_client.create_reading_session = AsyncMock(return_value={"id": 401})  # type: ignore[method-assign]
    service = SessionService(mock_client)

    batch_req = GrimmlinkReadingSessionBatchRequest(
        bookId=42,
        sessions=[
            GrimmlinkReadingSessionItemRequest(
                startTime="2026-09-28T10:00:00Z",
                endTime="2026-09-28T10:30:00Z",
                durationSeconds=1800,
            )
        ],
    )

    # Initial batch
    resp1 = await service.record_sessions_batch(batch_req, creds=mock_creds)
    assert resp1.results[0].status == "created"
    assert resp1.results[0].sessionId == 401
    assert mock_client.create_reading_session.call_count == 1  # type: ignore[attr-defined]

    # Duplicate batch retry
    resp2 = await service.record_sessions_batch(batch_req, creds=mock_creds)
    assert resp2.results[0].status == "duplicate"
    assert resp2.results[0].sessionId == 401
    assert resp2.successCount == 1
    # Upstream was not called again!
    assert mock_client.create_reading_session.call_count == 1  # type: ignore[attr-defined]


# ============================================================================
# 6. Timeout After Possible Commit & Upstream Probe Tests
# ============================================================================


@pytest.mark.asyncio
async def test_timeout_after_possible_commit_probes_upstream(
    monkeypatch: pytest.MonkeyPatch,
    mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    """When a POST times out, retry must probe Official first and not create a duplicate."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    mock_client = OfficialGrimmoryClient()

    # Attempt 1: create_reading_session times out!
    mock_client.create_reading_session = AsyncMock(  # type: ignore[method-assign]
        side_effect=OfficialTimeoutError(
            "Upstream timeout on POST /reading-sessions",
            method="POST",
            url="/api/v1/reading-sessions",
        )
    )
    mock_client.get_reading_sessions_for_book = AsyncMock(return_value=[])  # type: ignore[method-assign]
    service = SessionService(mock_client)

    req = GrimmlinkReadingSessionSingleRequest(
        bookId=42,
        startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T10:30:00Z",
        durationSeconds=1800,
    )

    # Attempt 1 fails with 504 Timeout
    with pytest.raises(HTTPException) as exc:
        await service.record_session(req, creds=mock_creds)
    assert exc.value.status_code == 504

    # Verify SQLite recorded PENDING with error
    key = generate_session_idempotency_key(
        server=settings.GRIMMORY_BASE_URL.rstrip("/"),
        user=str(verified_bearer.user_id),
        book_id=42,
        start_time="2026-09-28T10:00:00Z",
        end_time="2026-09-28T10:30:00Z",
    )
    rec = await ReadingSessionStore.get_by_idempotency_key(key)
    assert rec is not None
    assert rec.status == "PENDING"
    assert rec.retry_count == 1
    assert "timeout" in (rec.last_error or "").lower()

    # Now, Official Grimmory DID commit it in the background:
    mock_client.get_reading_sessions_for_book = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "id": 888,
                "bookId": 42,
                "startTime": "2026-09-28T10:00:00+00:00",
                "endTime": "2026-09-28T10:30:00+00:00",
            }
        ]
    )
    # Attempt 2 (Retry): Re-send the session
    # Reset create_reading_session to ensure it is NEVER called!
    mock_client.create_reading_session = AsyncMock()  # type: ignore[method-assign]

    res2 = await service.record_session(req, creds=mock_creds)
    assert res2 is True

    # Critical assertions:
    # 1. create_reading_session was NOT called (no blind duplicate created!)
    mock_client.create_reading_session.assert_not_called()  # type: ignore[attr-defined]
    # 2. get_reading_sessions_for_book was probed
    mock_client.get_reading_sessions_for_book.assert_awaited_once()  # type: ignore[attr-defined]
    # 3. Local SQLite state is updated to COMMITTED with official_session_id=888
    rec_updated = await ReadingSessionStore.get_by_idempotency_key(key)
    assert rec_updated is not None
    assert rec_updated.status == "COMMITTED"
    assert rec_updated.official_session_id == 888


# ============================================================================
# 7. Restart Pending Recovery Tests
# ============================================================================


@pytest.mark.asyncio
async def test_restart_pending_recovery_reconciles_upstream() -> None:
    """Recover pending sessions after restart by checking upstream first and replaying if absent."""
    mock_client = OfficialGrimmoryClient()
    service = SessionService(mock_client)

    # Seed 2 pending records in SQLite
    key1 = "http://localhost:8080/42/10/h1/2026-09-28T08:00:00Z/2026-09-28T08:30:00Z/dev"
    key2 = "http://localhost:8080/42/10/h2/2026-09-28T09:00:00Z/2026-09-28T09:30:00Z/dev"

    await ReadingSessionStore.record_session(
        ReadingSessionRecord(
            idempotency_key=key1,
            server="http://localhost:8080",
            user_id="42",
            book_id=10,
            start_time="2026-09-28T08:00:00Z",
            end_time="2026-09-28T08:30:00Z",
            duration_seconds=1800,
            status="PENDING",
        )
    )
    await ReadingSessionStore.record_session(
        ReadingSessionRecord(
            idempotency_key=key2,
            server="http://localhost:8080",
            user_id="42",
            book_id=10,
            start_time="2026-09-28T09:00:00Z",
            end_time="2026-09-28T09:30:00Z",
            duration_seconds=1800,
            status="PENDING",
        )
    )

    # Session 1 exists upstream (committed before restart)
    # Session 2 does NOT exist upstream (never committed)
    mock_client.get_reading_sessions_for_book = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "id": 701,
                "startTime": "2026-09-28T08:00:00Z",
                "endTime": "2026-09-28T08:30:00Z",
            }
        ]
    )
    mock_client.create_reading_session = AsyncMock(return_value={"id": 702})  # type: ignore[method-assign]

    reconciled = await service.recover_pending_sessions(
        bearer_token="valid_token", user_id="42", server="http://localhost:8080"
    )
    assert reconciled == 1

    # Session 1 adopted upstream ID 701 without calling POST
    rec1 = await ReadingSessionStore.get_by_idempotency_key(key1)
    assert rec1 is not None
    assert rec1.status == "COMMITTED"
    assert rec1.official_session_id == 701

    # An absent GET cannot prove a previous POST will never commit: fail closed.
    rec2 = await ReadingSessionStore.get_by_idempotency_key(key2)
    assert rec2 is not None
    assert rec2.status == "PENDING"
    assert rec2.official_session_id is None
    mock_client.create_reading_session.assert_not_called()  # type: ignore[attr-defined]


# ============================================================================
# 8. Same Timestamps Different Devices End-to-End
# ============================================================================


@pytest.mark.asyncio
async def test_same_timestamps_different_devices_both_created(
    monkeypatch: pytest.MonkeyPatch,
    mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    """Reading sessions with identical timestamps on different devices are both recorded."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    mock_client = OfficialGrimmoryClient()
    mock_client.create_reading_session = AsyncMock(  # type: ignore[method-assign]
        side_effect=[{"id": 801}, {"id": 802}]
    )
    service = SessionService(mock_client)

    # Device 1
    req1 = GrimmlinkReadingSessionSingleRequest(
        bookId=42,
        startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T10:30:00Z",
        durationSeconds=1800,
        device="Device Alpha",
    )
    res1 = await service.record_session(req1, creds=mock_creds)
    assert res1 is True

    # Device 2 (same start/end timestamps!)
    req2 = GrimmlinkReadingSessionSingleRequest(
        bookId=42,
        startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T10:30:00Z",
        durationSeconds=1800,
        device="Device Beta",
    )
    res2 = await service.record_session(req2, creds=mock_creds)
    assert res2 is True

    # Both must be sent upstream and committed
    assert mock_client.create_reading_session.call_count == 2  # type: ignore[attr-defined]


# ============================================================================
# 9. API Route Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_api_route_record_single_session_202(
    test_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/grimmlink/v1/reading-sessions returns 202 Accepted on success."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=VerifiedBearer("tok", user_id=42)),
    )
    monkeypatch.setattr(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.create_reading_session",
        AsyncMock(return_value={"id": 901}),
    )

    payload = {
        "bookId": 42,
        "startTime": "2026-09-28T10:00:00Z",
        "endTime": "2026-09-28T10:30:00Z",
        "durationSeconds": 1800,
    }
    resp = await test_client.post(
        "/api/grimmlink/v1/reading-sessions",
        json=payload,
        headers={"x-auth-user": "test", "x-auth-key": "key"},
    )
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_api_route_get_reading_sessions_returns_official_history(
    test_client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Advertised reading-session GET uses the authenticated Official history."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=VerifiedBearer("tok", user_id=42)),
    )
    get_history = AsyncMock(return_value=[{"id": 55, "bookId": 42}])
    monkeypatch.setattr(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.get_reading_sessions_for_book",
        get_history,
    )
    resp = await test_client.get(
        "/api/grimmlink/v1/reading-sessions?bookId=42",
        headers={"x-auth-user": "test", "x-auth-key": "key"},
    )
    assert resp.status_code == 200
    assert resp.json() == [{"id": 55, "bookId": 42}]
    get_history.assert_awaited_once_with(42, "tok", page=0, size=50)


@pytest.mark.asyncio
async def test_retry_defers_when_upstream_probe_fails(
    monkeypatch: pytest.MonkeyPatch, mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_reading_session = AsyncMock(  # type: ignore[method-assign]
        side_effect=OfficialTimeoutError("timeout")
    )
    service = SessionService(client)
    req = GrimmlinkReadingSessionSingleRequest(
        bookId=42, startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T10:30:00Z", durationSeconds=1800,
    )
    with pytest.raises(HTTPException) as first:
        await service.record_session(req, creds=mock_creds)
    assert first.value.status_code == 504
    client.get_reading_sessions_for_book = AsyncMock(side_effect=RuntimeError("GET failed"))  # type: ignore[method-assign]
    client.create_reading_session = AsyncMock(return_value={"id": 999})  # type: ignore[method-assign]
    with pytest.raises(HTTPException) as retry:
        await service.record_session(req, creds=mock_creds)
    assert retry.value.status_code == 503
    client.create_reading_session.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_retry_scans_later_upstream_pages(
    monkeypatch: pytest.MonkeyPatch, mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_reading_session = AsyncMock(side_effect=OfficialTimeoutError("timeout"))  # type: ignore[method-assign]
    service = SessionService(client)
    req = GrimmlinkReadingSessionSingleRequest(
        bookId=43, startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T10:30:00Z", durationSeconds=1800,
    )
    with pytest.raises(HTTPException):
        await service.record_session(req, creds=mock_creds)
    first_page = [{"id": i, "startTime": "other", "endTime": "other"} for i in range(100)]
    client.get_reading_sessions_for_book = AsyncMock(  # type: ignore[method-assign]
        side_effect=[first_page, [{"id": 501, "startTime": req.startTime, "endTime": req.endTime}]]
    )
    client.create_reading_session = AsyncMock(return_value={"id": 502})  # type: ignore[method-assign]
    assert await service.record_session(req, creds=mock_creds) is True
    client.create_reading_session.assert_not_called()  # type: ignore[attr-defined]
    assert client.get_reading_sessions_for_book.call_count == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("first_response", [{"id": 601}, {"status": "created"}])
async def test_same_interval_other_device_is_not_falsely_adopted(
    monkeypatch: pytest.MonkeyPatch, mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer, first_response: dict[str, int | str],
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_reading_session = AsyncMock(  # type: ignore[method-assign]
        side_effect=[first_response, OfficialTimeoutError("timeout")]
    )
    service = SessionService(client)
    base = {
        "bookId": 44, "startTime": "2026-09-28T10:00:00Z",
        "endTime": "2026-09-28T10:30:00Z", "durationSeconds": 1800,
    }
    assert await service.record_session(
        GrimmlinkReadingSessionSingleRequest(**base, device="A"), creds=mock_creds
    )
    other = GrimmlinkReadingSessionSingleRequest(**base, device="B")
    with pytest.raises(HTTPException):
        await service.record_session(other, creds=mock_creds)
    client.get_reading_sessions_for_book = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"id": 601, "startTime": other.startTime, "endTime": other.endTime}]
    )
    client.create_reading_session = AsyncMock(return_value={"id": 602})  # type: ignore[method-assign]
    with pytest.raises(HTTPException) as retry:
        await service.record_session(other, creds=mock_creds)
    assert retry.value.status_code == 503
    client.create_reading_session.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_concurrent_same_key_posts_only_once(
    monkeypatch: pytest.MonkeyPatch, mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_post(payload: dict[str, object], token: str) -> dict[str, int]:
        entered.set()
        await release.wait()
        return {"id": 701}

    client = OfficialGrimmoryClient()
    client.create_reading_session = AsyncMock(side_effect=slow_post)  # type: ignore[method-assign]
    service = SessionService(client)
    req = GrimmlinkReadingSessionSingleRequest(
        bookId=45, startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T10:30:00Z", durationSeconds=1800,
    )
    first = asyncio.create_task(service.record_session(req, creds=mock_creds))
    await asyncio.wait_for(entered.wait(), timeout=5)
    with pytest.raises(HTTPException) as second:
        await service.record_session(req, creds=mock_creds)
    assert second.value.status_code == 503
    release.set()
    assert await first is True
    client.create_reading_session.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_recovery_scoped_to_authenticated_owner() -> None:
    client = OfficialGrimmoryClient()
    client.get_reading_sessions_for_book = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"id": 801, "startTime": "2026-09-28T10:00:00Z", "endTime": "2026-09-28T10:30:00Z"}]
    )
    service = SessionService(client)
    for user in ("42", "99"):
        await ReadingSessionStore.record_session(ReadingSessionRecord(
            idempotency_key=f"server/{user}/book/session", server="server", user_id=user,
            book_id=46, start_time="2026-09-28T10:00:00Z",
            end_time="2026-09-28T10:30:00Z", duration_seconds=1800,
        ))
    assert await service.recover_pending_sessions("token-42", "42", "server") == 1
    assert (await ReadingSessionStore.get_by_idempotency_key("server/42/book/session")).status == "COMMITTED"  # type: ignore[union-attr]
    assert (await ReadingSessionStore.get_by_idempotency_key("server/99/book/session")).status == "PENDING"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_sub_one_percent_progress_keeps_percent_units(
    monkeypatch: pytest.MonkeyPatch, mock_creds: ClientCredentials,
    verified_bearer: VerifiedBearer,
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value=verified_bearer),
    )
    client = OfficialGrimmoryClient()
    client.create_reading_session = AsyncMock(return_value={"id": 901})  # type: ignore[method-assign]
    service = SessionService(client)
    req = GrimmlinkReadingSessionSingleRequest(
        bookId=47, bookType="EPUB", startTime="2026-09-28T10:00:00Z",
        endTime="2026-09-28T10:30:00Z", durationSeconds=1800,
        startProgress=0.33, endProgress=1.0, startLocation="cfi-a", endLocation="cfi-b",
    )
    assert await service.record_session(req, creds=mock_creds)
    payload = client.create_reading_session.call_args.args[0]  # type: ignore[attr-defined]
    assert payload["startProgress"] == 0.0033
    assert payload["endProgress"] == 0.01
    assert payload["bookType"] == "EPUB"
    assert payload["startLocation"] == "cfi-a"
    assert payload["endLocation"] == "cfi-b"


@pytest.mark.asyncio
async def test_existing_v10_reading_session_db_upgrades_book_type(tmp_path: Path) -> None:
    migrations = Path(__file__).resolve().parents[2] / "migrations"
    async with aiosqlite.connect(tmp_path / "v10.db") as conn:
        await conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        await conn.executescript((migrations / "010_reading_sessions.sql").read_text(encoding="utf-8"))
        await conn.executescript(
            (migrations / "011_reading_session_book_type.sql").read_text(encoding="utf-8")
        )
        async with conn.execute("PRAGMA table_info(reading_sessions_state)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
    assert "book_type" in columns
