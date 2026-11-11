"""Concurrency, isolation, deadlines and lifecycle regression tests."""

import asyncio
import base64
import json
import logging
import time
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.official import OfficialLoginResponse
from grimmlink_adapter.official.auth import AuthMode
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import OfficialTimeoutError
from grimmlink_adapter.official.runtime import current_runtime, start_runtime, stop_runtime
from grimmlink_adapter.performance import RequestTimingMiddleware, deadline, measure
from grimmlink_adapter.security.account_locks import _locks, credential_refresh_lock
from grimmlink_adapter.security.auth_extractor import _official_session_credentials
from grimmlink_adapter.state.adapter_sessions import AdapterSessionStore


@pytest_asyncio.fixture
async def runtime():
    await start_runtime()
    try:
        yield current_runtime()
    finally:
        await stop_runtime()


def jwt(expiry):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
    return f"e30.{payload}.signature"


async def test_distinct_accounts_do_not_wait_and_cancelled_waiter_releases_reference():
    async with credential_refresh_lock("http://server/", 1):
        async with credential_refresh_lock("http://server", 2):
            pass
        entered = asyncio.Event()

        async def waiter():
            async with credential_refresh_lock("http://server", "1"):
                entered.set()

        pending = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        assert not entered.is_set()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert not _locks[asyncio.get_running_loop()]


async def test_refresh_wait_obeys_request_budget():
    async with credential_refresh_lock("server", 1):
        token = deadline.set(time.monotonic() + 0.02)
        try:
            with pytest.raises(OfficialTimeoutError):
                async with credential_refresh_lock("server", 1):
                    pytest.fail("Must not acquire a held lock")
        finally:
            deadline.reset(token)
    assert not _locks[asyncio.get_running_loop()]


async def test_valid_session_checks_run_concurrently(monkeypatch):
    token, session = await AdapterSessionStore.create_official("reader", 42, jwt(time.time() + 600), None)
    both = asyncio.Event()
    calls = 0

    async def profile(self, access):
        nonlocal calls
        calls += 1
        if calls == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 2)
        return {"id": 42}

    monkeypatch.setattr(OfficialGrimmoryClient, "get_current_user", profile)
    try:
        results = await asyncio.gather(*(_official_session_credentials(token, session) for _ in range(2)))
        assert all(result.bearer_token.user_id == 42 for result in results)
    finally:
        await AdapterSessionStore.revoke(token)


async def test_expired_session_rotates_once_for_concurrent_requests(monkeypatch):
    token, session = await AdapterSessionStore.create_official("reader", 42, jwt(1), "refresh")
    fresh = jwt(time.time() + 600)
    refresh = AsyncMock(return_value=OfficialLoginResponse(token=fresh, refreshToken="new-refresh"))
    monkeypatch.setattr(OfficialGrimmoryClient, "refresh_token", refresh)
    monkeypatch.setattr(OfficialGrimmoryClient, "get_current_user", AsyncMock(return_value={"id": 42}))
    try:
        results = await asyncio.gather(*(_official_session_credentials(token, session) for _ in range(4)))
        assert all(result.bearer_token == fresh for result in results)
        refresh.assert_awaited_once()
        assert (await AdapterSessionStore.get(token)).official_refresh_token == "new-refresh"
    finally:
        await AdapterSessionStore.revoke(token)


async def test_logout_during_profile_check_is_rejected(monkeypatch):
    token, session = await AdapterSessionStore.create_official("reader", 42, "access", None)

    async def profile(self, access):
        await AdapterSessionStore.revoke(token)
        return {"id": 42}

    monkeypatch.setattr(OfficialGrimmoryClient, "get_current_user", profile)
    with pytest.raises(HTTPException) as exc:
        await _official_session_credentials(token, session)
    assert exc.value.status_code == 401


async def test_shared_transport_keeps_auth_cookies_and_ownership_separate(runtime):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": 1}, headers={"set-cookie": "user=alice"})

    transport = httpx.MockTransport(handler)
    transport.aclose = AsyncMock()
    runtime.transports[("http://mock", True)] = transport
    async with OfficialGrimmoryClient(base_url="http://mock") as first:
        await first.get_current_user("alice")
    transport.aclose.assert_not_awaited()
    async with OfficialGrimmoryClient(base_url="http://mock") as second:
        await second.get_current_user("bob")
    assert requests[0].headers["authorization"] == "Bearer alice"
    assert requests[1].headers["authorization"] == "Bearer bob"
    assert "cookie" not in requests[1].headers
    await stop_runtime()
    transport.aclose.assert_awaited_once()


