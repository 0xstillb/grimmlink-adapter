"""Contract tests ensuring legacy GrimmLink wire contract endpoints exist and match signatures."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient

from grimmlink_adapter.models.internal import BookHashEntry
from grimmlink_adapter.state.cache import BookHashCache

AUTH_HEADERS = {
    "x-auth-user": "test_reader",
    "x-auth-key": "098f6bcd4621d373cade4e832627b4f6",  # md5('test')
}


@pytest.mark.asyncio
async def test_auth_endpoint_requires_credentials(test_client: AsyncClient) -> None:
    resp = await test_client.get("/api/grimmlink/v1/auth")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_endpoint_rejects_invalid_credentials(test_client: AsyncClient) -> None:
    """Issue 1 fix verification: Invalid credentials rejected with 401."""
    with patch(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.get_koreader_auth",
        side_effect=httpx.HTTPStatusError(
            "Unauthorized",
            request=httpx.Request("GET", "http://test"),
            response=httpx.Response(401),
        ),
    ):
        resp = await test_client.get("/api/grimmlink/v1/auth", headers=AUTH_HEADERS)
        assert resp.status_code == 401
        assert "invalid" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_auth_endpoint_fails_when_upstream_unreachable(test_client: AsyncClient) -> None:
    """Issue 1 fix verification: Unreachable upstream returns 502 Bad Gateway."""
    with patch(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.get_koreader_auth",
        side_effect=httpx.ConnectError("Connection refused"),
    ):
        resp = await test_client.get("/api/grimmlink/v1/auth", headers=AUTH_HEADERS)
        assert resp.status_code == 502
        assert "unreachable" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_auth_endpoint_success_with_valid_upstream(test_client: AsyncClient) -> None:
    """Issue 1 fix verification: Valid credentials verified by Official succeed."""
    mock_auth = {
        "userId": 42,
        "syncEnabled": True,
        "syncWithWebReader": True,
    }
    with patch(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.get_koreader_auth",
        new=AsyncMock(return_value=mock_auth),
    ):
        resp = await test_client.get("/api/grimmlink/v1/auth", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["username"] == "test_reader"
        assert data["userId"] == 42
        assert data["syncEnabled"] is True
        assert data["syncWithWebReader"] is True


@pytest.mark.asyncio
async def test_capabilities_endpoint_contract(test_client: AsyncClient) -> None:
    """Issue 2 fix verification: Capabilities do not claim mutations are active in scaffold."""
    resp = await test_client.get("/api/grimmlink/v1/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert data["apiVersion"] == "v1"
    # In Session 00 (scaffold), mutations are NOT active yet
    assert data["progressSync"] is False
    assert data["readingSessions"] is False
    assert data["metadataSync"] is False
    assert data["pdfBridge"] is False
    assert data["shelves"] is False


@pytest.mark.asyncio
async def test_shelves_list_unavailable_in_scaffold(test_client: AsyncClient) -> None:
    resp = await test_client.get("/api/grimmlink/v1/shelves", headers=AUTH_HEADERS)
    assert resp.status_code == 501
    assert "shelf listing is unavailable" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_magic_shelf_removal_rejection_invariant(test_client: AsyncClient) -> None:
    """Non-negotiable invariant: Magic Shelf removal is rule-derived; manual removal is rejected."""
    resp = await test_client.post(
        "/api/grimmlink/v1/shelves/magic/100/books/42/remove",
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail", "")
    assert "magic" in detail.lower()
    assert "rule-derived" in detail.lower()


@pytest.mark.asyncio
async def test_regular_shelf_removal_returns_501_in_scaffold(test_client: AsyncClient) -> None:
    """Issue 2 fix verification: Shelf removal mutation returns 501 in scaffold."""
    resp = await test_client.post(
        "/api/grimmlink/v1/shelves/regular/1/books/42/remove",
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 501
    assert "not supported in session 00 scaffold" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_progress_sync_contract(test_client: AsyncClient) -> None:
    # GET must not invent zero progress while Official reads are unimplemented.
    get_resp = await test_client.get(
        "/api/grimmlink/v1/syncs/progress/sample_book_hash_123",
        headers=AUTH_HEADERS,
    )
    assert get_resp.status_code == 501

    # PUT progress returns 501 Not Implemented in scaffold (Issue 2 fix)
    payload = {
        "book_hash": "sample_book_hash_123",
        "current_page": 55,
        "total_pages": 16653,
    }
    put_resp = await test_client.put(
        "/api/grimmlink/v1/syncs/progress",
        json=payload,
        headers=AUTH_HEADERS,
    )
    assert put_resp.status_code == 501
    assert "not supported in session 00 scaffold" in put_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_metadata_sync_mutations_return_501_in_scaffold(test_client: AsyncClient) -> None:
    """Issue 2 fix verification: Metadata sync mutations return 501 in scaffold."""
    resp = await test_client.post(
        "/api/grimmlink/v1/syncs/metadata",
        json={"items": [{"type": "RATING", "rating": 5.0}]},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 501

    batch_resp = await test_client.post(
        "/api/grimmlink/v1/syncs/metadata/batch",
        json={"items": []},
        headers=AUTH_HEADERS,
    )
    assert batch_resp.status_code == 501


@pytest.mark.asyncio
async def test_reading_sessions_batch_returns_501_in_scaffold(test_client: AsyncClient) -> None:
    """Issue 2 fix verification: Reading sessions batch returns 501 in scaffold."""
    payload = {
        "bookId": 10,
        "sessions": [
            {
                "startTime": "2026-09-26T11:00:00Z",
                "endTime": "2026-09-26T11:45:00Z",
                "durationSeconds": 2700,
                "startPage": 10,
                "endPage": 35,
            }
        ]
    }
    resp = await test_client.post(
        "/api/grimmlink/v1/reading-sessions/batch",
        json=payload,
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 501
    assert "not supported in session 00 scaffold" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/grimmlink/v1/shelves/regular/1/books",
        "/api/grimmlink/v1/shelves/magic/100/books",
        "/api/grimmlink/v1/syncs/metadata",
        "/api/grimmlink/v1/reading-sessions?bookId=42",
        "/api/grimmlink/v1/books/42/download",
        "/api/grimmlink/v1/books/read-statuses",
    ],
)
async def test_unimplemented_reads_never_return_empty_success(
    test_client: AsyncClient, path: str
) -> None:
    resp = await test_client.get(path, headers=AUTH_HEADERS)
    assert resp.status_code == 501


@pytest.mark.asyncio
async def test_book_hash_cache_is_not_exposed_before_access_checks(
    test_client: AsyncClient,
) -> None:
    await BookHashCache.put(
        BookHashEntry(book_hash="cached-book-123", book_id=42, title="Private Book")
    )
    resp = await test_client.get(
        "/api/grimmlink/v1/books/by-hash/cached-book-123",
        headers={"x-auth-user": "unknown", "x-auth-key": "invalid"},
    )
    # Route is now live (Session 03), but with invalid credentials the
    # resolution should fail WITHOUT leaking any cached book metadata.
    # The endpoint returns 401 (auth check) before any book lookup.
    assert resp.status_code in (401, 502)
    assert "Private Book" not in resp.text


@pytest.mark.asyncio
async def test_read_status_does_not_report_unperformed_update(
    test_client: AsyncClient,
) -> None:
    resp = await test_client.put(
        "/api/grimmlink/v1/books/42/status",
        json={"status": "COMPLETED"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 501
    assert "updated" not in resp.json()
