"""Contract tests ensuring legacy GrimmLink wire contract endpoints exist and match signatures."""

import pytest
from httpx import AsyncClient

AUTH_HEADERS = {
    "x-auth-user": "test_reader",
    "x-auth-key": "098f6bcd4621d373cade4e832627b4f6",  # md5('test')
}


@pytest.mark.asyncio
async def test_auth_endpoint_requires_credentials(test_client: AsyncClient) -> None:
    resp = await test_client.get("/api/grimmlink/v1/auth")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_endpoint_contract(test_client: AsyncClient) -> None:
    resp = await test_client.get("/api/grimmlink/v1/auth", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["username"] == "test_reader"
    assert "userId" in data
    assert "syncEnabled" in data
    assert "syncWithWebReader" in data


@pytest.mark.asyncio
async def test_capabilities_endpoint_contract(test_client: AsyncClient) -> None:
    resp = await test_client.get("/api/grimmlink/v1/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert data["apiVersion"] == "v1"
    assert data["progressSync"] is True
    assert data["shelves"] is True
    assert data["readingSessions"] is True
    assert data["metadataSync"] is True


@pytest.mark.asyncio
async def test_shelves_list_contract(test_client: AsyncClient) -> None:
    resp = await test_client.get("/api/grimmlink/v1/shelves", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    shelves = resp.json()
    assert isinstance(shelves, list)
    assert len(shelves) > 0
    first = shelves[0]
    assert "id" in first
    assert "name" in first
    assert "type" in first


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
async def test_regular_shelf_removal_contract(test_client: AsyncClient) -> None:
    resp = await test_client.post(
        "/api/grimmlink/v1/shelves/regular/1/books/42/remove",
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["bookId"] == 42
    assert data["shelfId"] == 1


@pytest.mark.asyncio
async def test_progress_sync_contract(test_client: AsyncClient) -> None:
    # GET progress
    get_resp = await test_client.get(
        "/api/grimmlink/v1/syncs/progress/sample_book_hash_123",
        headers=AUTH_HEADERS,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["book_hash"] == "sample_book_hash_123"

    # PUT progress
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
    assert put_resp.status_code == 200
    assert put_resp.json()["status"] == "progress updated"


@pytest.mark.asyncio
async def test_reading_sessions_batch_contract(test_client: AsyncClient) -> None:
    payload = {
        "sessions": [
            {
                "bookId": 10,
                "startTime": "2026-09-26T11:00:00Z",
                "endTime": "2026-09-26T11:45:00Z",
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
    assert resp.status_code == 200
    data = resp.json()
    assert data["totalReceived"] == 1
    assert data["acceptedCount"] == 1
