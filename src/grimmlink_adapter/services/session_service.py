"""Reading session recording service with idempotency support and Official Grimmory adapter."""

from __future__ import annotations

import contextlib
import json
import logging
import math
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.grimmlink import (
    GrimmlinkReadingSessionBatchRequest,
    GrimmlinkReadingSessionBatchResponse,
    GrimmlinkReadingSessionResultItem,
    GrimmlinkReadingSessionSingleRequest,
)
from grimmlink_adapter.models.internal import ReadingSessionRecord
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import (
    OfficialAuthError,
    OfficialClientError,
    OfficialPermissionError,
    OfficialTimeoutError,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.request_auth import get_official_bearer
from grimmlink_adapter.state.book_identity import BookIdentityStore
from grimmlink_adapter.state.outbox import IdempotencyManager
from grimmlink_adapter.state.reading_sessions import ReadingSessionStore

logger = logging.getLogger(__name__)


def generate_session_idempotency_key(
    server: str = "",
    user: str | int = "",
    book_id: int | str = 0,
    book_hash: str | None = None,
    start_time: str = "",
    end_time: str = "",
    device: str | None = None,
    device_id: str | None = None,
    *,
    username: str | None = None,
) -> str:
    """Create canonical idempotency key from preserved legacy identity:
    server/user/book/hash/start/end/device (change only with evidence).
    """
    # Backward compatibility with old scaffold positional signature:
    # generate_session_idempotency_key(username, book_id, start_time, end_time, device_id)
    if isinstance(user, int):
        actual_user = server
        actual_book_id = user
        actual_start = str(book_id)
        actual_end = str(book_hash or "")
        actual_device = start_time
        base_server = settings.GRIMMORY_BASE_URL.rstrip("/")
        return generate_session_idempotency_key(
            server=base_server,
            user=actual_user,
            book_id=actual_book_id,
            book_hash=None,
            start_time=actual_start,
            end_time=actual_end,
            device_id=actual_device,
        )

    server_norm = (server or "").rstrip("/")
    user_val = str(user or username or "default").strip()
    book_str = str(book_id)
    hash_str = (book_hash or "").strip()
    start_str = (start_time or "").strip()
    end_str = (end_time or "").strip()
    if device and device_id and device != device_id:
        dev_str = f"{device}:{device_id}".strip()
    else:
        dev_str = (device_id or device or "").strip()
    return f"{server_norm}/{user_val}/{book_str}/{hash_str}/{start_str}/{end_str}/{dev_str}"


def validate_session_timing(
    start_time: str,
    end_time: str,
    duration_seconds: int | None,
) -> tuple[datetime, datetime]:
    """Validate that start_time and end_time form a valid chronological interval
    and duration_seconds is strictly positive.
    """
    if not start_time or not end_time:
        raise ValueError("startTime and endTime are required.")
    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid timestamp format: {exc}") from exc

    if (start_dt.tzinfo is None) != (end_dt.tzinfo is None):
        raise ValueError("startTime and endTime must both include a timezone or both omit it.")
    if end_dt <= start_dt:
        raise ValueError(
            f"Invalid session order: endTime ({end_time}) must be after startTime ({start_time})."
        )

    if duration_seconds is None or duration_seconds <= 0:
        raise ValueError(f"Invalid durationSeconds: must be positive, got {duration_seconds}.")

    return start_dt, end_dt


def _official_progress(value: float) -> float:
    """Legacy session progress is a percentage; Official expects a 0..1 fraction."""
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("Reading-session progress must be between 0 and 100 percent.")
    return round(value / 100.0, 6)


def _same_timestamp(left: object, right: str) -> bool:
    if not isinstance(left, str):
        return False
    try:
        return datetime.fromisoformat(left.replace("Z", "+00:00")) == datetime.fromisoformat(
            right.replace("Z", "+00:00")
        )
    except ValueError:
        return left == right


def _official_payload(record: ReadingSessionRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "bookId": record.book_id,
        "startTime": record.start_time,
        "endTime": record.end_time,
        "durationSeconds": record.duration_seconds,
    }
    if record.start_progress is not None:
        payload["startProgress"] = _official_progress(record.start_progress)
    if record.end_progress is not None:
        payload["endProgress"] = _official_progress(record.end_progress)
    for source, target in (
        (record.book_type, "bookType"),
        (record.start_location, "startLocation"),
        (record.end_location, "endLocation"),
    ):
        if source is not None:
            payload[target] = source
    return payload


class SessionService:
    """Manages recording and batching of reading sessions."""

    def __init__(self, official_client: OfficialGrimmoryClient | None = None) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()

    async def get_reading_sessions(
        self, book_id: int, limit: int = 50, creds: ClientCredentials | None = None,
    ) -> list[dict[str, Any]]:
        """Read paginated Official history using the requesting user's bearer."""
        _, _, bearer = await self._resolve_auth(creds, "default", None)
        if book_id <= 0 or limit <= 0:
            raise HTTPException(status_code=400, detail="bookId and limit must be positive.")
        result: list[dict[str, Any]] = []
        page = 0
        page_size = min(limit, 100)
        try:
            while len(result) < limit:
                chunk = await self.official_client.get_reading_sessions_for_book(
                    book_id, bearer, page=page, size=page_size
                )
                result.extend(chunk[: limit - len(result)])
                if len(chunk) < page_size:
                    break
                page += 1
        except (OfficialAuthError, OfficialPermissionError) as exc:
            raise HTTPException(status_code=403, detail="Reading-session access denied.") from exc
        except OfficialClientError as exc:
            raise HTTPException(status_code=502, detail="Reading-session history unavailable.") from exc
        return result

    async def _resolve_auth(
        self,
        creds: ClientCredentials | None,
        username: str,
        bearer_token: str | None,
    ) -> tuple[str, str, str]:
        """Resolve server, owner_key, and bearer token string."""
        server = settings.GRIMMORY_BASE_URL.rstrip("/")
        if bearer_token:
            user_id = str(username) if username and username != "default" else "1"
            return server, user_id, bearer_token
        if creds is not None:
            bearer = await get_official_bearer(creds)
            user_id = str(getattr(bearer, "user_id", creds.username or username or "1"))
            return server, user_id, str(bearer)

        # Fallback when no creds provided (e.g. testing or configured client)
        tok = (
            self.official_client.jwt_auth.access_token
            if self.official_client.jwt_auth and self.official_client.jwt_auth.access_token
            else "mock_bearer_token"
        )
        return server, username or "default", tok

    async def _resolve_book_id(
        self,
        book_id: int | None,
        book_hash: str | None,
        owner_key: str,
        server: str,
    ) -> int:
        """Resolve authoritative book ID from bookId or bookHash."""
        if book_id is not None and book_id > 0:
            return book_id
        if not book_hash or not book_hash.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either bookId or bookHash is required for reading sessions.",
            )
        hash_val = book_hash.strip()
        rows = await BookIdentityStore.lookup_by_current_hash(server, owner_key, hash_val)
        if not rows:
            rows = await BookIdentityStore.lookup_by_initial_hash(server, owner_key, hash_val)
        if len(rows) == 1:
            return rows[0].book_id
        if len(rows) > 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Ambiguous book hash {hash_val}; multiple books matched.",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No verified book found matching hash {hash_val}.",
        )

    async def _probe_upstream_session(
        self,
        book_id: int,
        start_time: str,
        end_time: str,
        bearer_token: str,
    ) -> list[int]:
        """Exhaust Official pages; errors are uncertainty, never evidence of absence."""
        matches: list[int] = []
        for page in range(1000):
            sessions = await self.official_client.get_reading_sessions_for_book(
                book_id, bearer_token, page=page, size=100
            )
            for session in sessions:
                if _same_timestamp(session.get("startTime"), start_time) and _same_timestamp(
                    session.get("endTime"), end_time
                ):
                    raw_id = session.get("id")
                    if raw_id is None:
                        raise ValueError("Matching upstream session has no ID")
                    matches.append(int(raw_id))
            if len(sessions) < 100:
                return matches
        raise RuntimeError("Reading-session probe exceeded 1000 pages")

    async def _reconcile(
        self, record: ReadingSessionRecord, bearer_token: str,
    ) -> tuple[str, int | None]:
        """Return found/absent/uncertain without confusing another device's session."""
        try:
            upstream_ids = await self._probe_upstream_session(
                record.book_id, record.start_time, record.end_time, bearer_token
            )
            local = await ReadingSessionStore.list_matching(
                record.server, record.user_id, record.book_id, record.start_time, record.end_time
            )
        except Exception as exc:
            logger.warning(
                "Reading-session reconciliation deferred for book %d: %s",
                record.book_id, type(exc).__name__,
            )
            return "uncertain", None
        claimed = {sid for key, state, sid in local if key != record.idempotency_key and state == "COMMITTED" and sid is not None}
        candidates = set(upstream_ids) - claimed
        other_unresolved = any(
            key != record.idempotency_key and (state != "COMMITTED" or sid is None)
            for key, state, sid in local
        )
        if len(candidates) == 1 and not other_unresolved:
            return "found", next(iter(candidates))
        if candidates or (upstream_ids and other_unresolved):
            return "uncertain", None
        return "absent", None

    async def _record_single_session_internal(
        self,
        *,
        book_id: int,
        book_hash: str | None,
        book_type: str | None,
        start_time: str,
        end_time: str,
        duration_seconds: int,
        device: str | None,
        device_id: str | None,
        current_page: int | None = None,
        total_pages: int | None = None,
        start_progress: float | None = None,
        end_progress: float | None = None,
        start_page: int | None = None,
        end_page: int | None = None,
        start_location: str | None = None,
        end_location: str | None = None,
        server: str,
        user: str,
        bearer_token: str,
    ) -> tuple[int | None, str, str | None]:
        """Process a single reading session.
        Returns: (sessionId, status, message)
        where status is 'created', 'duplicate', 'pending', or 'error'.
        """
        # 1. Validate timing & duration
        try:
            validate_session_timing(start_time, end_time, duration_seconds)
        except ValueError as exc:
            return None, "error", str(exc)

        # 2. Canonical idempotency key
        idempotency_key = generate_session_idempotency_key(
            server=server,
            user=user,
            book_id=book_id,
            book_hash=book_hash,
            start_time=start_time,
            end_time=end_time,
            device=device,
            device_id=device_id,
        )

        idemp_resp = await IdempotencyManager.get_response(idempotency_key)
        if idemp_resp is not None:
            try:
                cached_data = json.loads(idemp_resp[1])
                cached_id = cached_data.get("id") or cached_data.get("sessionId")
                return cached_id, "duplicate", "Session already committed"
            except (json.JSONDecodeError, ValueError):
                return None, "duplicate", "Session already committed"

        # Persist the complete original payload before crossing the POST boundary.
        record = ReadingSessionRecord(
            idempotency_key=idempotency_key,
            server=server,
            user_id=user,
            book_id=book_id,
            book_hash=book_hash,
            book_type=book_type,
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration_seconds,
            device=device,
            device_id=device_id,
            current_page=current_page,
            total_pages=total_pages,
            start_progress=start_progress,
            end_progress=end_progress,
            start_page=start_page,
            end_page=end_page,
            start_location=start_location,
            end_location=end_location,
            official_session_id=None,
            status="POSTING",
            retry_count=0,
            last_error=None,
        )
        try:
            official_payload = _official_payload(record)
        except ValueError as exc:
            return None, "error", str(exc)
        if not await ReadingSessionStore.create_posting(record):
            existing = await ReadingSessionStore.get_by_idempotency_key(idempotency_key)
            if existing is None:
                return None, "pending", "Session state changed; retry later"
            if existing.status == "COMMITTED":
                return existing.official_session_id, "duplicate", "Session already committed"
            if existing.status == "POSTING":
                await ReadingSessionStore.requeue_stale_posting(idempotency_key)
                existing = await ReadingSessionStore.get_by_idempotency_key(idempotency_key)
                if existing is None or existing.status == "POSTING":
                    return None, "pending", "Session POST is still in flight"
            outcome, upstream_id = await self._reconcile(existing, bearer_token)
            if outcome == "uncertain":
                return None, "pending", "Upstream session state is uncertain"
            if outcome == "found" and upstream_id is not None:
                await ReadingSessionStore.mark_committed(idempotency_key, upstream_id)
                await IdempotencyManager.record_response(
                    idempotency_key, "SESSION_RECORD", 200,
                    json.dumps({"id": upstream_id, "status": "duplicate"}),
                )
                return upstream_id, "duplicate", "Session already committed upstream"
            # A missing GET result cannot prove that a timed-out POST will never commit.
            return None, "pending", "Upstream has not confirmed this session; replay deferred"

        # 7. POST to Official Grimmory
        try:
            resp = await self.official_client.create_reading_session(
                official_payload, bearer_token
            )
        except OfficialTimeoutError as exc:
            await ReadingSessionStore.mark_failed(idempotency_key, f"Timeout: {exc}")
            raise
        except OfficialClientError as exc:
            await ReadingSessionStore.mark_failed(idempotency_key, str(exc))
            raise
        except Exception as exc:
            await ReadingSessionStore.mark_failed(idempotency_key, str(exc))
            return None, "error", str(exc)

        created_id: int | None = None
        if isinstance(resp, dict):
            raw_id = resp.get("id") or resp.get("sessionId")
            if raw_id is not None:
                with contextlib.suppress(ValueError, TypeError):
                    created_id = int(raw_id)

        # 8. Mark COMMITTED in SQLite and IdempotencyManager
        await ReadingSessionStore.mark_committed(idempotency_key, created_id)
        await IdempotencyManager.record_response(
            idempotency_key,
            "SESSION_RECORD",
            200,
            json.dumps({"id": created_id, "status": "created"}),
        )

        return created_id, "created", None

    async def record_session(
        self,
        session: GrimmlinkReadingSessionSingleRequest,
        creds: ClientCredentials | None = None,
        username: str = "default",
        bearer_token: str | None = None,
    ) -> bool:
        """Record a single reading session idempotently."""
        server, user, bearer = await self._resolve_auth(creds, username, bearer_token)
        book_id = await self._resolve_book_id(session.bookId, session.bookHash, user, server)

        # Validate timing upfront for single request
        try:
            validate_session_timing(session.startTime, session.endTime, session.durationSeconds)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        await self.recover_pending_sessions(bearer_token=bearer, user_id=user, server=server)

        try:
            _, status_val, msg = await self._record_single_session_internal(
                book_id=book_id,
                book_hash=session.bookHash,
                book_type=session.bookType,
                start_time=session.startTime,
                end_time=session.endTime,
                duration_seconds=session.durationSeconds,
                device=session.device,
                device_id=session.deviceId,
                current_page=session.currentPage,
                total_pages=session.totalPages,
                start_progress=session.startProgress,
                end_progress=session.endProgress,
                start_page=getattr(session, "startPage", None),
                end_page=getattr(session, "endPage", None),
                start_location=session.startLocation,
                end_location=session.endLocation,
                server=server,
                user=user,
                bearer_token=bearer,
            )
        except OfficialTimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Upstream timeout while recording reading session: {exc}",
            ) from exc
        except OfficialPermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden access to upstream reading session endpoint.",
            ) from exc
        except OfficialAuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Upstream authentication failed.",
            ) from exc
        except OfficialClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Upstream error: {exc}",
            ) from exc

        if status_val == "pending":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=msg or "Reading session is awaiting reconciliation.",
            )
        if status_val == "error":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=msg or "Failed to record reading session.",
            )

        return True

    async def record_sessions_batch(
        self,
        request: GrimmlinkReadingSessionBatchRequest,
        creds: ClientCredentials | None = None,
        username: str = "default",
        bearer_token: str | None = None,
    ) -> GrimmlinkReadingSessionBatchResponse:
        """Record batch of reading sessions idempotently, fanning out into single POSTs."""
        server, user, bearer = await self._resolve_auth(creds, username, bearer_token)
        book_id = await self._resolve_book_id(request.bookId, request.bookHash, user, server)
        await self.recover_pending_sessions(bearer_token=bearer, user_id=user, server=server)

        results: list[GrimmlinkReadingSessionResultItem] = []

        for i, item in enumerate(request.sessions):
            book_hash = getattr(item, "bookHash", None) or request.bookHash
            device = getattr(item, "device", None) or request.device
            device_id = getattr(item, "deviceId", None) or request.deviceId
            book_type = request.bookType
            current_page = getattr(item, "currentPage", None)
            total_pages = getattr(item, "totalPages", None)

            try:
                session_id, status_val, msg = await self._record_single_session_internal(
                    book_id=book_id,
                    book_hash=book_hash,
                    book_type=book_type,
                    start_time=item.startTime,
                    end_time=item.endTime,
                    duration_seconds=item.durationSeconds,
                    device=device,
                    device_id=device_id,
                    current_page=current_page,
                    total_pages=total_pages,
                    start_progress=item.startProgress,
                    end_progress=item.endProgress,
                    start_page=item.startPage,
                    end_page=item.endPage,
                    start_location=item.startLocation,
                    end_location=item.endLocation,
                    server=server,
                    user=user,
                    bearer_token=bearer,
                )
                results.append(
                    GrimmlinkReadingSessionResultItem(
                        index=i,
                        sessionId=session_id,
                        status=status_val,
                        message=msg,
                        startTime=item.startTime,
                        endTime=item.endTime,
                    )
                )
            except OfficialTimeoutError as exc:
                results.append(
                    GrimmlinkReadingSessionResultItem(
                        index=i,
                        sessionId=None,
                        status="error",
                        message=f"Timeout: {exc}",
                        startTime=item.startTime,
                        endTime=item.endTime,
                    )
                )
            except Exception as exc:
                results.append(
                    GrimmlinkReadingSessionResultItem(
                        index=i,
                        sessionId=None,
                        status="error",
                        message=str(exc),
                        startTime=item.startTime,
                        endTime=item.endTime,
                    )
                )

        success_count = sum(1 for r in results if r.status in ("created", "duplicate"))

        return GrimmlinkReadingSessionBatchResponse(
            totalRequested=len(request.sessions),
            successCount=success_count,
            results=results,
        )

    async def recover_pending_sessions(
        self,
        bearer_token: str,
        user_id: str,
        server: str,
        limit: int = 100,
    ) -> int:
        """Reconcile only this authenticated owner's pending writes on their next request.

        An absent GET result is not proof that a prior POST cannot commit later;
        such records remain pending rather than being automatically replayed.
        """
        pending = await ReadingSessionStore.list_pending(server, user_id, limit=limit)
        reconciled_count = 0
        for rec in pending:
            if rec.status == "POSTING":
                await ReadingSessionStore.requeue_stale_posting(rec.idempotency_key)
            outcome, upstream_id = await self._reconcile(rec, bearer_token)
            if outcome == "found" and upstream_id is not None:
                await ReadingSessionStore.mark_committed(rec.idempotency_key, upstream_id)
                await IdempotencyManager.record_response(
                    rec.idempotency_key,
                    "SESSION_RECORD",
                    200,
                    json.dumps({"id": upstream_id, "status": "duplicate"}),
                )
                reconciled_count += 1
        return reconciled_count