async def test_bookmark_reads_coalesce_only_inflight_and_are_copied(runtime, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch(self, book_id, bearer):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return [{"id": 1, "data": {"page": 10}}]

    monkeypatch.setattr(OfficialGrimmoryClient, "_fetch_bookmarks_for_book", fetch)
    first, second = OfficialGrimmoryClient(), OfficialGrimmoryClient()
    a = asyncio.create_task(first.get_bookmarks_for_book(1, "alice"))
    await entered.wait()
    b = asyncio.create_task(second.get_bookmarks_for_book(1, "alice"))
    await asyncio.sleep(0)
    release.set()
    left, right = await asyncio.gather(a, b)
    assert calls == 1
    left[0]["data"]["page"] = 99
    assert right[0]["data"]["page"] == 10
    await second.get_bookmarks_for_book(1, "alice")
    assert calls == 2  # No TTL cache: the next read is fresh.
    assert not runtime.bookmarks


async def test_bookmark_reads_never_share_across_users_or_books(runtime, monkeypatch):
    calls = []
    release = asyncio.Event()

    async def fetch(self, book_id, bearer):
        calls.append((book_id, bearer))
        if len(calls) == 3:
            release.set()
        await asyncio.wait_for(release.wait(), 2)
        return [{"owner": bearer, "book": book_id}]

    monkeypatch.setattr(OfficialGrimmoryClient, "_fetch_bookmarks_for_book", fetch)
    client = OfficialGrimmoryClient()
    results = await asyncio.gather(*(
        client.get_bookmarks_for_book(book, user)
        for book, user in [(1, "alice"), (1, "bob"), (2, "alice")]
    ))
    assert len(calls) == 3
    assert results[1] == [{"owner": "bob", "book": 1}]


async def test_bookmark_write_invalidates_inflight_reads(runtime):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def handler(request):
        nonlocal calls
        if request.method == "POST":
            return httpx.Response(200, json={"id": 2})
        calls += 1
        snapshot = calls
        started.set()
        if snapshot == 1:
            await release.wait()
        return httpx.Response(200, json=[{"id": snapshot}])

    runtime.transports[("http://mock", True)] = httpx.MockTransport(handler)
    async with OfficialGrimmoryClient(base_url="http://mock") as client:
        old = asyncio.create_task(client.get_bookmarks_for_book(1, "alice"))
        await started.wait()
        await client._send_request("POST", "/api/v1/bookmarks", auth_mode=AuthMode.NONE)
        new = await asyncio.wait_for(client.get_bookmarks_for_book(1, "alice"), 2)
        assert new == [{"id": 2}]
        release.set()
        assert await old == [{"id": 1}]
    assert not runtime.bookmarks


async def test_cancelled_bookmark_follower_does_not_cancel_leader(runtime):
    started, release = asyncio.Event(), asyncio.Event()

    async def fetch():
        started.set()
        await release.wait()
        return [{"id": 1}]

    key = ("server", "alice", 1)
    leader = asyncio.create_task(runtime.read_bookmarks(key, fetch))
    await started.wait()
    follower = asyncio.create_task(runtime.read_bookmarks(key, fetch))
    await asyncio.sleep(0)
    follower.cancel()
    with pytest.raises(asyncio.CancelledError):
        await follower
    release.set()
    assert await leader == [{"id": 1}]
    assert not runtime.bookmarks


async def test_cancelled_leader_does_not_cancel_other_bookmark_readers(runtime):
    entered = asyncio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        return [{"id": 2}]

    key = ("server", "alice", 1)
    leader = asyncio.create_task(runtime.read_bookmarks(key, fetch))
    await entered.wait()
    follower = asyncio.create_task(runtime.read_bookmarks(key, fetch))
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    assert await asyncio.wait_for(follower, 2) == [{"id": 2}]
    assert calls == 2
    assert not runtime.bookmarks


async def test_failed_bookmark_read_is_not_cached(runtime):
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OfficialTimeoutError("upstream down")
        return []

    key = ("server", "alice", 1)
    with pytest.raises(OfficialTimeoutError):
        await runtime.read_bookmarks(key, fetch)
    assert await runtime.read_bookmarks(key, fetch) == []
    assert calls == 2


async def test_total_budget_stops_retry_backoff(monkeypatch):
    monkeypatch.setattr(settings, "OFFICIAL_CLIENT_TOTAL_TIMEOUT", 0.03)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock") as http:
        client = OfficialGrimmoryClient(client=http, backoff_factor=1)
        with pytest.raises(OfficialTimeoutError, match="budget exhausted"):
            await client.get_current_user("alice")
    assert calls == 1


async def test_budget_is_shared_across_upstream_operations(monkeypatch):
    monkeypatch.setattr(settings, "OFFICIAL_CLIENT_TOTAL_TIMEOUT", 10)
    token = deadline.set(time.monotonic() + 0.04)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"id": 1})), base_url="http://mock") as http:
            client = OfficialGrimmoryClient(client=http)
            await client.get_current_user("alice")
            await asyncio.sleep(0.06)
            async def stall(*args, **kwargs):
                await asyncio.Event().wait()

            stalled = AsyncMock(side_effect=stall)
            monkeypatch.setattr(client, "_send_request_attempts", stalled)
            with pytest.raises(OfficialTimeoutError):
                await client.get_current_user("alice")
            stalled.assert_not_awaited()
    finally:
        deadline.reset(token)


