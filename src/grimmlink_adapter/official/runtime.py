"""Application-owned connections and in-flight bookmark reads (no result cache)."""

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any
from weakref import WeakKeyDictionary

import httpx

from grimmlink_adapter.config import settings


class _ReadAbandoned(Exception):
    """The leader disconnected; remaining readers should fetch independently."""


class BorrowedTransport(httpx.AsyncBaseTransport):
    """Clients retain separate cookies/auth; only their socket pool is shared."""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self.transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.transport.handle_async_request(request)

    async def aclose(self) -> None:
        pass  # The application lifespan owns the real transport.


class UpstreamRuntime:
    def __init__(self) -> None:
        self.transports: dict[tuple[str, bool | str], httpx.AsyncBaseTransport] = {}
        self.bookmarks: dict[tuple[Any, ...], asyncio.Future[list[dict[str, Any]]]] = {}

    def transport(self, server: str, verify: bool | str) -> BorrowedTransport:
        key = (server, verify)
        if key not in self.transports:
            self.transports[key] = httpx.AsyncHTTPTransport(
                verify=verify,
                limits=httpx.Limits(
                    max_connections=settings.OFFICIAL_CLIENT_MAX_CONNECTIONS,
                    max_keepalive_connections=settings.OFFICIAL_CLIENT_MAX_CONNECTIONS,
                    keepalive_expiry=30,
                ),
            )
        return BorrowedTransport(self.transports[key])

    async def read_bookmarks(
        self, key: tuple[Any, ...], fetch: Callable[[], Awaitable[list[dict[str, Any]]]],
    ) -> list[dict[str, Any]]:
        pending = self.bookmarks.get(key)
        if pending is not None:
            # A cancelled follower must not cancel the leader's HTTP request.
            try:
                return deepcopy(await asyncio.shield(pending))
            except _ReadAbandoned:
                return await self.read_bookmarks(key, fetch)
        pending = asyncio.get_running_loop().create_future()
        self.bookmarks[key] = pending
        try:
            result = await fetch()
            pending.set_result(result)
            return deepcopy(result)
        except asyncio.CancelledError:
            pending.set_exception(_ReadAbandoned())
            pending.exception()
            raise
        except Exception as exc:
            pending.set_exception(exc)
            pending.exception()  # Consume even when there are no followers.
            raise
        finally:
            if self.bookmarks.get(key) is pending:
                self.bookmarks.pop(key)

    def invalidate_bookmarks(self, server: str) -> None:
        # Existing readers may finish; new reads must never join a pre-write GET.
        for key in list(self.bookmarks):
            if key[0] == server:
                self.bookmarks.pop(key)

    async def aclose(self) -> None:
        for transport in self.transports.values():
            await transport.aclose()
        self.transports.clear()
        self.bookmarks.clear()


_runtimes: WeakKeyDictionary[asyncio.AbstractEventLoop, UpstreamRuntime] = WeakKeyDictionary()


def current_runtime() -> UpstreamRuntime | None:
    return _runtimes.get(asyncio.get_running_loop())


async def start_runtime() -> None:
    loop = asyncio.get_running_loop()
    if loop in _runtimes:
        raise RuntimeError("Upstream runtime already started on this event loop")
    _runtimes[loop] = UpstreamRuntime()


async def stop_runtime() -> None:
    runtime = _runtimes.pop(asyncio.get_running_loop(), None)
    if runtime is not None:
        await runtime.aclose()
