"""Book resolution and download routes.

Implements the legacy-compatible GET /books/by-hash/{book_hash} endpoint
that resolves plugin MD5 hashes to official book identifiers.

Response contract (frozen):
    GET /books/by-hash/{book_hash} → OfficialBookDTO (200) or 404/409/403/502
    Auth: x-auth-user + x-auth-key OR Bearer token (both accepted)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.grimmlink import (
    GrimmlinkReadStatusRequest,
    GrimmlinkReadStatusResponse,
)
from grimmlink_adapter.models.official import OfficialBookDTO
from grimmlink_adapter.official.auth import AuthMode, KOReaderAuth
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import (
    OfficialAuthError,
    OfficialBadResponseError,
    OfficialClientError,
    OfficialPermissionError,
    OfficialTimeoutError,
    OfficialTransportError,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials, require_client_credentials
from grimmlink_adapter.services.auth_service import AuthService
from grimmlink_adapter.services.book_service import (
    AmbiguousBookError,
    BookInaccessibleError,
    BookNotFoundError,
    BookResolutionUnavailableError,
    BookService,
    BookVerificationError,
    _book_contains_file,
)
from grimmlink_adapter.services.linked_auth import LinkedAuthUnavailable, get_linked_bearer
from grimmlink_adapter.services.progress_service import ProgressService, _parse_timestamp
from grimmlink_adapter.services.request_auth import get_official_bearer
from grimmlink_adapter.state.book_identity import BookIdentityStore
from grimmlink_adapter.state.cache import ProgressStateCache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/books", tags=["Books"])


async def _resolve_user_identity(creds: ClientCredentials) -> tuple[str, str]:
    """Resolve verified (server, user_id_str) from credentials.

    For MD5 auth: verifies credentials upstream via KOReader auth and uses
    the authenticated user ID.
    For Bearer auth: fetches user profile from upstream and uses the
    verified user ID.

    This ensures the cache key is always tied to an upstream-verified user ID,
    preventing cache collisions between accounts.
    """
    from grimmlink_adapter.config import settings

    server = settings.GRIMMORY_BASE_URL.rstrip("/")

    auth_service = AuthService()
    try:
        if creds.bearer_token and creds.username and creds.md5_key:
            md5_identity = await auth_service.authorize_client(
                ClientCredentials(username=creds.username, md5_key=creds.md5_key)
            )
            bearer_identity = await auth_service.authorize_client(
                ClientCredentials(bearer_token=creds.bearer_token)
            )
            if md5_identity.userId != bearer_identity.userId:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Credential identities do not match.",
                )
            return server, str(bearer_identity.userId)
        auth_response = await auth_service.authorize_client(creds)
        return server, str(auth_response.userId)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to verify user identity.",
        ) from exc


@router.get("/by-hash/{book_hash}", response_model=OfficialBookDTO)
async def get_book_by_hash(
    book_hash: str,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> Response | OfficialBookDTO:
    """Resolve a book by its MD5 hash using the configured identity source.

    Resolution chain: Grimmory DB current/initial when enabled; otherwise
    Adapter cache current/initial and exact upstream evidence.

    Returns the full OfficialBookDTO representation per the frozen contract.

    Auth: x-auth-user + x-auth-key OR Bearer token (both accepted).

    Errors:
        404 — hash unknown everywhere
        409 — multiple candidates (ambiguous, never silently choose)
        403 — book exists but user lacks access
        502 — upstream verification failed
        401 — missing/invalid credentials
    """
    # Optional native Grimmlink proxy: when enabled, preserve the official
    # fork's exact response instead of rebuilding it through several /api/v1
    # verification calls. This path is deliberately MD5-only because that is
    # the auth contract used by the KOReader plugin.
    if settings.GRIMMLINK_NATIVE_PROXY and creds.username and creds.md5_key and not creds.bearer_token:
        client = OfficialGrimmoryClient(
            koreader_auth=KOReaderAuth(username=creds.username, md5_key=creds.md5_key),
        )
        try:
            upstream = await client.request(
                "GET",
                f"/api/grimmlink/v1/books/by-hash/{quote(book_hash, safe='')}",
                auth_mode=AuthMode.KOREADER,
            )
            content_type = upstream.headers.get("content-type", "application/json")
            return Response(content=upstream.content, media_type=content_type.split(";", 1)[0])
        except OfficialPermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Book access was denied by Official Grimmory.") from exc
        except OfficialAuthError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed during book lookup.") from exc
        except OfficialBadResponseError as exc:
            code = status.HTTP_404_NOT_FOUND if exc.status_code == 404 else status.HTTP_502_BAD_GATEWAY
            raise HTTPException(status_code=code, detail="Official book lookup failed.") from exc
        except OfficialClientError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Official book lookup is unavailable.") from exc
        finally:
            await client.aclose()

    server, user = await _resolve_user_identity(creds)

    if not creds.bearer_token and creds.username and creds.md5_key:
        try:
            linked_bearer = await get_linked_bearer(server, user, creds.username, creds.md5_key)
        except LinkedAuthUnavailable as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except OfficialAuthError as exc:
            raise HTTPException(status_code=401, detail="Linked Grimmory session is invalid.") from exc
        except OfficialClientError as exc:
            raise HTTPException(status_code=502, detail="Failed to verify linked account.") from exc
        creds = ClientCredentials(
            username=creds.username, md5_key=creds.md5_key, bearer_token=linked_bearer,
        )

    service = BookService()

    try:
        identity = await service.resolve_book_by_hash(
            server=server,
            user=user,
            book_hash=book_hash,
            creds=creds,
        )
    except BookNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No book found for hash {book_hash}",
        ) from exc
    except AmbiguousBookError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except BookInaccessibleError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except BookVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to verify book accessibility: {exc}",
        ) from exc
    except BookResolutionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except OfficialAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed during book resolution.",
        ) from exc

    bearer = await service._get_bearer(creds)
    if not bearer:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="A Bearer token is required to verify full book details.",
        )
    try:
        full_data = await service.official_client.get_book_by_id(identity.book_id, bearer)
        book = OfficialBookDTO.model_validate(full_data)
        if book.id != identity.book_id or (
            identity.book_file_id != 0 and not _book_contains_file(full_data, identity.book_file_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Book identity changed during resolution.",
            )
        return book
    except OfficialPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Book access was revoked during resolution.",
        ) from exc
    except OfficialAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed during book resolution.",
        ) from exc
    except OfficialBadResponseError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.status_code == 404 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(
            status_code=code,
            detail="Upstream book details are unavailable.",
        ) from exc
    except OfficialClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to retrieve verified book details.",
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream book details are malformed.",
        ) from exc

@router.get("/{book_id}/download")
async def download_book(
    book_id: int,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> StreamingResponse:
    """Stream an authenticated Official download after checking its response headers/body."""
    bearer = await get_official_bearer(creds)
    client = OfficialGrimmoryClient()
    try:
        book_data = await client.get_book_by_id(book_id, bearer)
        book = OfficialBookDTO.model_validate(book_data)
        if book.id != book_id or book.primaryFile is None:
            raise HTTPException(status_code=502, detail="Official book details have no primary file.")
        upstream = await client.download_book_stream(book_id, bearer)
        content_type = upstream.headers.get("content-type", "application/octet-stream").split(";", 1)[0].lower()
        if content_type in {"application/json", "text/html", "application/problem+json"}:
            await upstream.aclose()
            raise HTTPException(status_code=502, detail="Official download returned an error document.")
        stream_iterator = upstream.aiter_bytes()
        try:
            first_chunk = await anext(stream_iterator)
        except StopAsyncIteration as exc:
            await upstream.aclose()
            raise HTTPException(status_code=502, detail="Official download returned an empty file.") from exc
        except httpx.TimeoutException as exc:
            await upstream.aclose()
            raise OfficialTimeoutError("Official download stream timed out.") from exc
        except httpx.RequestError as exc:
            await upstream.aclose()
            raise OfficialTransportError("Official download stream failed.") from exc
        if not first_chunk:
            await upstream.aclose()
            raise HTTPException(status_code=502, detail="Official download returned an empty file.")
        if content_type == "application/octet-stream" and first_chunk.lstrip().startswith((b"{", b"[", b"<!DOCTYPE", b"<html")):
            await upstream.aclose()
            raise HTTPException(status_code=502, detail="Official download returned an error document.")
    except HTTPException:
        await client.aclose()
        raise
    except OfficialPermissionError as exc:
        await client.aclose()
        raise HTTPException(status_code=403, detail="Book access was denied by Official Grimmory.") from exc
    except OfficialAuthError as exc:
        await client.aclose()
        raise HTTPException(status_code=401, detail="Upstream authentication failed.") from exc
    except OfficialBadResponseError as exc:
        await client.aclose()
        code = 404 if exc.status_code == 404 else 502
        raise HTTPException(status_code=code, detail="Official book or download is unavailable.") from exc
    except OfficialClientError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Official book download failed.") from exc
    except ValidationError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Official book details are malformed.") from exc

    raw_name = book.primaryFile.fileName or f"{book_id}.{book.primaryFile.extension or 'bin'}"
    safe_name = raw_name.replace("\r", "").replace("\n", "").replace('"', "'")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe_name, safe='')}",
    }
    if upstream.headers.get("content-length"):
        headers["Content-Length"] = upstream.headers["content-length"]

    async def body_stream() -> AsyncIterator[bytes]:
        try:
            yield first_chunk
            async for chunk in stream_iterator:
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(body_stream(), media_type=content_type, headers=headers)


@router.get("/read-statuses")
async def get_supported_read_statuses(
    creds: ClientCredentials = Depends(require_client_credentials),
) -> dict[str, list[str]]:
    """Return the status values accepted by the Official status endpoint."""
    return {"statuses": ["UNREAD", "READING", "RE_READING", "READ", "PARTIALLY_READ", "PAUSED", "WONT_READ", "ABANDONED"]}


@router.put("/{book_id}/status")
async def update_read_status(
    book_id: int,
    request: GrimmlinkReadStatusRequest | None = None,
    creds: ClientCredentials = Depends(require_client_credentials),
) -> GrimmlinkReadStatusResponse:
    """Update Official status and persist its authoritative manual timestamp."""
    if request is None or not request.status or not request.status.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status is required")
    status_value = request.status.strip().upper()
    if status_value == "COMPLETED":
        status_value = "READ"
    allowed = {"UNREAD", "READING", "RE_READING", "READ", "PARTIALLY_READ", "PAUSED", "WONT_READ", "ABANDONED"}
    if status_value not in allowed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported read status")

    bearer = await get_official_bearer(creds)
    client = OfficialGrimmoryClient()
    try:
        result = await client.update_read_status(book_id, status_value, bearer)
    except OfficialPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Book status update was denied by Official Grimmory.") from exc
    except OfficialAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Upstream authentication failed.") from exc
    except OfficialClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Official read-status service is unavailable.") from exc
    finally:
        await client.aclose()

    item = next((entry for entry in result if entry.get("bookId") == book_id), result[0] if result else {})
    raw_time = item.get("readStatusModifiedTime") or item.get("updatedAt")
    epoch, _ = _parse_timestamp(raw_time)
    timestamp_epoch = epoch or int(datetime.now(UTC).timestamp())
    owner_key = (
        f"user:{getattr(bearer, 'user_id', '')}"
        if creds.bearer_token else ProgressService._owner_key(creds)
    )
    await ProgressStateCache.set_manual_status_for_book(owner_key, book_id, status_value, timestamp_epoch)
    # Also materialize hash markers for already verified mappings so legacy
    # progress payloads without bookId still honor this manual status.
    if getattr(bearer, "user_id", None) is not None:
        from grimmlink_adapter.config import settings

        rows = await BookIdentityStore.lookup_by_book_id(
            settings.GRIMMORY_BASE_URL.rstrip("/"), str(bearer.user_id), book_id,
        )
        seen_hashes: set[str] = set()
        for row in rows:
            for book_hash in (row.current_hash, row.initial_hash):
                if book_hash and book_hash not in seen_hashes:
                    seen_hashes.add(book_hash)
                    await ProgressStateCache.set_manual_status(
                        owner_key, book_hash, status_value, timestamp_epoch,
                    )
    return GrimmlinkReadStatusResponse(bookId=book_id, status=status_value, updated=True)