async def test_slow_mutation_is_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "OFFICIAL_CLIENT_TOTAL_TIMEOUT", 0.02)
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock") as http:
        client = OfficialGrimmoryClient(client=http)
        with pytest.raises(OfficialTimeoutError):
            await client._send_request("POST", "/api/v1/bookmarks", auth_mode=AuthMode.NONE)
    assert calls == 1


async def test_streamed_download_body_can_outlive_header_budget(monkeypatch):
    monkeypatch.setattr(settings, "OFFICIAL_CLIENT_TOTAL_TIMEOUT", 0.02)

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.04)
            yield b"book-file"

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Body())), base_url="http://mock") as http:
        client = OfficialGrimmoryClient(client=http)
        response = await client._send_request("GET", "/download", auth_mode=AuthMode.NONE, stream=True)
        try:
            assert await response.aread() == b"book-file"
        finally:
            await response.aclose()


async def test_budget_cancellation_closes_streamed_error_body(monkeypatch):
    monkeypatch.setattr(settings, "OFFICIAL_CLIENT_TOTAL_TIMEOUT", 0.02)

    class Body(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b"error"

        async def aclose(self):
            self.closed = True

    body = Body()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503, stream=body)), base_url="http://mock") as http:
        client = OfficialGrimmoryClient(client=http)
        with pytest.raises(OfficialTimeoutError):
            await client._send_request("GET", "/download", auth_mode=AuthMode.NONE, stream=True)
    assert body.closed


async def test_lifespan_closes_pool_even_when_worker_stop_fails(monkeypatch):
    from grimmlink_adapter.main import create_app

    monkeypatch.setattr("grimmlink_adapter.main.MDNSAdvertiser.start", AsyncMock())
    monkeypatch.setattr("grimmlink_adapter.main.MDNSAdvertiser.stop", AsyncMock())
    monkeypatch.setattr("grimmlink_adapter.main.metadata_automation.start", AsyncMock())
    monkeypatch.setattr("grimmlink_adapter.main.metadata_automation.stop", AsyncMock(side_effect=RuntimeError("stop failed")))
    app = create_app()
    with pytest.raises(RuntimeError, match="stop failed"):
        async with app.router.lifespan_context(app):
            assert current_runtime() is not None
    assert current_runtime() is None


async def test_timing_logs_use_route_template_and_never_credentials(monkeypatch, caplog):
    monkeypatch.setattr(settings, "SLOW_REQUEST_MS", 0)
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)

    @app.get("/books/{book_id}")
    async def read(book_id: str):
        with measure("db"):
            await asyncio.sleep(0)
        return {"ok": True}

    with caplog.at_level(logging.INFO, logger="grimmlink_adapter.performance"):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/books/private-title?token=secret", headers={"Authorization": "Bearer hidden"})
    records = [r.message for r in caplog.records if r.name == "grimmlink_adapter.performance"]
    assert len(records) == 1
    assert "route=/books/{book_id}" in records[0]
    assert "db_ms=" in records[0]
    assert all(secret not in records[0] for secret in ["private-title", "secret", "hidden"])
    assert deadline.get() is None
