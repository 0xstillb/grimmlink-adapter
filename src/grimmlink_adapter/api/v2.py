"""Versioned v2 routes and aliases for the GrimmLink client contract."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse

from grimmlink_adapter.api.books import download_book, get_book_by_hash
from grimmlink_adapter.config import settings
from grimmlink_adapter.models.grimmlink import (
    GrimmlinkBookSummary,
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
from grimmlink_adapter.security.auth_extractor import (
    ClientCredentials,
    get_adapter_session,
    require_v2_credentials,
)
from grimmlink_adapter.services.auth_service import AuthService
from grimmlink_adapter.services.diagnostics import collect_diagnostics
from grimmlink_adapter.services.metadata_automation import metadata_execution_lock
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

router = APIRouter(tags=["GrimmLink v2"])
_progress_service = ProgressService()
_shelf_service = ShelfService()


async def _issue_session(username: str, md5_key: str) -> V2SessionResponse:
    creds = ClientCredentials(username=username, md5_key=md5_key)
    auth = AuthService()
    try:
        identity = await auth.authorize_client(creds)
        bearer = await get_official_bearer(creds)
        if getattr(bearer, "user_id", None) != identity.userId:
            raise HTTPException(status_code=401, detail="Linked Grimmory identity does not match KOSync user.")
        token, session = await AdapterSessionStore.create(username, md5_key, identity.userId)
    finally:
        await auth.official_client.aclose()
    return V2SessionResponse(
        accessToken=token,
        expiresAt=session.expires_at,
        username=identity.username,
        userId=identity.userId,
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
        username, user_id, login.token, login.refreshToken,
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
            position = position.model_copy(update={"cfi": cfi})
            conversion = "synced" if cfi else "failed"
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


@router.get("/books/{book_id}/metadata")
async def v2_pull_metadata(
    book_id: int,
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> object:
    bearer = await get_official_bearer(creds)
    client = OfficialGrimmoryClient()
    try:
        book = await client.get_book_by_id(book_id, bearer)
        return {"bookId": book_id, "metadata": book.get("metadata"), "readStatus": book.get("readStatus")}
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
        path for path in source.parent.glob(f"{source.stem}.*")
        if path.is_file() and path.suffix.lower() in _BOOK_SUFFIXES
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


@router.get("/local/metadata/sources")
async def list_metadata_sources(
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> list[dict[str, Any]]:
    del creds  # Dependency authenticates the request; the scan is confined to configured roots.
    result: list[dict[str, Any]] = []
    for root_index in range(len(settings.METADATA_IMPORT_ROOTS)):
        try:
            root = _configured_root(root_index)
            for source in root.rglob("*"):
                if not source.is_file() or source.is_symlink():
                    continue
                resolved = source.resolve(strict=True)
                if not resolved.is_relative_to(root) or not resolved.name.lower().endswith(_METADATA_SUFFIXES):
                    continue
                if resolved.stat().st_size > settings.LOCAL_METADATA_MAX_SOURCE_BYTES:
                    continue
                books = [book for book in _source_books(resolved) if book.resolve(strict=True).is_relative_to(root)]
                result.append({
                    "rootIndex": root_index,
                    "rootName": root.name,
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
    *,
    dry_run: bool,
) -> dict[str, Any]:
    root = _configured_root(request.rootIndex)
    source = _safe_rooted_file(root, request.sourcePath)
    book = _safe_rooted_file(root, request.bookPath)
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
) -> dict[str, Any]:
    return await _run_local_metadata(request, creds, dry_run=True)


@router.post("/local/metadata/apply")
async def apply_local_metadata(
    request: V2LocalMetadataRequest,
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> dict[str, Any]:
    return await _run_local_metadata(request, creds, dry_run=False)
