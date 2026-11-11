"""Versioned v2 routes and aliases for the GrimmLink client contract."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse

from grimmlink_adapter.api.books import (
    download_book,
    get_book_by_hash,
    get_supported_read_statuses,
    update_read_status,
)
from grimmlink_adapter.config import settings
from grimmlink_adapter.models.automation import LibraryPolicy
from grimmlink_adapter.models.grimmlink import (
    GrimmlinkBookSummary,
    GrimmlinkReadStatusRequest,
    GrimmlinkReadStatusResponse,
    GrimmlinkShelfSummary,
    KoreaderProgressPayload,
)
from grimmlink_adapter.models.v2 import (
    V2BookMetadataUpdate,
    V2LocalMetadataRequest,
    V2LocalPasswordLogin,
    V2ProgressPosition,
    V2ProgressRequest,
    V2SessionResponse,
)
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import OfficialAuthError, OfficialClientError
from grimmlink_adapter.official.identity_lookup import (
    GrimmoryIdentityLookup,
    GrimmoryIdentityLookupError,
)
from grimmlink_adapter.security.auth_extractor import (
    ClientCredentials,
    get_adapter_session,
    require_v2_credentials,
)
from grimmlink_adapter.security.metadata_vault import metadata_cipher
from grimmlink_adapter.services.diagnostics import collect_diagnostics
from grimmlink_adapter.services.metadata_automation import (
    MetadataBlocked,
    check_storage,
    mapped_root,
    metadata_automation,
    metadata_execution_lock,
    server_key,
)
from grimmlink_adapter.services.opf_ingestion import (
    OPFAuthorizationError,
    OPFFieldLockError,
    OPFIdentityError,
    OPFIngestionError,
    OPFIngestionService,
    OPFSourceMutationError,
    OPFSourceMutationProtectionError,
)
from grimmlink_adapter.services.progress_service import ProgressService, snapshot_from_official
from grimmlink_adapter.services.prose_converter import prose_converter
from grimmlink_adapter.services.request_auth import get_official_bearer
from grimmlink_adapter.services.shelf_service import ShelfService
from grimmlink_adapter.state.adapter_sessions import AdapterSession, AdapterSessionStore
from grimmlink_adapter.state.cache import ProgressStateCache
from grimmlink_adapter.state.metadata_automation import MetadataAutomationStore

router = APIRouter(tags=["GrimmLink v2"])
_progress_service = ProgressService()
_shelf_service = ShelfService()


async def _issue_session(username: str, md5_key: str) -> V2SessionResponse:
    creds = ClientCredentials(username=username, md5_key=md5_key)
    # The resolver checks the reader credentials and verifies the linked
    # Grimmory account belongs to the same user before returning its identity.
    bearer = await get_official_bearer(creds)
    user_id = getattr(bearer, "user_id", None)
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise HTTPException(status_code=401, detail="Linked Grimmory identity is unavailable.")
    token, session = await AdapterSessionStore.create(username, md5_key, user_id)
    return V2SessionResponse(
        accessToken=token,
        expiresAt=session.expires_at,
        username=username,
        userId=user_id,
    )


@router.post("/auth/session", response_model=V2SessionResponse)
async def create_session(
    x_auth_user: str | None = Header(None, alias="x-auth-user"),
    x_auth_key: str | None = Header(None, alias="x-auth-key"),
) -> V2SessionResponse:
    if not x_auth_user or not x_auth_key:
        raise HTTPException(status_code=401, detail="KOSync username and MD5 key are required.")
    return await _issue_session(x_auth_user, x_auth_key)


@router.post("/local/auth/session", response_model=V2SessionResponse)
async def create_local_ui_session(request: V2LocalPasswordLogin) -> V2SessionResponse:
    try:
        async with OfficialGrimmoryClient() as client:
            login = await client.login(request.username, request.password)
            profile = await client.get_current_user(login.token)
    except OfficialAuthError as exc:
        raise HTTPException(status_code=401, detail="Invalid Grimmory username or password.") from exc
    except OfficialClientError as exc:
        raise HTTPException(status_code=502, detail="Grimmory authentication is unavailable.") from exc
    user_id = profile.get("id", profile.get("userId"))
    if isinstance(user_id, str) and user_id.isdigit():
        user_id = int(user_id)
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise HTTPException(status_code=502, detail="Grimmory account identity is unavailable.")
    username = str(profile.get("username") or request.username)
    token, session = await AdapterSessionStore.create_official(
        username, user_id, login.token, login.refreshToken, scope="metadata_ui",
    )
    return V2SessionResponse(
        accessToken=token, expiresAt=session.expires_at, username=username, userId=user_id,
    )


@router.post("/local/auth/session/auto", response_model=V2SessionResponse)
async def create_local_ui_auto_session() -> V2SessionResponse:
    """Issue a short-lived, local-UI-only session from the bound background account."""
    owners = await MetadataAutomationStore.query(
        "SELECT DISTINCT owner_id FROM metadata_libraries WHERE server=? ORDER BY owner_id",
        (server_key(),),
    )
    if len(owners) == 1:
        user_id = int(owners[0]["owner_id"])
    elif len(owners) > 1:
        raise HTTPException(status_code=409, detail="metadata_ui_operator_ambiguous")
    else:
        accounts = await MetadataAutomationStore.query(
            "SELECT user_id FROM metadata_accounts WHERE server=? ORDER BY user_id",
            (server_key(),),
        )
        if len(accounts) != 1:
            detail = "background_account_not_connected" if not accounts else "metadata_ui_operator_ambiguous"
            raise HTTPException(status_code=409, detail=detail)
        user_id = int(accounts[0]["user_id"])

    account_rows = await MetadataAutomationStore.query(
        "SELECT encrypted_credentials,blocked_reason FROM metadata_accounts WHERE server=? AND user_id=?",
        (server_key(), user_id),
    )
    if not account_rows:
        raise HTTPException(status_code=409, detail="background_account_not_connected")
    if account_rows[0]["blocked_reason"]:
        raise HTTPException(status_code=409, detail="background_account_reconnect_required")
    try:
        access_token = await metadata_automation.bearer(user_id)
        fresh_rows = await MetadataAutomationStore.query(
            "SELECT encrypted_credentials FROM metadata_accounts WHERE server=? AND user_id=?",
            (server_key(), user_id),
        )
        saved = json.loads(metadata_cipher().decrypt(fresh_rows[0]["encrypted_credentials"].encode()))
    except MetadataBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail="background_account_reconnect_required") from exc
    username = saved.get("username")
    if not isinstance(username, str) or not username:
        raise HTTPException(status_code=409, detail="background_account_reconnect_required")
    token, session = await AdapterSessionStore.create_official(
        username, user_id, access_token, saved.get("refresh"), scope="metadata_ui",
    )
    return V2SessionResponse(
        accessToken=token, expiresAt=session.expires_at, username=username, userId=user_id,
    )


@router.delete("/auth/session", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session: tuple[str, AdapterSession] = Depends(get_adapter_session),
) -> Response:
    await AdapterSessionStore.revoke(session[0])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/books/resolve")
async def resolve_book(
    hash: str = Query(..., min_length=1),
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> object:
    return await get_book_by_hash(hash, creds)


@router.get("/books/{book_id}/content")
async def get_content(
    book_id: int,
    hash: str = Query(..., min_length=1),
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> StreamingResponse:
    resolved = await get_book_by_hash(hash, creds)
    if isinstance(resolved, Response):
        try:
            resolved_data = json.loads(bytes(resolved.body or b""))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="Hash lookup returned an invalid identity.") from exc
        resolved_id = resolved_data.get("id") if isinstance(resolved_data, dict) else None
    else:
        resolved_id = getattr(resolved, "id", None)
    if isinstance(resolved_id, bool) or not isinstance(resolved_id, int):
        raise HTTPException(status_code=502, detail="Hash lookup did not return a verified book identity.")
    if resolved_id != book_id:
        raise HTTPException(status_code=409, detail="Book ID does not match the verified content hash.")
    return await download_book(book_id, creds)


@router.get("/books/{book_id}/hash")
async def get_primary_file_hash(
    book_id: int,
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> dict[str, object]:
    bearer = await get_official_bearer(creds)
    async with OfficialGrimmoryClient() as client:
        book = await client.get_book_by_id(book_id, bearer)
    if not isinstance(book, dict) or book.get("id") != book_id:
        raise HTTPException(status_code=502, detail="Grimmory returned a different book identity.")
    primary = book.get("primaryFile")
    file_id = primary.get("id") if isinstance(primary, dict) else None
    if not isinstance(file_id, int) or isinstance(file_id, bool) or file_id <= 0:
        raise HTTPException(status_code=404, detail="Book has no primary file.")
    lookup = GrimmoryIdentityLookup()
    if not lookup.enabled:
        raise HTTPException(status_code=501, detail="Read-only hash lookup is not configured.")
    try:
        candidates = await lookup.lookup_file_id(file_id)
    except GrimmoryIdentityLookupError as exc:
        raise HTTPException(status_code=502, detail="Read-only hash lookup failed.") from exc
    matching = [row for row in candidates if row.book_id == book_id and row.current_hash]
    if len(matching) != 1:
        raise HTTPException(status_code=409, detail="Primary file hash could not be verified.")
    return {"bookId": book_id, "bookFileId": file_id, "bookHash": matching[0].current_hash}


@router.get("/shelves")
async def list_regular_shelves_v2(
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> list[GrimmlinkShelfSummary]:
    # Magic shelves are optional and may depend on upstream /app routes that
    # Grimmory plans to remove. They must never make regular shelf sync fail.
    return await _shelf_service.list_shelves("regular", creds)


@router.get("/shelves/{shelf_id}/items")
async def list_shelf_items(
    shelf_id: int,
    limit: int | None = Query(None, ge=1, le=500),
    offset: int | None = Query(None, ge=0),
    cursor: str | None = Query(None),
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> list[GrimmlinkBookSummary]:
    return await _shelf_service.list_shelf_books("regular", shelf_id, creds, limit, offset, cursor)


@router.put("/shelves/{shelf_id}/items/{book_id}")
async def add_shelf_item(
    shelf_id: int,
    book_id: int,
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> dict[str, object]:
    return await _shelf_service.add_book_to_shelf("regular", shelf_id, book_id, creds)


@router.delete("/shelves/{shelf_id}/items/{book_id}")
async def remove_shelf_item(
    shelf_id: int,
    book_id: int,
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> object:
    result = await _shelf_service.remove_book_from_shelf("regular", shelf_id, book_id, creds)
    return result.model_dump()


@router.get("/progress/{book_hash}")
async def get_v2_progress(
    book_hash: str,
    reader: str = Query(..., pattern="^(koreader|readest)$"),
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> dict[str, object]:
    progress = None
    try:
        progress = await _progress_service.get_progress(book_hash, creds)
    except HTTPException as exc:
        if exc.status_code != 404 and not (reader == "koreader" and exc.status_code == 501):
            raise
    native_snapshot = None
    if creds.username and creds.md5_key:
        try:
            native = await _progress_service.get_native_progress(book_hash, creds)
            native_snapshot = snapshot_from_official(native, book_hash)
            if native_snapshot is not None and native_snapshot.xpointer is None:
                native_location = native_snapshot.native_location or ""
                if native_location.startswith("/body") and "/DocFragment" in native_location:
                    native_snapshot.xpointer = native_location
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
    if progress is None and native_snapshot is None:
        raise HTTPException(status_code=404, detail="No progress found for book hash.")
    if progress is None and native_snapshot is not None and native_snapshot.format is None:
        try:
            _, _, _, mapped_format = await _progress_service._resolve_bearer_book(creds, book_hash)
            native_snapshot.format = mapped_format
        except HTTPException as exc:
            if exc.status_code not in (404, 501):
                raise

    book_time = _progress_time(progress.updatedAt if progress else None)
    native_time = native_snapshot.timestamp_epoch if native_snapshot else None
    # Prefer the target reader's own locator when the two writes have equal or
    # unknown timestamps. Never combine locators from different reading events.
    book_is_newer = (
        progress is not None and (native_snapshot is None or
        (book_time is not None and native_time is not None and book_time > native_time))
    )
    native_is_newer = (
        native_snapshot is not None and (progress is None or
        (book_time is not None and native_time is not None and native_time > book_time))
    )
    preferred = progress if reader == "readest" else native_snapshot
    if reader == "readest" and native_is_newer:
        preferred = native_snapshot
    if reader == "koreader" and book_is_newer:
        preferred = progress

    # A newer snapshot is not useful to a reader when it only contains a
    # percentage. Prefer the alternate source when it has an actual locator;
    # otherwise KOReader receives a format-only position and cannot offer its
    # jump dialog.
    def has_locator(candidate: KoreaderProgressPayload | object | None) -> bool:
        if candidate is None:
            return False
        if isinstance(candidate, KoreaderProgressPayload):
            return bool(
                candidate.currentPage
                or candidate.progress
                or candidate.location
                or candidate.xpointer
                or candidate.cfi
            )
        return bool(
            getattr(candidate, "current_page", None)
            or getattr(candidate, "native_location", None)
            or getattr(candidate, "xpointer", None)
            or getattr(candidate, "cfi", None)
        )

    alternate = native_snapshot if preferred is progress else progress
    if not has_locator(preferred) and has_locator(alternate):
        preferred = alternate
    assert preferred is not None
    fmt = (
        (progress.fileFormat or progress.format) if progress else None
    ) or (native_snapshot.format if native_snapshot else None)
    if not fmt and native_snapshot is not None and native_snapshot.xpointer:
        fmt = "EPUB"
    if not fmt:
        raise HTTPException(status_code=501, detail="A verified file format is required for this progress position.")
    fmt = fmt.upper()
    cfi = preferred.cfi
    xpointer = preferred.xpointer
    page = preferred.currentPage if isinstance(preferred, KoreaderProgressPayload) else preferred.current_page
    total_pages = preferred.totalPages if isinstance(preferred, KoreaderProgressPayload) else preferred.total_pages
    href = preferred.href
    percentage = preferred.percentage if isinstance(preferred, KoreaderProgressPayload) else preferred.display_percent
    updated_at = preferred.updatedAt if isinstance(preferred, KoreaderProgressPayload) else preferred.updated_at
    device = (
        preferred.device if isinstance(preferred, KoreaderProgressPayload)
        else getattr(preferred, "device", None)
    )
    device_id = (
        preferred.device_id if isinstance(preferred, KoreaderProgressPayload)
        else getattr(preferred, "device_id", None)
    )
    if fmt in {"PDF", "CBZ", "CBR", "DJVU"} and page is None:
        page_location = preferred.progress if isinstance(preferred, KoreaderProgressPayload) else preferred.native_location
        if page_location and str(page_location).isdigit() and int(page_location) >= 1:
            page = int(page_location)
    if fmt == "EPUB":
        if reader == "koreader" and not xpointer and cfi:
            xpointer = await prose_converter.convert(book_hash, "cfi", cfi, creds)
        elif reader == "readest" and not cfi and xpointer:
            cfi = await prose_converter.convert(book_hash, "xpointer", xpointer, creds)
        if (reader == "koreader" and not xpointer) or (reader == "readest" and not cfi):
            raise HTTPException(
                status_code=424,
                detail="The newest EPUB position could not be converted for this reader; an older page was not returned.",
            )
    position = V2ProgressPosition(
        format=fmt, cfi=cfi, xpointer=xpointer, href=href,
        page=page, totalPages=total_pages,
    )
    return {
        "status": "ok",
        "bookHash": book_hash,
        "fraction": (percentage or 0.0) / 100.0,
        "updatedAt": updated_at,
        "position": position.model_dump(exclude_none=True),
        "reader": reader,
        "source": "book-api" if isinstance(preferred, KoreaderProgressPayload) else "koreader-native",
        "device": device,
        "deviceId": device_id,
    }


def _progress_time(value: str | datetime | None) -> int | None:
    if isinstance(value, datetime):
        return int((value if value.tzinfo else value.replace(tzinfo=UTC)).timestamp())
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp())
        except ValueError:
            return None
    return None


@router.put("/progress/{book_hash}")
async def put_v2_progress(
    book_hash: str,
    request: V2ProgressRequest,
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> dict[str, object]:
    position = request.position
    fmt = position.format.upper()
    conversion = "not-needed"
    if fmt == "EPUB":
        if request.source == "koreader":
            if not position.xpointer:
                raise HTTPException(status_code=422, detail="KOReader EPUB progress requires an XPointer.")
            cfi = await prose_converter.convert(book_hash, "xpointer", position.xpointer, creds)
            if not cfi:
                raise HTTPException(
                    status_code=424,
                    detail="Verified EPUB CFI conversion is required for Grimmory progress and Continue Reading.",
                )
            position = position.model_copy(update={"cfi": cfi})
            conversion = "synced"
        else:
            if not position.cfi:
                raise HTTPException(status_code=422, detail="Readest EPUB progress requires a CFI.")
            xpointer = await prose_converter.convert(book_hash, "cfi", position.cfi, creds)
            position = position.model_copy(update={"xpointer": xpointer})
            conversion = "synced" if xpointer else "failed"
    location = position.xpointer or position.cfi
    fixed_page = fmt in {"PDF", "CBZ", "CBR", "DJVU"}
    if position.page is not None and fixed_page:
        location = str(position.page)
    payload = KoreaderProgressPayload(
        document=book_hash,
        bookHash=book_hash,
        fileFormat=fmt,
        percentage=request.fraction * 100.0,
        progress=location,
        location=location,
        currentPage=position.page if fixed_page else None,
        totalPages=position.totalPages if fixed_page else None,
        cfi=position.cfi,
        xpointer=position.xpointer,
        href=position.href,
        timestamp=int(request.updatedAt.replace(tzinfo=UTC).timestamp())
        if request.updatedAt.tzinfo is None else int(request.updatedAt.timestamp()),
        updatedAt=request.updatedAt.isoformat().replace("+00:00", "Z"),
        device=request.device.name if request.device else request.source,
        device_id=request.device.id if request.device else None,
        source=request.source,
        expectedUpdatedAt=request.expectedUpdatedAt,
        force=request.force,
    )
    result = await _progress_service.update_progress(payload, creds)
    projection = str(result.get("projection") or "")
    if "nativeSync" in result:
        native_reason = str(result["nativeSync"])
    elif "koreader-native" in projection:
        native_reason = "synced"
    else:
        native_reason = "not-requested"
    native_status = (
        native_reason if native_reason in {"synced", "failed", "unsupported"}
        else "skipped"
    )
    readest_written = "official-book-api" in projection
    has_readest_position = bool(position.cfi or (fixed_page and position.page is not None))
    read_status = result.get("readStatus")
    if not isinstance(read_status, dict):
        read_status = {
            "status": "unchanged" if result.get("conflictDetected") else "skipped",
            "value": None,
            "source": "manual-status" if result.get("conflictDetected") else "position",
        }
    if read_status.get("status") == "failed":
        raise HTTPException(
            status_code=502,
            detail="Progress was saved, but Grimmory reading status could not be confirmed. Retry the same position.",
        )
    return {
        **result,
        "operationId": str(request.operationId),
        "epubConversion": conversion,
        "nativeSync": {
            "status": native_status,
            "reader": "koreader",
            "detail": native_reason if native_status != "synced" else None,
        },
        "readestProjection": {
            "status": "synced" if readest_written and has_readest_position else "skipped",
            "locator": "cfi" if position.cfi else "page" if position.page is not None else None,
        },
        "readStatus": read_status,
    }


@router.get("/books/read-statuses")
async def v2_read_statuses(
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> dict[str, list[str]]:
    return await get_supported_read_statuses(creds)


@router.put("/books/{book_id}/status", response_model=GrimmlinkReadStatusResponse)
async def v2_update_read_status(
    book_id: int,
    request: GrimmlinkReadStatusRequest,
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> GrimmlinkReadStatusResponse:
    return await update_read_status(book_id, request, creds)


@router.get("/books/{book_id}/metadata")
async def v2_pull_metadata(
    book_id: int,
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> object:
    bearer = await get_official_bearer(creds)
    client = OfficialGrimmoryClient()
    try:
        book = await client.get_book_by_id(book_id, bearer)
        owner_key = (
            f"user:{getattr(bearer, 'user_id', '')}"
            if creds.bearer_token else ProgressService._owner_key(creds)
        )
        cached_status = await ProgressStateCache.get_manual_status_for_book(owner_key, book_id)
        raw_status_time = book.get("readStatusModifiedTime") or book.get("updatedAt")
        if not raw_status_time and cached_status:
            raw_status_time = datetime.fromtimestamp(
                int(cached_status["updatedAt"]), UTC,
            ).isoformat().replace("+00:00", "Z")
        return {
            "bookId": book_id,
            "metadata": book.get("metadata"),
            "readStatus": book.get("readStatus"),
            "readStatusModifiedTime": raw_status_time,
        }
    finally:
        await client.aclose()


@router.put("/books/{book_id}/metadata")
async def v2_sync_metadata(
    book_id: int,
    request: V2BookMetadataUpdate,
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> object:
    bearer = await get_official_bearer(creds)
    client = OfficialGrimmoryClient()
    try:
        result = await client.update_book_metadata(
            book_id,
            bearer,
            {"metadata": request.metadata},
            merge_categories=request.mergeCategories,
            replace_mode=request.replaceMode,
        )
        return {**result, "operationId": str(request.operationId)}
    finally:
        await client.aclose()


_METADATA_SUFFIXES = (".opf", ".metadata.json")
_BOOK_SUFFIXES = (".epub", ".pdf", ".cbz", ".cbr", ".djvu", ".fb2", ".mobi", ".azw3")


def _configured_root(index: int) -> Path:
    roots = settings.METADATA_IMPORT_ROOTS
    if index < 0 or index >= len(roots):
        raise HTTPException(status_code=404, detail="Configured import root was not found.")
    try:
        root = Path(roots[index]).expanduser().resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=422, detail="Configured import root is unavailable.") from exc
    if not root.is_dir():
        raise HTTPException(status_code=422, detail="Configured import root is not a directory.")
    return root


def _safe_rooted_file(root: Path, relative_path: str) -> Path:
    candidate_path = Path(relative_path)
    if candidate_path.is_absolute() or ".." in candidate_path.parts:
        raise HTTPException(status_code=400, detail="Path must stay inside its configured import root.")
    try:
        resolved = (root / candidate_path).resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Selected import file was not found.") from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise HTTPException(status_code=400, detail="Selected file is outside its configured import root.")
    return resolved


def _source_books(source: Path) -> list[Path]:
    if source.name.lower().endswith(".metadata.json"):
        base = source.with_name(source.name[:-len(".metadata.json")])
        if base.is_file() and base.suffix.lower() in _BOOK_SUFFIXES:
            return [base]
        return sorted(path for path in source.parent.iterdir() if path.is_file() and path.suffix.lower() in _BOOK_SUFFIXES and path.stem == base.name)
    if source.name.lower() == "metadata.opf":
        return sorted(
            path for path in source.parent.iterdir()
            if path.is_file() and path.suffix.lower() in _BOOK_SUFFIXES
        )
    return sorted(
        path for path in source.parent.iterdir()
        if path.is_file() and path.suffix.lower() in _BOOK_SUFFIXES and path.stem == source.stem
    )


@router.get("/local/metadata", include_in_schema=False)
async def local_metadata_ui() -> FileResponse:
    page = Path(__file__).resolve().parent.parent / "static" / "metadata.html"
    return FileResponse(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


@router.get("/local/diagnostics")
async def local_diagnostics(
    request: Request,
    response: Response,
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> dict[str, Any]:
    """Return a redacted system report; it never includes stored secrets."""
    del creds
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    advertiser = getattr(request.app.state, "mdns_advertiser", None)
    mdns_status = advertiser.status() if advertiser is not None else None
    return await collect_diagnostics(mdns_status)


async def _library_metadata_folders(
    library_id: int, creds: ClientCredentials, session: tuple[str, AdapterSession],
) -> dict[int, set[Path]]:
    async with OfficialGrimmoryClient() as client:
        library = await client.get_library(library_id, creds.bearer_token or "")
    rows = await MetadataAutomationStore.query(
        "SELECT config FROM metadata_libraries WHERE server=? AND library_id=? AND owner_id=?",
        (server_key(), library_id, session[1].user_id),
    )
    if not rows:
        raise HTTPException(422, "Configure the selected Library's NAS folders before scanning")
    policy = LibraryPolicy.model_validate(json.loads(rows[0]["config"]))
    path_ids = {path["id"] for path in library.get("paths", [])}
    if any(mapping.libraryPathId not in path_ids for mapping in policy.mappings):
        raise HTTPException(422, "Library paths changed; update the NAS folder mapping")
    try:
        roots = check_storage(policy)
    except (OSError, MetadataBlocked) as exc:
        raise HTTPException(422, str(exc)) from exc
    folders: dict[int, set[Path]] = {}
    for mapping in policy.mappings:
        folders.setdefault(mapping.rootIndex, set()).add(mapped_root(roots[mapping.rootIndex], mapping.localSubpath))
    return folders


@router.get("/local/metadata/sources")
async def list_metadata_sources(
    library_id: int = Query(gt=0),
    creds: ClientCredentials = Depends(require_v2_credentials),
    session: tuple[str, AdapterSession] = Depends(get_adapter_session),
) -> list[dict[str, Any]]:
    folders = await _library_metadata_folders(library_id, creds, session)
    result: list[dict[str, Any]] = []
    for root_index, scoped_folders in folders.items():
        try:
            root = _configured_root(root_index)
            for folder in scoped_folders:
                for source in folder.rglob("*"):
                    if not source.is_file() or source.is_symlink():
                        continue
                    resolved = source.resolve(strict=True)
                    if not resolved.is_relative_to(folder) or not resolved.name.lower().endswith(_METADATA_SUFFIXES):
                        continue
                    if resolved.stat().st_size > settings.LOCAL_METADATA_MAX_SOURCE_BYTES:
                        continue
                    books = [book for book in _source_books(resolved) if book.resolve(strict=True).is_relative_to(folder)]
                    result.append({
                        "rootIndex": root_index,
                        "rootName": folder.name,
                        "sourcePath": resolved.relative_to(root).as_posix(),
                        "sourceType": "sidecar" if resolved.name.lower().endswith(".metadata.json") else "opf",
                        "bookCandidates": [book.relative_to(root).as_posix() for book in books],
                    })
        except OSError:
            continue
    return result


def _ingestion_result_json(result: Any, root: Path) -> dict[str, Any]:
    payload = cast(dict[str, Any], result.model_dump(mode="json"))
    preview = payload.get("preview")
    if isinstance(preview, dict):
        for key in ("opf_source", "book_target", "cover_source"):
            value = preview.get(key)
            if value:
                try:
                    preview[key] = Path(value).resolve(strict=False).relative_to(root).as_posix()
                except ValueError:
                    preview[key] = None
    return payload


async def _run_local_metadata(
    request: V2LocalMetadataRequest,
    creds: ClientCredentials,
    session: tuple[str, AdapterSession],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    folders = await _library_metadata_folders(request.libraryId, creds, session)
    root = _configured_root(request.rootIndex)
    source = _safe_rooted_file(root, request.sourcePath)
    book = _safe_rooted_file(root, request.bookPath)
    if not any(source.is_relative_to(folder) and book.is_relative_to(folder) for folder in folders.get(request.rootIndex, set())):
        raise HTTPException(status_code=400, detail="Selected files are outside this Library's NAS folders")
    if not source.name.lower().endswith(_METADATA_SUFFIXES):
        raise HTTPException(status_code=400, detail="Selected metadata file must be OPF or metadata JSON.")
    if book.suffix.lower() not in _BOOK_SUFFIXES:
        raise HTTPException(status_code=400, detail="Selected target must be a supported book file.")
    if source.parent != book.parent:
        raise HTTPException(status_code=400, detail="Metadata and target book must be in the same folder.")
    bearer = await get_official_bearer(creds)
    service = OPFIngestionService()
    try:
        async with metadata_execution_lock:
            result = await service.ingest(
                book_path=book,
                opf_path=source,
                bearer_token=bearer,
                dry_run=dry_run,
            )
    except OPFAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OPFIdentityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OPFFieldLockError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OPFSourceMutationProtectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OPFSourceMutationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OPFIngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await service.official_client.aclose()
    payload = _ingestion_result_json(result, root)
    payload["operationId"] = str(request.operationId)
    return payload


@router.post("/local/metadata/preview")
async def preview_local_metadata(
    request: V2LocalMetadataRequest,
    creds: ClientCredentials = Depends(require_v2_credentials),
    session: tuple[str, AdapterSession] = Depends(get_adapter_session),
) -> dict[str, Any]:
    return await _run_local_metadata(request, creds, session, dry_run=True)


@router.post("/local/metadata/apply")
async def apply_local_metadata(
    request: V2LocalMetadataRequest,
    creds: ClientCredentials = Depends(require_v2_credentials),
    session: tuple[str, AdapterSession] = Depends(get_adapter_session),
) -> dict[str, Any]:
    return await _run_local_metadata(request, creds, session, dry_run=False)
