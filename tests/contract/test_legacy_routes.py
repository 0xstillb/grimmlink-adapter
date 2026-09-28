"""Contract tests ensuring legacy GrimmLink wire contract endpoints exist and match signatures."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient

from grimmlink_adapter.models.internal import BookHashEntry
from grimmlink_adapter.official.exceptions import OfficialAuthError
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
    """Session 06 progress and PDF bridge capabilities are advertised."""
    resp = await test_client.get("/api/grimmlink/v1/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert data["apiVersion"] == "v1"
    assert data["webUiProgress"] is True
    assert data["progressSync"] is True
    assert data["readingSessions"] is True
    assert data["metadataSync"] is True
    assert data["pdfBridge"] is True
    assert data["shelves"] is True


@pytest.mark.asyncio
async def test_shelves_list_requires_working_upstream(test_client: AsyncClient) -> None:
    with patch(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.get_koreader_auth",
        side_effect=OfficialAuthError("Invalid credentials", status_code=401),
    ):
        resp = await test_client.get("/api/grimmlink/v1/shelves", headers=AUTH_HEADERS)
    assert resp.status_code in (401, 502)


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
async def test_regular_shelf_removal_calls_official_mutation(
    test_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regular removal returns the frozen success payload after Official succeeds."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.shelf_service.get_official_bearer",
        AsyncMock(return_value="verified"),
    )
    assign = AsyncMock(return_value={})
    monkeypatch.setattr(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.assign_shelves_to_books",
        assign,
    )
    resp = await test_client.post(
        "/api/grimmlink/v1/shelves/regular/1/books/42/remove",
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"
    assign.assert_awaited_once()


@pytest.mark.asyncio
async def test_progress_sync_contract(test_client: AsyncClient) -> None:
    with patch(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.get_koreader_progress",
        new=AsyncMock(return_value={
            "bookHash": "sample_book_hash_123",
            "fileFormat": "EPUB",
            "progress": "/body/3",
            "location": "/body/3",
            "percentage": 0.0033,
            "timestamp": 100,
        }),
    ), patch(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.update_koreader_progress",
        new=AsyncMock(return_value={}),
    ):
        get_resp = await test_client.get(
            "/api/grimmlink/v1/syncs/progress/sample_book_hash_123",
            headers=AUTH_HEADERS,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["percentage"] == pytest.approx(0.33, abs=0.0001)

        put_resp = await test_client.put(
            "/api/grimmlink/v1/syncs/progress",
            json={
                "book_hash": "sample_book_hash_123",
                "fileFormat": "EPUB",
                "progress": "/body/3",
                "location": "/body/3",
                "timestamp": 101,
            },
            headers=AUTH_HEADERS,
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["status"] == "progress updated"


@pytest.mark.asyncio
async def test_metadata_sync_routes_contract(
    test_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Metadata sync routes return frozen schema responses with verified Official bridge."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.metadata_service.get_official_bearer",
        AsyncMock(return_value="verified_token"),
    )
    monkeypatch.setattr(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.update_personal_rating",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.get_personal_rating",
        AsyncMock(return_value=4),
    )
    monkeypatch.setattr(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.get_bookmarks_for_book",
        AsyncMock(return_value=[]),
    )

    # 1. POST /syncs/metadata
    resp = await test_client.post(
        "/api/grimmlink/v1/syncs/metadata",
        json={
            "bookId": 42,
            "rating": {"value": 8, "scale": 10, "dedupeKey": "rating:42:koreader"},
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["appliedCount"] == 1
    assert data["results"]["rating"]["status"] == "SUCCESS"

    # 2. GET /syncs/metadata
    pull_resp = await test_client.get(
        "/api/grimmlink/v1/syncs/metadata?bookId=42",
        headers=AUTH_HEADERS,
    )
    assert pull_resp.status_code == 200
    pull_data = pull_resp.json()
    assert pull_data["ok"] is True
    assert pull_data["bookId"] == 42
    assert len(pull_data["items"]) == 1
    assert pull_data["items"][0]["type"] == "rating"

    # 3. POST /syncs/metadata/batch
    batch_resp = await test_client.post(
        "/api/grimmlink/v1/syncs/metadata/batch",
        json={"bookId": 42},
        headers=AUTH_HEADERS,
    )
    assert batch_resp.status_code == 200
    batch_data = batch_resp.json()
    assert batch_data["ok"] is True
    assert "push" in batch_data
    assert "pull" in batch_data


@pytest.mark.asyncio
async def test_reading_sessions_batch_calls_official_session(
    test_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading sessions batch fans out to Official single session POST and aggregates."""
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value="verified-bearer"),
    )
    mock_create = AsyncMock(return_value={"id": 101})
    monkeypatch.setattr(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.create_reading_session",
        mock_create,
    )
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
        ],
    }
    resp = await test_client.post(
        "/api/grimmlink/v1/reading-sessions/batch",
        json=payload,
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["totalRequested"] == 1
    assert data["successCount"] == 1
    assert len(data["results"]) == 1
    assert data["results"][0]["status"] == "created"
    assert data["results"][0]["sessionId"] == 101
    mock_create.assert_awaited_once()



@pytest.mark.asyncio
async def test_reading_session_history_route_returns_official_items(
    test_client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "grimmlink_adapter.services.session_service.get_official_bearer",
        AsyncMock(return_value="verified-bearer"),
    )
    monkeypatch.setattr(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.get_reading_sessions_for_book",
        AsyncMock(return_value=[{"id": 77, "bookId": 42}]),
    )
    resp = await test_client.get(
        "/api/grimmlink/v1/reading-sessions?bookId=42", headers=AUTH_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json() == [{"id": 77, "bookId": 42}]


@pytest.mark.asyncio
async def test_read_status_discovery_returns_official_values(test_client: AsyncClient) -> None:
    resp = await test_client.get("/api/grimmlink/v1/books/read-statuses", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "READ" in resp.json()["statuses"]


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
    with patch(
        "grimmlink_adapter.api.books.get_official_bearer",
        new=AsyncMock(return_value="verified-bearer"),
    ), patch(
        "grimmlink_adapter.official.client.OfficialGrimmoryClient.update_read_status",
        new=AsyncMock(return_value=[{
            "bookId": 42,
            "readStatus": "READ",
            "readStatusModifiedTime": "2026-09-27T12:00:00Z",
        }]),
    ):
        resp = await test_client.put(
            "/api/grimmlink/v1/books/42/status",
            json={"status": "COMPLETED"},
            headers=AUTH_HEADERS,
        )
    assert resp.status_code == 200
    assert resp.json() == {"bookId": 42, "status": "READ", "updated": True}
