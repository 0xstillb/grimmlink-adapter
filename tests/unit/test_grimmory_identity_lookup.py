"""Tests for the optional read-only Grimmory identity lookup."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import SecretStr

from grimmlink_adapter.config import Settings, settings
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.identity_lookup import (
    GrimmoryIdentityCandidate,
    GrimmoryIdentityLookup,
    GrimmoryIdentityLookupError,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.book_service import (
    AmbiguousBookError,
    BookNotFoundError,
    BookResolutionUnavailableError,
    BookService,
    BookVerificationError,
)
from grimmlink_adapter.state.book_identity import BookIdentityStore

HASH = "d65441b897513421215d1fe4cfc3e9a5"
CURRENT_HASH = "95b818e712f70c1eb0b6f2aaaa650e1e"
SERVER = settings.GRIMMORY_BASE_URL
USER = "25"


@pytest_asyncio.fixture(autouse=True)
async def _clear_identity_cache() -> AsyncGenerator[None, None]:
    await BookIdentityStore.invalidate_all_for_user(SERVER, USER)
    yield
    await BookIdentityStore.invalidate_all_for_user(SERVER, USER)


class FakeLookup:
    enabled = True

    def __init__(
        self,
        current: list[GrimmoryIdentityCandidate] | None = None,
        initial: list[GrimmoryIdentityCandidate] | None = None,
    ) -> None:
        self.current = current or []
        self.initial = initial or []
        self.calls: list[str] = []

    async def lookup_current_hash(self, book_hash: str) -> list[GrimmoryIdentityCandidate]:
        self.calls.append(f"current:{book_hash}")
        return self.current

    async def lookup_initial_hash(self, book_hash: str) -> list[GrimmoryIdentityCandidate]:
        self.calls.append(f"initial:{book_hash}")
        return self.initial


def _candidate(current_hash: str = HASH) -> GrimmoryIdentityCandidate:
    return GrimmoryIdentityCandidate(
        book_id=25,
        book_file_id=25,
        current_hash=current_hash,
        initial_hash=HASH,
    )


def _official_client() -> AsyncMock:
    client = AsyncMock(spec=OfficialGrimmoryClient)
    client.get_book_by_id.return_value = {
        "id": 25,
        "title": "Verified book",
        "primaryFile": {"id": 25, "fileName": "verified.pdf", "bookType": "PDF"},
    }
    return client


@pytest.mark.asyncio
async def test_current_hash_uses_db_candidate_and_caches_after_bearer_check() -> None:
    lookup = FakeLookup(current=[_candidate()])
    client = _official_client()
    service = BookService(official_client=client, identity_lookup=lookup)  # type: ignore[arg-type]

    result = await service.resolve_book_by_hash(
        server=SERVER,
        user=USER,
        book_hash=HASH,
        creds=ClientCredentials(bearer_token="valid-bearer"),
    )

    assert result.book_id == 25
    assert result.book_file_id == 25
    assert lookup.calls == [f"current:{HASH}"]
    client.get_book_by_id.assert_awaited_once_with(25, "valid-bearer")
    cached = await BookIdentityStore.lookup_by_current_hash(SERVER, USER, HASH)
    assert len(cached) == 1
    assert cached[0].book_file_id == 25


@pytest.mark.asyncio
async def test_initial_hash_is_queried_only_after_current_hash_misses() -> None:
    lookup = FakeLookup(initial=[_candidate(CURRENT_HASH)])
    service = BookService(official_client=_official_client(), identity_lookup=lookup)  # type: ignore[arg-type]

    result = await service.resolve_book_by_hash(
        server=SERVER,
        user=USER,
        book_hash=HASH,
        creds=ClientCredentials(bearer_token="valid-bearer"),
    )

    assert result.book_id == 25
    assert lookup.calls == [f"current:{HASH}", f"initial:{HASH}"]
    cached = await BookIdentityStore.lookup_by_current_hash(SERVER, USER, CURRENT_HASH)
    assert len(cached) == 1


@pytest.mark.asyncio
async def test_db_current_wins_over_stale_cache_initial() -> None:
    await BookIdentityStore.upsert(
        server=SERVER, user=USER, book_id=99, book_file_id=99,
        current_hash=CURRENT_HASH, initial_hash=HASH,
    )
    lookup = FakeLookup(current=[_candidate()])
    client = _official_client()
    service = BookService(official_client=client, identity_lookup=lookup)  # type: ignore[arg-type]

    result = await service.resolve_book_by_hash(
        server=SERVER, user=USER, book_hash=HASH,
        creds=ClientCredentials(bearer_token="valid-bearer"),
    )

    assert result.book_id == 25
    client.get_book_by_id.assert_awaited_once_with(25, "valid-bearer")


@pytest.mark.asyncio
async def test_db_ambiguity_wins_over_stale_cache() -> None:
    await BookIdentityStore.upsert(
        server=SERVER, user=USER, book_id=99, book_file_id=99,
        current_hash=HASH, initial_hash=HASH,
    )
    lookup = FakeLookup(current=[_candidate(), _candidate()])
    service = BookService(official_client=_official_client(), identity_lookup=lookup)  # type: ignore[arg-type]

    with pytest.raises(AmbiguousBookError):
        await service.resolve_book_by_hash(
            server=SERVER, user=USER, book_hash=HASH,
            creds=ClientCredentials(bearer_token="valid-bearer"),
        )


@pytest.mark.asyncio
async def test_enabled_db_miss_does_not_use_stale_cache() -> None:
    await BookIdentityStore.upsert(
        server=SERVER, user=USER, book_id=99, book_file_id=99,
        current_hash=HASH, initial_hash=HASH,
    )
    client = _official_client()
    service = BookService(official_client=client, identity_lookup=FakeLookup())  # type: ignore[arg-type]
    with pytest.raises(BookNotFoundError):
        await service.resolve_book_by_hash(
            server=SERVER, user=USER, book_hash=HASH,
            creds=ClientCredentials(bearer_token="valid-bearer"),
        )
    client.get_book_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_db_error_does_not_use_stale_cache() -> None:
    await BookIdentityStore.upsert(
        server=SERVER, user=USER, book_id=99, book_file_id=99,
        current_hash=HASH, initial_hash=HASH,
    )
    lookup = FakeLookup()
    lookup.lookup_current_hash = AsyncMock(side_effect=GrimmoryIdentityLookupError("DB unavailable"))
    client = _official_client()
    service = BookService(official_client=client, identity_lookup=lookup)  # type: ignore[arg-type]
    with pytest.raises(BookVerificationError):
        await service.resolve_book_by_hash(
            server=SERVER, user=USER, book_hash=HASH,
            creds=ClientCredentials(bearer_token="valid-bearer"),
        )
    client.get_book_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_current_hash_fails_without_initial_fallback() -> None:
    lookup = FakeLookup(current=[_candidate(), _candidate()])
    client = _official_client()
    service = BookService(official_client=client, identity_lookup=lookup)  # type: ignore[arg-type]

    with pytest.raises(AmbiguousBookError) as exc_info:
        await service.resolve_book_by_hash(
            server=SERVER,
            user=USER,
            book_hash=HASH,
            creds=ClientCredentials(bearer_token="valid-bearer"),
        )

    assert exc_info.value.candidate_count == 2
    assert lookup.calls == [f"current:{HASH}"]
    client.get_book_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_md5_only_candidate_is_not_cached_without_book_access_check() -> None:
    lookup = FakeLookup(current=[_candidate()])
    client = _official_client()
    service = BookService(official_client=client, identity_lookup=lookup)  # type: ignore[arg-type]

    with pytest.raises(BookResolutionUnavailableError):
        await service.resolve_book_by_hash(
            server=SERVER,
            user=USER,
            book_hash=HASH,
            creds=ClientCredentials(username="user", md5_key="md5-key"),
        )

    client.get_book_by_id.assert_not_awaited()
    assert await BookIdentityStore.lookup_by_current_hash(SERVER, USER, HASH) == []


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.query = ""
        self.params: tuple[str, ...] = ()

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, query: str, params: tuple[str, ...]) -> None:
        self.query = query
        self.params = params

    async def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_query_is_parameterized_read_only_and_closes_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    from grimmlink_adapter.official import identity_lookup as lookup_module

    cursor = _FakeCursor([{
        "book_id": 25,
        "book_file_id": 25,
        "current_hash": HASH,
        "initial_hash": HASH,
    }])
    connection = _FakeConnection(cursor)
    received: dict[str, Any] = {}

    async def fake_connect(**kwargs: Any) -> _FakeConnection:
        received.update(kwargs)
        return connection

    monkeypatch.setattr(lookup_module.aiomysql, "connect", fake_connect)
    config = Settings(
        GRIMMORY_DB_ENABLED=True,
        GRIMMORY_DB_HOST="grimmory-mariadb",
        GRIMMORY_DB_USER="adapter_ro",
        GRIMMORY_DB_PASSWORD=SecretStr("not-logged"),
    )

    results = await GrimmoryIdentityLookup(config).lookup_current_hash(HASH)

    assert results == [_candidate()]
    assert cursor.params == (HASH,)
    assert "%s" in cursor.query
    assert "current_hash = %s" in cursor.query
    assert "bf.is_book = 1" in cursor.query
    assert "b.deleted IS NULL OR b.deleted = 0" in cursor.query
    assert cursor.query.lstrip().upper().startswith("SELECT")
    assert cursor.query.count(";") == 0
    assert received["user"] == "adapter_ro"
    assert received["autocommit"] is True
    assert connection.closed


@pytest.mark.asyncio
async def test_enabled_lookup_rejects_missing_connection_settings() -> None:
    config = Settings(GRIMMORY_DB_ENABLED=True)
    with pytest.raises(GrimmoryIdentityLookupError, match="settings are incomplete"):
        await GrimmoryIdentityLookup(config).lookup_current_hash(HASH)
