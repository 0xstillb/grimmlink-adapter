"""Serialize token rotation shared by reader and background authentication."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

from grimmlink_adapter.official.exceptions import OfficialTimeoutError
from grimmlink_adapter.performance import measure, remaining_budget

_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple[str, str], tuple[asyncio.Lock, int]]] = WeakKeyDictionary()


@asynccontextmanager
async def credential_refresh_lock(server: str, user_id: str | int) -> AsyncIterator[None]:
    """Serialize a user's shared refresh chain, without blocking other users.

    Count holders and waiters so unused locks do not accumulate indefinitely.
    Reader and metadata workers must use the same server/user key.
    """
    locks = _locks.setdefault(asyncio.get_running_loop(), {})
    key = (server.rstrip("/"), str(user_id))
    lock, count = locks.get(key, (asyncio.Lock(), 0))
    locks[key] = (lock, count + 1)
    acquired = False
    try:
        try:
            with measure("auth_wait"):
                budget = remaining_budget()
                if budget <= 0:
                    raise TimeoutError
                async with asyncio.timeout(budget):
                    await lock.acquire()
                    acquired = True
        except TimeoutError as exc:
            raise OfficialTimeoutError("Upstream budget exhausted while waiting for account refresh") from exc
        yield
    finally:
        if acquired:
            lock.release()
        _, count = locks[key]
        if count == 1:
            locks.pop(key)
        else:
            locks[key] = (lock, count - 1)
