"""Comprehensive tests for book identity resolution (Session 03).

Test matrix (acceptance criteria from sessions/03_BOOK_IDENTITY.md & user review):
  1. Exact currentHash hit
  2. initialHash fallback
  3. Stale mapping (book deleted server-side) → invalidated & re-resolved
  4. Inaccessible candidate (permissions changed) → error
  5. Ambiguity on initialHash (multiple matches) → 409 Conflict
  6. Ambiguity on currentHash (duplicate current_hash rows in DB) → 409 Conflict
  7. Rename (hash unchanged) → still resolves
  8. Server/user isolation (same hash, different accounts → different books)
  9. Restart persistence (mapping survives process restart)
  10. Missing progress cannot prove book absence; return resolution unavailable
  11. File replaced upstream (primaryFile.id changed) → mapping invalidated & re-resolved
  12. book_file_id NOT NULL default 0 prevents duplicate PK rows
  13. MD5 auth is verified but book resolution fails closed without book ID evidence
  14. Bearer auth through live route (/books/by-hash/{hash})
  15. Token prefix collision: different accounts with identical token prefix isolated by verified user ID
  16. Golden contract response shape conformance (OfficialBookDTO)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
from grimmlink_adapter.config import settings
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import (
    OfficialBadResponseError,
    OfficialPermissionError,
    OfficialTransportError,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.book_service import (
    AmbiguousBookError,
    BookInaccessibleError,
    BookResolutionUnavailableError,
    BookService,
    BookVerificationError,
)
from grimmlink_adapter.state.book_identity import BookIdentityStore
from grimmlink_adapter.state.database import get_connection
from grimmlink_adapter.state.linked_accounts import (
    LinkedAccount,
    LinkedAccountStore,
    digest_md5_key,
)

SERVER = settings.GRIMMORY_BASE_URL
USER_A = "101"  # Upstream verified user ID for Alice
USER_B = "102"  # Upstream verified user ID for Bob
HASH_1 = "d41d8cd98f00b204e9800998ecf8427e"
HASH_2 = "e99a18c428cb38d5f260853678922e03"
HASH_INITIAL = "abc123abc123abc123abc123abc12300"

CREDS_BEARER = ClientCredentials(bearer_token="test-bearer-token-full-length-12345")
CREDS_MD5 = ClientCredentials(username="alice", md5_key="md5hashkey123")
CREDS_BOTH = ClientCredentials(username="alice", md5_key="md5hashkey123", bearer_token="bearer-token-abc")


# ---------------------------------------------------------------------------
# Per-test cleanup — ensure test isolation
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def _clean_book_identity() -> AsyncGenerator[None, None]:
    """Delete all book_identity rows before each test to prevent leakage."""
    async with get_connection() as conn:
        await conn.execute("DELETE FROM book_identity")
        await conn.execute("DELETE FROM linked_accounts")
        await conn.commit()
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(
    get_book_by_id_return: dict[str, Any] | None = None,
    get_book_by_id_side_effect: Any = None,
    koreader_progress_return: dict[str, Any] | None = None,
    koreader_progress_side_effect: Any = None,
) -> tuple[BookService, AsyncMock]:
    """Create a BookService with an AsyncMock OfficialGrimmoryClient."""
    mock_client = AsyncMock(spec=OfficialGrimmoryClient)

    if get_book_by_id_side_effect is not None:
        mock_client.get_book_by_id.side_effect = get_book_by_id_side_effect
    elif get_book_by_id_return is not None:
        mock_client.get_book_by_id.return_value = get_book_by_id_return
    else:
        async def default_book(book_id: int, _bearer: str) -> dict[str, Any]:
            return {
                "id": book_id,
                "title": "Test Book",
                "primaryFile": {"id": 100, "bookId": book_id, "fileName": "test.epub", "bookType": "EPUB"},
            }
        mock_client.get_book_by_id.side_effect = default_book

    if koreader_progress_side_effect is not None:
        mock_client.get_koreader_progress.side_effect = koreader_progress_side_effect
    elif koreader_progress_return is not None:
        mock_client.get_koreader_progress.return_value = koreader_progress_return
    else:
        mock_client.get_koreader_progress.return_value = {}

    service = BookService(official_client=mock_client)
    return service, mock_client


async def _seed_identity(
    server: str = SERVER,
    user: str = USER_A,
    current_hash: str = HASH_1,
    initial_hash: str = HASH_INITIAL,
    book_id: int = 42,
    book_file_id: int = 100,
    title: str = "Test Book",
    authors: list[str] | None = None,
    fmt: str = "EPUB",
    file_size: int | None = None,
    filename: str | None = None,
) -> None:
    """Insert a book identity mapping directly into the store."""
    await BookIdentityStore.upsert(
        server=server,
        user=user,
        current_hash=current_hash,
        initial_hash=initial_hash,
        book_id=book_id,
        book_file_id=book_file_id,
        title=title,
        authors=authors or ["Test Author"],
        fmt=fmt,
        file_size=file_size,
        filename=filename,
    )


# ===========================================================================
# Test 1: Exact currentHash hit
# ===========================================================================

@pytest.mark.asyncio
async def test_exact_current_hash_hit() -> None:
    """Exact currentHash match, book accessible → return immediately without remote search."""
    await _seed_identity()

    service, mock_client = _make_service()
    result = await service.resolve_book_by_hash(
        server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
    )

    assert result.book_id == 42
    assert result.book_file_id == 100
    assert result.current_hash == HASH_1
    assert result.server == SERVER
    assert result.user == USER_A
    mock_client.get_koreader_progress.assert_not_called()


# ===========================================================================
# Test 2: initialHash fallback
# ===========================================================================

@pytest.mark.asyncio
async def test_initial_hash_fallback() -> None:
    """currentHash misses but initialHash matches a single row."""
    await _seed_identity(current_hash=HASH_2, initial_hash=HASH_1)

    service, _ = _make_service()
    result = await service.resolve_book_by_hash(
        server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
    )

    assert result.book_id == 42
    assert result.current_hash == HASH_2
    assert result.initial_hash == HASH_1


# ===========================================================================
# Test 3: Stale mapping (book deleted server-side)
# ===========================================================================

@pytest.mark.asyncio
async def test_stale_mapping_invalidated_and_reresolved() -> None:
    """Cached book deleted (404) → invalidate → re-resolve via KOReader progress."""
    await _seed_identity(book_id=42, current_hash=HASH_1)

    call_count = 0

    async def verify_side_effect(book_id: int, bearer_token: str) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if book_id == 42:
            raise OfficialBadResponseError(
                "Not found", status_code=404, method="GET",
                url=f"/api/v1/books/{book_id}",
            )
        return {
            "id": 99,
            "title": "Replacement Book",
            "primaryFile": {"id": 200, "bookId": 99, "fileName": "r.epub", "bookType": "EPUB", "fileSizeKb": 2048},
        }

    service, mock_client = _make_service(get_book_by_id_side_effect=verify_side_effect)
    mock_client.get_koreader_progress.return_value = {
        "bookId": 99, "currentHash": HASH_1, "initialHash": HASH_1,
    }

    result = await service.resolve_book_by_hash(
        server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BOTH,
    )

    assert result.book_id == 99
    assert result.book_file_id == 200

    # Old mapping gone, new one present
    old = await BookIdentityStore.lookup_by_book_id(SERVER, USER_A, 42)
    assert len(old) == 0
    new_rows = await BookIdentityStore.lookup_by_current_hash(SERVER, USER_A, HASH_1)
    assert len(new_rows) == 1
    assert new_rows[0].book_id == 99


# ===========================================================================
# Test 4: Inaccessible candidate (permissions revoked)
# ===========================================================================

@pytest.mark.asyncio
async def test_inaccessible_candidate_raises_error() -> None:
    """Cached book returns 403 → BookInaccessibleError with correct book_id."""
    await _seed_identity(book_id=42)

    service, _ = _make_service(
        get_book_by_id_side_effect=OfficialPermissionError(
            "Access forbidden", status_code=403,
            method="GET", url="/api/v1/books/42",
        ),
    )

    with pytest.raises(BookInaccessibleError) as exc_info:
        await service.resolve_book_by_hash(
            server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
        )
    assert exc_info.value.book_id == 42


# ===========================================================================
# Test 5: Ambiguity on initialHash → error (never silently choose)
# ===========================================================================

@pytest.mark.asyncio
async def test_ambiguous_initial_hash_raises_conflict() -> None:
    """Multiple cached books share the same initialHash → AmbiguousBookError."""
    await _seed_identity(book_id=42, book_file_id=100, current_hash=HASH_1, initial_hash=HASH_INITIAL)
    await _seed_identity(book_id=43, book_file_id=101, current_hash=HASH_2, initial_hash=HASH_INITIAL)

    service, _ = _make_service()

    with pytest.raises(AmbiguousBookError) as exc_info:
        await service.resolve_book_by_hash(
            server=SERVER, user=USER_A, book_hash=HASH_INITIAL, creds=CREDS_BEARER,
        )
    assert exc_info.value.candidate_count == 2


# ===========================================================================
# Test 6: Ambiguity on currentHash → error (current_hash duplicate guard)
# ===========================================================================

@pytest.mark.asyncio
async def test_ambiguous_current_hash_raises_conflict() -> None:
    """Multiple cached books share the same currentHash → AmbiguousBookError (never silent pick)."""
    # Insert two different book records with the same current_hash
    await _seed_identity(book_id=42, book_file_id=100, current_hash=HASH_1)
    await _seed_identity(book_id=43, book_file_id=101, current_hash=HASH_1)

    service, _ = _make_service()

    with pytest.raises(AmbiguousBookError) as exc_info:
        await service.resolve_book_by_hash(
            server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
        )
    assert exc_info.value.candidate_count == 2


# ===========================================================================
# Test 7: Rename (hash unchanged) → still resolves
# ===========================================================================

@pytest.mark.asyncio
async def test_rename_still_resolves() -> None:
    """Book file renamed but hash unchanged → cache hit."""
    await _seed_identity(current_hash=HASH_1, initial_hash=HASH_INITIAL, filename="old_name.epub")

    service, _ = _make_service()
    result = await service.resolve_book_by_hash(
        server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
    )

    assert result.book_id == 42
    assert result.filename == "old_name.epub"


# ===========================================================================
# Test 8: Server/user isolation
# ===========================================================================

@pytest.mark.asyncio
async def test_server_user_isolation() -> None:
    """Same hash, different verified users → completely separate cache rows."""
    await _seed_identity(server=SERVER, user=USER_A, book_id=42, current_hash=HASH_1)
    await _seed_identity(server=SERVER, user=USER_B, book_id=99, current_hash=HASH_1)

    service, _ = _make_service()

    result_a = await service.resolve_book_by_hash(
        server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
    )
    result_b = await service.resolve_book_by_hash(
        server=SERVER, user=USER_B, book_hash=HASH_1, creds=CREDS_BEARER,
    )

    assert result_a.book_id == 42
    assert result_b.book_id == 99
    assert result_a.user == USER_A
    assert result_b.user == USER_B


@pytest.mark.asyncio
async def test_different_server_isolation() -> None:
    """Same user, different servers → separate mappings."""
    server_1 = "http://server1.test"
    server_2 = "http://server2.test"

    await _seed_identity(server=server_1, user=USER_A, book_id=42, current_hash=HASH_1)
    await _seed_identity(server=server_2, user=USER_A, book_id=77, current_hash=HASH_1)

    service, _ = _make_service()

    r1 = await service.resolve_book_by_hash(
        server=server_1, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
    )
    r2 = await service.resolve_book_by_hash(
        server=server_2, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
    )

    assert r1.book_id == 42
    assert r2.book_id == 77


# ===========================================================================
# Test 9: Restart persistence
# ===========================================================================

@pytest.mark.asyncio
async def test_restart_persistence() -> None:
    """Mappings survive service restart (persisted in SQLite)."""
    await _seed_identity()

    service1, _ = _make_service()
    r1 = await service1.resolve_book_by_hash(
        server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
    )
    assert r1.book_id == 42

    service2, mock2 = _make_service()
    r2 = await service2.resolve_book_by_hash(
        server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
    )
    assert r2.book_id == 42
    mock2.get_koreader_progress.assert_not_called()


# ===========================================================================
# Test 10: Hash mismatch — book exists in remote but hash doesn't match
# ===========================================================================

@pytest.mark.asyncio
async def test_empty_progress_cannot_prove_book_absent() -> None:
    """An empty progress response cannot establish that the book does not exist."""
    service, _ = _make_service(koreader_progress_return={})

    with pytest.raises(BookResolutionUnavailableError):
        await service.resolve_book_by_hash(
            server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BOTH,
        )


@pytest.mark.asyncio
async def test_remote_only_trusts_exact_hash_evidence() -> None:
    """Only trust exact hash verified upstream via KOReader progress endpoint."""
    service, mock_client = _make_service()
    mock_client.get_koreader_progress.return_value = {
        "bookId": 77, "currentHash": HASH_1, "initialHash": HASH_1,
    }
    mock_client.get_book_by_id.return_value = {
        "id": 77,
        "title": "Exact Matched Book",
        "primaryFile": {"id": 500, "bookId": 77, "fileName": "exact.epub", "bookType": "EPUB", "fileSizeKb": 100},
    }
    mock_client.get_book_by_id.side_effect = None

    result = await service.resolve_book_by_hash(
        server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BOTH,
    )

    assert result.book_id == 77
    assert result.title == "Exact Matched Book"


# ===========================================================================
# Test 11: File replaced upstream (primaryFile.id changed)
# ===========================================================================

@pytest.mark.asyncio
async def test_file_replaced_invalidates_mapping() -> None:
    """Cached mapping has file_id=100, but upstream now shows file_id=200 → invalidates & re-resolves."""
    await _seed_identity(book_id=42, book_file_id=100)

    # Initial check: file_id is 200 (changed from 100), triggering invalidation
    service, mock_client = _make_service(
        get_book_by_id_return={
            "id": 42,
            "title": "Test Book",
            "primaryFile": {"id": 200, "bookId": 42, "fileName": "new.epub", "bookType": "EPUB"},
        },
    )
    mock_client.get_koreader_progress.return_value = {
        "bookId": 42, "currentHash": HASH_1, "initialHash": HASH_1,
    }

    result = await service.resolve_book_by_hash(
        server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BOTH,
    )

    assert result.book_id == 42
    assert result.book_file_id == 200


# ===========================================================================
# Test 12: book_file_id NOT NULL default 0 prevents duplicate PK rows
# ===========================================================================

@pytest.mark.asyncio
async def test_book_file_id_not_null_prevents_duplicate_pk() -> None:
    """Inserting book with book_file_id=0 updates existing row instead of duplicating."""
    await BookIdentityStore.upsert(
        server=SERVER, user=USER_A,
        current_hash=HASH_1, initial_hash=HASH_INITIAL,
        book_id=42, book_file_id=0,
        title="First Insert",
    )
    await BookIdentityStore.upsert(
        server=SERVER, user=USER_A,
        current_hash=HASH_1, initial_hash=HASH_INITIAL,
        book_id=42, book_file_id=0,
        title="Second Insert",
    )

    rows = await BookIdentityStore.lookup_by_book_id(SERVER, USER_A, 42)
    assert len(rows) == 1
    assert rows[0].title == "Second Insert"


# ===========================================================================
# Test 13: MD5 auth through live route
# ===========================================================================

@pytest.mark.asyncio
async def test_native_grimmlink_proxy_preserves_official_book_response(test_client: AsyncClient) -> None:
    native_payload = {
        "id": 25,
        "title": "Native Book",
        "primaryFile": {"id": 25, "bookId": 25, "fileName": "book.pdf", "bookType": "PDF"},
    }
    upstream_response = httpx.Response(200, json=native_payload)
    with (
        patch.object(settings, "GRIMMLINK_NATIVE_PROXY", True),
        patch.object(
            OfficialGrimmoryClient,
            "request",
            new_callable=AsyncMock,
            return_value=upstream_response,
        ) as native_request,
    ):
        response = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/native-hash",
            headers={"x-auth-user": "reader", "x-auth-key": "md5-key"},
        )

    assert response.status_code == 200
    assert response.json() == native_payload
    native_request.assert_awaited_once()
    assert native_request.await_args.args[:2] == (
        "GET", "/api/grimmlink/v1/books/by-hash/native-hash",
    )
    assert native_request.await_args.kwargs["auth_mode"].value == "KOREADER"


@pytest.mark.asyncio
async def test_md5_auth_through_live_route(test_client: AsyncClient) -> None:
    """MD5 authentication succeeds, but stock Official cannot verify book access."""
    await _seed_identity(user="10", current_hash="md5hash1")

    with patch.object(
        OfficialGrimmoryClient, "get_koreader_auth",
        new_callable=AsyncMock,
        return_value={"userId": 10, "username": "koreader_user"},
    ):
        resp = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/md5hash1",
            headers={"x-auth-user": "koreader_user", "x-auth-key": "some_md5_key"},
        )
    assert resp.status_code == 501
    rows = await BookIdentityStore.lookup_by_current_hash(SERVER, "10", "md5hash1")
    assert len(rows) == 1
    assert rows[0].book_id == 42


@pytest.mark.asyncio
async def test_linked_md5_client_gets_verified_book(test_client: AsyncClient) -> None:
    """A locally linked JWT lets the existing MD5-only client retrieve a book."""
    await _seed_identity(user="10", current_hash="linked-md5", book_file_id=101)
    await LinkedAccountStore.put(LinkedAccount(
        server=SERVER, user_id="10", username="koreader_user",
        md5_key_digest=digest_md5_key("some_md5_key"),
        access_token="linked-jwt", refresh_token=None,
    ))
    with (
        patch.object(OfficialGrimmoryClient, "get_koreader_auth", new_callable=AsyncMock,
                     return_value={"userId": 10}),
        patch.object(OfficialGrimmoryClient, "get_current_user", new_callable=AsyncMock,
                     return_value={"id": 10, "username": "koreader_user"}),
        patch.object(OfficialGrimmoryClient, "get_book_by_id", new_callable=AsyncMock,
                     return_value={
                         "id": 42, "title": "Test Book",
                         "primaryFile": {"id": 100, "fileName": "test.epub"},
                         "alternativeFormats": [{"id": 101, "fileName": "test.pdf"}],
                     }) as get_book,
    ):
        resp = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/linked-md5",
            headers={"x-auth-user": "koreader_user", "x-auth-key": "some_md5_key"},
        )
    assert resp.status_code == 200
    assert resp.json()["id"] == 42
    assert resp.json()["alternativeFormats"][0]["id"] == 101
    assert get_book.await_count == 2


@pytest.mark.asyncio
async def test_minimal_identity_response_returns_only_consumed_book_id(test_client: AsyncClient) -> None:
    await _seed_identity(user="10", current_hash="minimal-md5", book_file_id=101)
    await LinkedAccountStore.put(LinkedAccount(
        server=SERVER, user_id="10", username="koreader_user",
        md5_key_digest=digest_md5_key("some_md5_key"),
        access_token="linked-jwt", refresh_token=None,
    ))
    with (
        patch.object(settings, "GRIMMLINK_MINIMAL_IDENTITY_RESPONSE", True),
        patch.object(OfficialGrimmoryClient, "get_koreader_auth", new_callable=AsyncMock,
                     return_value={"userId": 10}),
        patch.object(OfficialGrimmoryClient, "get_current_user", new_callable=AsyncMock,
                     return_value={"id": 10, "username": "koreader_user"}),
        patch.object(OfficialGrimmoryClient, "get_book_by_id", new_callable=AsyncMock,
                     return_value={
                         "id": 42, "title": "Test Book",
                         "primaryFile": {"id": 100, "fileName": "test.epub"},
                         "alternativeFormats": [{"id": 101, "fileName": "test.pdf"}],
                     }) as get_book,
    ):
        resp = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/minimal-md5",
            headers={"x-auth-user": "koreader_user", "x-auth-key": "some_md5_key"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"id": 42}
    assert get_book.await_count == 1


@pytest.mark.asyncio
async def test_wrong_linked_md5_key_never_uses_saved_bearer(test_client: AsyncClient) -> None:
    await _seed_identity(user="10", current_hash="linked-wrong-key")
    await LinkedAccountStore.put(LinkedAccount(
        server=SERVER, user_id="10", username="koreader_user",
        md5_key_digest=digest_md5_key("correct-key"),
        access_token="linked-jwt", refresh_token=None,
    ))
    with (
        patch.object(OfficialGrimmoryClient, "get_koreader_auth", new_callable=AsyncMock,
                     return_value={"userId": 10}),
        patch.object(OfficialGrimmoryClient, "get_book_by_id", new_callable=AsyncMock) as get_book,
    ):
        resp = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/linked-wrong-key",
            headers={"x-auth-user": "koreader_user", "x-auth-key": "wrong-key"},
        )
    assert resp.status_code == 501
    get_book.assert_not_awaited()


# ===========================================================================
# Test 14: Bearer auth through live route
# ===========================================================================

@pytest.mark.asyncio
async def test_bearer_auth_through_live_route(test_client: AsyncClient) -> None:
    """Live route GET /api/grimmlink/v1/books/by-hash/{hash} accepts Bearer token."""
    await _seed_identity(user="20", current_hash="bearerhash1")

    with (
        patch.object(
            OfficialGrimmoryClient, "get_current_user",
            new_callable=AsyncMock,
            return_value={"id": 20, "username": "bearer_user"},
        ),
        patch.object(
            OfficialGrimmoryClient, "get_book_by_id",
            new_callable=AsyncMock,
            return_value={
                "id": 42,
                "title": "Test Book",
                "primaryFile": {"id": 100, "bookId": 42, "fileName": "test.epub", "bookType": "EPUB"},
            },
        ),
    ):
        resp = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/bearerhash1",
            headers={"Authorization": "Bearer valid_jwt_token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 42


# ===========================================================================
# Test 15: Token prefix collision: different accounts isolated by verified user ID
# ===========================================================================

@pytest.mark.asyncio
async def test_token_prefix_collision_different_accounts_isolated(test_client: AsyncClient) -> None:
    """Tokens sharing first 16 chars map to distinct user IDs; their caches never mix."""
    # Pre-seed User 10 with book 42, User 20 with book 99 for the same hash
    await _seed_identity(user="10", book_id=42, current_hash="shared_content_hash")
    await _seed_identity(user="20", book_id=99, current_hash="shared_content_hash")

    token_alice = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9_ALICE"
    token_bob = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9_BOB"
    assert token_alice[:16] == token_bob[:16]  # Identical 16-char prefix!

    async def mock_get_user(token: str) -> dict[str, Any]:
        if "ALICE" in token:
            return {"id": 10, "username": "alice"}
        return {"id": 20, "username": "bob"}

    async def mock_get_book(book_id: int, token: str) -> dict[str, Any]:
        return {
            "id": book_id,
            "title": f"Book {book_id}",
            "primaryFile": {"id": 100, "bookId": book_id, "fileName": "b.epub", "bookType": "EPUB"},
        }

    with (
        patch.object(OfficialGrimmoryClient, "get_current_user", side_effect=mock_get_user),
        patch.object(OfficialGrimmoryClient, "get_book_by_id", side_effect=mock_get_book),
    ):
        resp_a = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/shared_content_hash",
            headers={"Authorization": f"Bearer {token_alice}"},
        )
        resp_b = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/shared_content_hash",
            headers={"Authorization": f"Bearer {token_bob}"},
        )

        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        assert resp_a.json()["id"] == 42  # Alice got book 42
        assert resp_b.json()["id"] == 99  # Bob got book 99 (no collision!)


# ===========================================================================
# Test 16: Golden contract response shape conformance (OfficialBookDTO)
# ===========================================================================

@pytest.mark.asyncio
async def test_golden_contract_response_shape(test_client: AsyncClient) -> None:
    """Route returns full OfficialBookDTO matching golden fixture structure."""
    await _seed_identity(user="50", book_id=42, book_file_id=101, current_hash="golden_hash")

    golden_data: dict[str, Any] = {
        "id": 42,
        "libraryId": 1,
        "libraryName": "Main Library",
        "title": "The Count of Monte Cristo",
        "readStatus": "READING",
        "personalRating": 8,
        "addedOn": "2026-01-15T10:00:00Z",
        "lastReadTime": "2026-09-25T14:30:00Z",
        "primaryFile": {
            "id": 101,
            "bookId": 42,
            "fileName": "The Count of Monte Cristo.epub",
            "filePath": "/books/Alexandre Dumas/The Count of Monte Cristo.epub",
            "bookType": "EPUB",
            "fileSizeKb": 1250,
        },
        "metadata": {
            "title": "The Count of Monte Cristo",
            "authors": [{"id": 5, "name": "Alexandre Dumas"}],
            "seriesName": "Classics",
            "seriesNumber": 1.0,
            "description": "Historical adventure novel",
        },
        "shelves": [{"id": 7, "name": "Favorites", "bookCount": 12, "publicShelf": False}],
    }

    with (
        patch.object(
            OfficialGrimmoryClient, "get_current_user",
            new_callable=AsyncMock,
            return_value={"id": 50, "username": "reader"},
        ),
        patch.object(
            OfficialGrimmoryClient, "get_book_by_id",
            new_callable=AsyncMock,
            return_value=golden_data,
        ),
    ):
        resp = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/golden_hash",
            headers={"Authorization": "Bearer jwt_golden"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 42
        assert data["libraryId"] == 1
        assert data["libraryName"] == "Main Library"
        assert data["title"] == "The Count of Monte Cristo"
        assert data["primaryFile"]["id"] == 101
        assert data["primaryFile"]["fileName"] == "The Count of Monte Cristo.epub"
        assert data["metadata"]["seriesName"] == "Classics"
        assert len(data["shelves"]) == 1


# ===========================================================================
# Transport error edge case
# ===========================================================================

@pytest.mark.asyncio
async def test_network_error_during_verify_raises() -> None:
    """Transport error during verification → BookVerificationError."""
    await _seed_identity()

    service, _ = _make_service(
        get_book_by_id_side_effect=OfficialTransportError(
            "Connection refused", method="GET", url="/api/v1/books/42",
        ),
    )

    with pytest.raises(BookVerificationError):
        await service.resolve_book_by_hash(
            server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
        )


# ===========================================================================
# BookIdentityStore unit tests
# ===========================================================================

@pytest.mark.asyncio
async def test_store_upsert_and_lookup() -> None:
    """Basic round-trip: upsert then lookup by currentHash."""
    await BookIdentityStore.upsert(
        server=SERVER, user=USER_A,
        current_hash=HASH_1, initial_hash=HASH_INITIAL,
        book_id=42, book_file_id=100,
        title="Test", authors=["Author A"], fmt="EPUB",
    )

    rows = await BookIdentityStore.lookup_by_current_hash(SERVER, USER_A, HASH_1)
    assert len(rows) == 1
    row = rows[0]
    assert row.book_id == 42
    assert row.title == "Test"
    assert row.authors == ["Author A"]


@pytest.mark.asyncio
async def test_store_upsert_overwrites() -> None:
    """Upserting same PK updates fields."""
    await BookIdentityStore.upsert(
        server=SERVER, user=USER_A,
        current_hash=HASH_1, initial_hash=HASH_INITIAL,
        book_id=42, book_file_id=100, title="Old Title",
    )
    await BookIdentityStore.upsert(
        server=SERVER, user=USER_A,
        current_hash=HASH_2, initial_hash=HASH_INITIAL,
        book_id=42, book_file_id=100, title="New Title",
    )

    old_rows = await BookIdentityStore.lookup_by_current_hash(SERVER, USER_A, HASH_1)
    assert len(old_rows) == 0

    new_rows = await BookIdentityStore.lookup_by_current_hash(SERVER, USER_A, HASH_2)
    assert len(new_rows) == 1
    assert new_rows[0].title == "New Title"


@pytest.mark.asyncio
async def test_store_invalidate() -> None:
    """Invalidation removes the mapping."""
    await BookIdentityStore.upsert(
        server=SERVER, user=USER_A,
        current_hash=HASH_1, initial_hash=HASH_INITIAL,
        book_id=42, book_file_id=100,
    )
    await BookIdentityStore.invalidate(SERVER, USER_A, 42, 100)

    rows = await BookIdentityStore.lookup_by_current_hash(SERVER, USER_A, HASH_1)
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_store_invalidate_all_for_user() -> None:
    """Invalidating all mappings for a user removes everything."""
    await _seed_identity(book_id=1, book_file_id=10, current_hash="aaa111")
    await _seed_identity(book_id=2, book_file_id=20, current_hash="bbb222")

    count = await BookIdentityStore.invalidate_all_for_user(SERVER, USER_A)
    assert count == 2

    rows = await BookIdentityStore.lookup_by_current_hash(SERVER, USER_A, "aaa111")
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_store_lookup_miss() -> None:
    """Lookup on empty table returns empty list."""
    rows = await BookIdentityStore.lookup_by_current_hash(SERVER, USER_A, "nonexistent")
    assert rows == []

    initial_rows = await BookIdentityStore.lookup_by_initial_hash(SERVER, USER_A, "nonexistent")
    assert initial_rows == []


# ===========================================================================
# Session 03 review: stock Official response and fail-closed behavior
# ===========================================================================

@pytest.mark.asyncio
async def test_stock_progress_payload_does_not_invent_book_id() -> None:
    """Official progress schema has position fields and no bookId."""
    progress = {
        "timestamp": 1,
        "document": "reader.epub",
        "percentage": 0.25,
        "progress": "/body/1",
        "device": "KOReader",
        "device_id": "device-1",
    }
    service, mock_client = _make_service(koreader_progress_return=progress)
    with pytest.raises(BookResolutionUnavailableError):
        await service.resolve_book_by_hash(
            server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_MD5,
        )
    mock_client.get_book_by_id.assert_not_called()
    assert await BookIdentityStore.lookup_by_current_hash(SERVER, USER_A, HASH_1) == []


@pytest.mark.asyncio
async def test_bearer_only_cache_miss_is_unavailable_without_exact_mapping() -> None:
    service, mock_client = _make_service()
    with pytest.raises(BookResolutionUnavailableError):
        await service.resolve_book_by_hash(
            server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_BEARER,
        )
    mock_client.get_koreader_progress.assert_not_called()


@pytest.mark.asyncio
async def test_progress_transport_failure_is_not_reported_as_hash_miss() -> None:
    service, _ = _make_service(
        koreader_progress_side_effect=OfficialTransportError("connection failed"),
    )
    with pytest.raises(BookVerificationError):
        await service.resolve_book_by_hash(
            server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_MD5,
        )


@pytest.mark.asyncio
async def test_last_book_fetch_forbidden_returns_403(test_client: AsyncClient) -> None:
    await _seed_identity(user="50", book_id=42, book_file_id=100, current_hash="race_hash")
    book = {
        "id": 42,
        "title": "Private Book",
        "primaryFile": {"id": 100, "bookId": 42, "fileName": "private.epub"},
    }
    with (
        patch.object(
            OfficialGrimmoryClient, "get_current_user",
            new_callable=AsyncMock, return_value={"id": 50, "username": "reader"},
        ),
        patch.object(
            OfficialGrimmoryClient, "get_book_by_id",
            new_callable=AsyncMock,
            side_effect=[
                book,
                OfficialPermissionError("forbidden", status_code=403),
            ],
        ),
    ):
        resp = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/race_hash",
            headers={"Authorization": "Bearer reader-token"},
        )
    assert resp.status_code == 403
    assert "Private Book" not in resp.text


@pytest.mark.asyncio
async def test_mismatched_md5_and_bearer_users_are_rejected(test_client: AsyncClient) -> None:
    with (
        patch.object(
            OfficialGrimmoryClient, "get_koreader_auth",
            new_callable=AsyncMock, return_value={"userId": 10},
        ),
        patch.object(
            OfficialGrimmoryClient, "get_current_user",
            new_callable=AsyncMock, return_value={"id": 20, "username": "other"},
        ),
    ):
        resp = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/any_hash",
            headers={
                "x-auth-user": "reader",
                "x-auth-key": "md5-key",
                "Authorization": "Bearer other-token",
            },
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_md5_extension_candidate_without_bearer_is_not_cached() -> None:
    """Even a non-stock bookId response needs book-level access verification."""
    service, mock_client = _make_service(
        koreader_progress_return={"bookId": 42, "bookHash": HASH_1},
    )
    with pytest.raises(BookResolutionUnavailableError):
        await service.resolve_book_by_hash(
            server=SERVER, user=USER_A, book_hash=HASH_1, creds=CREDS_MD5,
        )
    mock_client.get_book_by_id.assert_not_called()
    assert await BookIdentityStore.lookup_by_current_hash(SERVER, USER_A, HASH_1) == []


@pytest.mark.asyncio
async def test_last_book_fetch_file_change_returns_conflict(test_client: AsyncClient) -> None:
    await _seed_identity(user="50", book_id=42, book_file_id=100, current_hash="swap_hash")
    old_book = {
        "id": 42, "title": "Book",
        "primaryFile": {"id": 100, "bookId": 42, "fileName": "old.epub"},
    }
    new_book = {
        "id": 42, "title": "Book",
        "primaryFile": {"id": 200, "bookId": 42, "fileName": "new.epub"},
    }
    with (
        patch.object(
            OfficialGrimmoryClient, "get_current_user",
            new_callable=AsyncMock, return_value={"id": 50, "username": "reader"},
        ),
        patch.object(
            OfficialGrimmoryClient, "get_book_by_id",
            new_callable=AsyncMock, side_effect=[old_book, new_book],
        ),
    ):
        resp = await test_client.get(
            "/api/grimmlink/v1/books/by-hash/swap_hash",
            headers={"Authorization": "Bearer reader-token"},
        )
    assert resp.status_code == 409
    assert "new.epub" not in resp.text
