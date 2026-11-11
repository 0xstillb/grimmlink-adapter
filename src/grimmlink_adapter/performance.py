"""Request budgets and timing without logging URLs, credentials or book data."""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from grimmlink_adapter.config import settings

logger = logging.getLogger(__name__)
deadline: ContextVar[float | None] = ContextVar("upstream_deadline", default=None)
timings: ContextVar[dict[str, float] | None] = ContextVar("request_timings", default=None)
_active_spans: ContextVar[frozenset[str]] = ContextVar("active_spans", default=frozenset())


def remaining_budget() -> float:
    end = deadline.get()
    return max(0.0, end - time.monotonic()) if end is not None else settings.OFFICIAL_CLIENT_TOTAL_TIMEOUT


@contextmanager
def measure(name: str) -> Iterator[None]:
    active = _active_spans.get()
    if name in active:
        yield
        return
    span_token = _active_spans.set(active | {name})
    start = time.monotonic()
    try:
        yield
    finally:
        values = timings.get()
        if values is not None:
            values[name] = values.get(name, 0.0) + (time.monotonic() - start) * 1000
        _active_spans.reset(span_token)


class RequestTimingMiddleware:
    """Share an upstream time budget across auth, retries and service calls.

    The deadline bounds upstream calls, not streamed download bodies. Timings
    overlap (e.g. auth includes HTTP); they are diagnostic, not additive.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.monotonic()
        end_token = deadline.set(start + settings.OFFICIAL_CLIENT_TOTAL_TIMEOUT)
        values: dict[str, float] = {}
        timing_token = timings.set(values)
        status = 500

        async def timed_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, timed_send)
        finally:
            elapsed = (time.monotonic() - start) * 1000
            route = getattr(scope.get("route"), "path", "unmatched")
            log = logger.info if elapsed >= settings.SLOW_REQUEST_MS else logger.debug
            log(
                "Request timing method=%s route=%s status=%s total_ms=%.1f auth_ms=%.1f "
                "auth_wait_ms=%.1f upstream_ms=%.1f db_ms=%.1f",
                scope["method"], route, status, elapsed, values.get("auth", 0),
                values.get("auth_wait", 0), values.get("upstream", 0), values.get("db", 0),
            )
            timings.reset(timing_token)
            deadline.reset(end_token)
