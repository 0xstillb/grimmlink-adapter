"""Shelf synchronization and removal management service."""

import hashlib
import json
import logging
import uuid
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.grimmlink import (
    GrimmlinkBookSummary,
    GrimmlinkShelfRemovalResponse,
    GrimmlinkShelfSummary,
)
from grimmlink_adapter.models.internal import ManagedFileRecord
from grimmlink_adapter.models.official import OfficialAppBookSummaryDTO, OfficialBookDTO
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import (
    OfficialAuthError,
    OfficialBadResponseError,
    OfficialClientError,
    OfficialPermissionError,
    OfficialTimeoutError,
    OfficialTransportError,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.request_auth import get_official_bearer
from grimmlink_adapter.state.cache import ManagedFileCache, ShelfOwnershipCache
from grimmlink_adapter.state.outbox import IdempotencyManager, OutboxManager

logger = logging.getLogger(__name__)


def _required_int(item: dict[str, Any], key: str) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise OfficialBadResponseError(f"Official shelf is missing a valid {key}")
    return value


def _required_str(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OfficialBadResponseError(f"Official shelf is missing a valid {key}")
    return value


def _optional_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise OfficialBadResponseError("Official shelf contains an invalid bookCount")
    return value


def _file_extension(filename: str | None) -> str | None:
    if filename is None:
        return None
    dot = filename.rfind(".")
    return filename[dot + 1:].lower() if 0 <= dot < len(filename) - 1 else None


def _normalize_book(value: dict[str, Any], shelf_type: str) -> GrimmlinkBookSummary:
    if shelf_type == "magic":
        try:
            app_book = OfficialAppBookSummaryDTO.model_validate(value)
        except Exception as exc:
            raise OfficialBadResponseError("Official magic shelf contains a malformed book") from exc
        file_format = app_book.primaryFileType
        if file_format:
            file_format = file_format.lstrip(".").upper()
        size_kb = app_book.fileSizeKb
        return GrimmlinkBookSummary(
            bookId=app_book.id,
            bookFileId=app_book.primaryFileId,
            title=app_book.title,
            author=", ".join(app_book.authors) if app_book.authors else None,
            fileName=app_book.primaryFileName,
            originalFileName=app_book.primaryFileName,
            extension=_file_extension(app_book.primaryFileName),
            fileFormat=file_format,
            fileSizeKb=size_kb,
            fileSize=size_kb * 1024 if size_kb is not None else None,
            bookHash=None,
            seriesName=app_book.seriesName,
            seriesNumber=app_book.seriesNumber,
        )
    try:
        book = OfficialBookDTO.model_validate(value)
    except Exception as exc:
        raise OfficialBadResponseError("Official shelf contains a malformed book") from exc
    primary = book.primaryFile
    metadata = book.metadata or {}
    authors = metadata.get("authors") or metadata.get("author") or []
    if isinstance(authors, str):
        author: str | None = authors
    elif isinstance(authors, list):
        author = ", ".join(
            item if isinstance(item, str) else str(item.get("name", ""))
            for item in authors if isinstance(item, (str, dict)) and (isinstance(item, str) or item.get("name"))
        ) or None
    else:
        author = None
    series_name = metadata.get("seriesName")
    series_number: float | None = None
    raw_number = metadata.get("seriesNumber")
    with suppress(TypeError, ValueError):
        series_number = float(raw_number) if raw_number is not None else None
    file_format = (primary.bookType or primary.extension) if primary else None
    if file_format:
        file_format = file_format.lstrip(".").upper()
    size_kb = primary.fileSizeKb if primary else None
    filename = primary.fileName if primary else None
    return GrimmlinkBookSummary(
        bookId=book.id,
        bookFileId=primary.id if primary else None,
        title=book.title or metadata.get("title"),
        author=author,
        fileName=filename,
        originalFileName=filename,
        extension=_file_extension(filename),
        fileFormat=file_format,
        fileSizeKb=size_kb,
        fileSize=size_kb * 1024 if size_kb is not None else None,
        bookHash=None,
        seriesName=series_name,
        seriesNumber=series_number,
    )


def _dedupe_books(books: Iterable[GrimmlinkBookSummary]) -> list[GrimmlinkBookSummary]:
    result: list[GrimmlinkBookSummary] = []
    seen: set[tuple[int, int | None]] = set()
    for normalized in books:
        key = (normalized.bookId, normalized.bookFileId)
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def _upstream_error(exc: OfficialBadResponseError, message: str) -> HTTPException:
    code = 404 if exc.status_code == 404 else 502
    return HTTPException(status_code=code, detail=message)


class ShelfService:
    """Manages shelf aggregation and book removals with multi-shelf safety."""

    def __init__(
        self,
        official_client: OfficialGrimmoryClient | None = None,
        *,
        cleanup_policy: bool | None = None,
        managed_file_root: Path | None = None,
    ) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()
        self.cleanup_policy = settings.SHELF_CLEANUP_ENABLED if cleanup_policy is None else cleanup_policy
        self.managed_file_root = managed_file_root or (
            Path(settings.SHELF_MANAGED_FILE_ROOT).resolve()
            if settings.SHELF_MANAGED_FILE_ROOT else None
        )

    @staticmethod
    def _owner_key(creds: ClientCredentials, bearer: str) -> str:
        """Derive a stable, non-secret state scope for the authenticated user."""
        verified_user_id = getattr(bearer, "user_id", None)
        if isinstance(verified_user_id, int):
            return f"user:{verified_user_id}"
        return f"token:{hashlib.sha256(bearer.encode()).hexdigest()[:24]}"

    async def list_shelves(
        self, shelf_type: str | None = None, creds: ClientCredentials | None = None,
    ) -> list[GrimmlinkShelfSummary]:
        """Return normalized regular and rule-derived magic shelf summaries."""
        normalized_type = shelf_type.lower() if shelf_type else None
        if normalized_type not in (None, "regular", "magic"):
            raise HTTPException(status_code=400, detail="Shelf type must be regular or magic.")
        request_creds = creds or ClientCredentials()
        bearer = await get_official_bearer(request_creds)
        result: list[GrimmlinkShelfSummary] = []
        try:
            if normalized_type in (None, "regular"):
                for item in await self.official_client.get_regular_shelves(bearer):
                    public = item.get("publicShelf")
                    result.append(GrimmlinkShelfSummary(
                        id=_required_int(item, "id"), name=_required_str(item, "name"), type="regular",
                        visibility=item.get("visibility") or ("public" if public is True else "personal"),
                        bookCount=_optional_int(item.get("bookCount"), 0),
                        description=item.get("description"),
                    ))
            if normalized_type in (None, "magic"):
                for item in await self.official_client.get_magic_shelves(bearer):
                    public = item.get("publicShelf", item.get("public"))
                    result.append(GrimmlinkShelfSummary(
                        id=_required_int(item, "id"), name=_required_str(item, "name"), type="magic",
                        visibility=item.get("visibility") or ("public" if public is True else "personal"),
                        bookCount=_optional_int(item.get("bookCount"), 0),
                        description=item.get("description") or item.get("rule"),
                    ))
        except OfficialAuthError as exc:
            raise HTTPException(status_code=401, detail="Upstream authentication failed.") from exc
        except OfficialPermissionError as exc:
            raise HTTPException(status_code=403, detail="Shelf access was denied by Official Grimmory.") from exc
        except OfficialBadResponseError as exc:
            raise _upstream_error(exc, "Official shelf response is unavailable or malformed.") from exc
        except OfficialClientError as exc:
            raise HTTPException(status_code=502, detail="Official shelf service is unavailable.") from exc
        return result

    async def list_shelf_books(
        self,
        shelf_type: str,
        shelf_id: int,
        creds: ClientCredentials | None = None,
        limit: int | None = None,
        offset: int | None = None,
        cursor: str | None = None,
    ) -> list[GrimmlinkBookSummary]:
        """Read a complete shelf snapshot before applying legacy pagination."""
        normalized_type = shelf_type.lower()
        if normalized_type not in ("regular", "magic"):
            raise HTTPException(status_code=400, detail="Shelf type must be regular or magic.")
        request_creds = creds or ClientCredentials()
        bearer = await get_official_bearer(request_creds)
        owner_key = self._owner_key(request_creds, bearer)
        try:
            if normalized_type == "regular":
                books = await self.official_client.get_shelf_books(shelf_id, bearer)
            else:
                books = await self._get_all_magic_books(shelf_id, bearer)
        except OfficialAuthError as exc:
            raise HTTPException(status_code=401, detail="Upstream authentication failed.") from exc
        except OfficialPermissionError as exc:
            raise HTTPException(status_code=403, detail="Shelf access was denied by Official Grimmory.") from exc
        except OfficialBadResponseError as exc:
            raise _upstream_error(exc, "Shelf books are unavailable or the snapshot is incomplete.") from exc
        except OfficialClientError as exc:
            raise HTTPException(status_code=502, detail="Shelf books are unavailable or the snapshot is incomplete.") from exc
        try:
            normalized = _dedupe_books(_normalize_book(item, normalized_type) for item in books)
        except OfficialBadResponseError as exc:
            raise HTTPException(
                status_code=502, detail="Shelf books are unavailable or malformed."
            ) from exc
        # Only complete upstream reads update the ownership cache. Pagination is
        # applied after this point and therefore cannot hide a referenced book.
        await ShelfOwnershipCache.replace_shelf_ownership(
            [book.bookId for book in normalized], shelf_id, normalized_type, owner_key,
        )
        start = 0
        if cursor and cursor.strip():
            try:
                cursor_book_id = int(cursor.strip())
            except ValueError:
                pass
            else:
                for index, book in enumerate(normalized):
                    if book.bookId == cursor_book_id:
                        start = index + 1
                        break
        if offset is not None and offset >= 0:
            start = offset
        page_limit = min(limit, 100) if limit is not None and limit > 0 else 100
        return normalized[start:start + page_limit]

    async def _get_all_magic_books(self, shelf_id: int, bearer: str) -> list[dict[str, Any]]:
        first = await self._scan_magic_books(shelf_id, bearer)
        second = await self._scan_magic_books(shelf_id, bearer)
        if self._magic_membership(first) != self._magic_membership(second):
            raise OfficialBadResponseError("Magic shelf membership changed during verification")
        return second

    @staticmethod
    def _magic_membership(books: list[dict[str, Any]]) -> dict[int, int | None]:
        try:
            summaries = [OfficialAppBookSummaryDTO.model_validate(book) for book in books]
        except Exception as exc:
            raise OfficialBadResponseError("Magic shelf contains a malformed book") from exc
        return {book.id: book.primaryFileId for book in summaries}

    async def _scan_magic_books(self, shelf_id: int, bearer: str) -> list[dict[str, Any]]:
        page = 0
        page_size = 50
        books: list[dict[str, Any]] = []
        seen_book_ids: set[int] = set()
        returned_size_seen: int | None = None
        total_pages: int | None = None
        total_elements: int | None = None
        while page < 10000:
            result = await self.official_client.get_magic_shelf_books(shelf_id, bearer, page, page_size)
            content = result.get("content")
            if not isinstance(content, list) or not all(isinstance(item, dict) for item in content):
                raise OfficialBadResponseError("Magic shelf page is missing content")
            returned_page = result.get("page")
            returned_size = result.get("size")
            returned_total_pages = result.get("totalPages")
            returned_total_elements = result.get("totalElements")
            has_next = result.get("hasNext")
            has_previous = result.get("hasPrevious")
            if (
                isinstance(returned_page, bool) or not isinstance(returned_page, int)
                or returned_page != page
                or isinstance(returned_size, bool) or not isinstance(returned_size, int)
                or returned_size != page_size
                or isinstance(returned_total_pages, bool) or not isinstance(returned_total_pages, int)
                or returned_total_pages < 0
                or isinstance(returned_total_elements, bool) or not isinstance(returned_total_elements, int)
                or returned_total_elements < 0
                or not isinstance(has_next, bool)
                or not isinstance(has_previous, bool)
            ):
                raise OfficialBadResponseError("Magic shelf page metadata is malformed")
            expected_total_pages = (
                (returned_total_elements + returned_size - 1) // returned_size
                if returned_total_elements
                else 0
            )
            if returned_total_pages != expected_total_pages or has_previous != (page > 0):
                raise OfficialBadResponseError("Magic shelf page metadata is inconsistent")
            if returned_size_seen is None:
                returned_size_seen = returned_size
            elif returned_size != returned_size_seen:
                raise OfficialBadResponseError("Magic shelf page size changed during the snapshot")
            if total_pages is None:
                total_pages = returned_total_pages
                total_elements = returned_total_elements
            elif returned_total_pages != total_pages or returned_total_elements != total_elements:
                raise OfficialBadResponseError("Magic shelf pagination totals changed during the snapshot")
            if returned_total_pages == 0:
                raise OfficialBadResponseError(
                    "Magic shelf returned no reliable completeness metadata for an empty snapshot"
                )
            if page >= returned_total_pages or has_next != (page + 1 < returned_total_pages):
                raise OfficialBadResponseError("Magic shelf page completion metadata is inconsistent")
            expected_content_size = (
                returned_size
                if has_next
                else returned_total_elements - (page * returned_size)
            )
            if expected_content_size < 0 or len(content) != expected_content_size:
                raise OfficialBadResponseError("Magic shelf page content is incomplete")
            for item in content:
                book_id = item.get("id")
                if isinstance(book_id, bool) or not isinstance(book_id, int):
                    raise OfficialBadResponseError("Magic shelf book is missing a valid id")
                if book_id in seen_book_ids:
                    raise OfficialBadResponseError("Magic shelf snapshot contains duplicate book ids")
                seen_book_ids.add(book_id)
            books.extend(content)
            if not has_next:
                if total_elements != len(books):
                    raise OfficialBadResponseError("Magic shelf snapshot ended before totalElements")
                return books
            page += 1
        raise OfficialBadResponseError("Magic shelf pagination exceeded the safety limit")

    async def remove_book_from_shelf(
        self,
        shelf_type: str,
        shelf_id: int,
        book_id: int,
        creds: ClientCredentials | None = None,
    ) -> GrimmlinkShelfRemovalResponse:
        """Unassign a regular shelf membership, then finalize local state safely."""
        normalized_type = shelf_type.lower()
        if normalized_type == "magic":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Manual removal from a magic shelf is not supported because magic shelves are rule-derived.",
            )
        if normalized_type != "regular":
            raise HTTPException(status_code=400, detail="Shelf type must be regular or magic.")

        request_creds = creds or ClientCredentials()
        bearer = await get_official_bearer(request_creds)
        owner_key = self._owner_key(request_creds, bearer)
        base_idempotency_key = f"SHELF_UNASSIGN:{owner_key}:{shelf_id}:{book_id}"
        idempotency_key = base_idempotency_key
        action = await OutboxManager.get_by_idempotency_key(base_idempotency_key)
        if action is not None and action.status == "FAILED":
            await OutboxManager.requeue(action.id)
        elif action is not None and action.status == "COMPLETED":
            # A completed remove must not suppress a later remove after the
            # book has been re-added to the shelf.
            idempotency_key = f"{base_idempotency_key}:{uuid.uuid4().hex}"
            action = await OutboxManager.get_by_idempotency_key(idempotency_key)
        if action is None:
            action_id = await OutboxManager.enqueue(
                "SHELF_UNASSIGN",
                {
                    "book_ids": [book_id],
                    "shelves_to_assign": [],
                    "shelves_to_unassign": [shelf_id],
                    "shelf_type": normalized_type,
                    "shelf_id": shelf_id,
                    "book_id": book_id,
                },
                idempotency_key=idempotency_key,
            )
        else:
            action_id = action.id

        try:
            await self.official_client.assign_shelves_to_books(
                [book_id], [], [shelf_id], bearer,
            )
        except OfficialClientError as exc:
            await OutboxManager.mark_failed(action_id, str(exc))
            if isinstance(exc, OfficialAuthError):
                raise HTTPException(status_code=401, detail="Upstream authentication failed.") from exc
            if isinstance(exc, OfficialPermissionError):
                raise HTTPException(status_code=403, detail="Shelf mutation was denied by Official Grimmory.") from exc
            if isinstance(exc, (OfficialTimeoutError, OfficialTransportError)):
                raise HTTPException(status_code=502, detail="Official shelf mutation timed out or is unavailable.") from exc
            if isinstance(exc, OfficialBadResponseError):
                raise _upstream_error(exc, "Official shelf mutation failed.") from exc
            raise HTTPException(status_code=502, detail="Official shelf mutation failed.") from exc

        response = GrimmlinkShelfRemovalResponse(
            shelfId=shelf_id,
            bookId=book_id,
            shelfType=normalized_type,
            removed=True,
            status="removed",
            message="Shelf membership removed",
        )
        # The remote mutation succeeded. Local ownership is finalized only now.
        await ShelfOwnershipCache.remove_ownership(book_id, shelf_id, normalized_type, owner_key)
        if self.cleanup_policy:
            cleanup_verified = await self._refresh_complete_ownership_snapshot(bearer, owner_key)
            if cleanup_verified:
                deleted = await self._delete_managed_copy_if_safe(book_id, owner_key)
                response.message = (
                    "Shelf membership removed; managed local copy deleted"
                    if deleted else "Shelf membership removed; local copy retained"
                )
            else:
                response.message = "Shelf membership removed; local cleanup skipped because snapshot was incomplete"
        # Persist completion only after local ownership finalization. If the
        # process dies earlier, a retry can safely replay the idempotent remote
        # unassign and finish the local step instead of returning early.
        await IdempotencyManager.record_response(
            idempotency_key,
            "SHELF_UNASSIGN",
            200,
            response.model_dump_json(),
        )
        await OutboxManager.mark_completed(action_id)
        return response

    async def register_managed_file(
        self,
        book_id: int,
        book_file_id: int,
        tracked_path: Path,
        *,
        expected_size: int | None = None,
        provider_reference_count: int = 0,
        owner_key: str = "default",
    ) -> None:
        """Register a file only after a local download pipeline created it.

        The streaming download route does not call this method because it has no
        local destination. A future managed-copy pipeline must provide an exact
        path inside the configured cleanup root after the file is written.
        """
        if self.managed_file_root is None:
            raise HTTPException(status_code=501, detail="Managed file cleanup root is not configured.")
        root = self.managed_file_root.resolve()
        try:
            resolved = tracked_path.resolve(strict=True)
            actual_size = resolved.stat().st_size
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail="Managed file path is invalid.") from exc
        if resolved != root and root not in resolved.parents:
            raise HTTPException(status_code=400, detail="Managed file path is outside the cleanup root.")
        if expected_size is not None and expected_size != actual_size:
            raise HTTPException(status_code=400, detail="Managed file size does not match the written file.")
        await ManagedFileCache.put(ManagedFileRecord(
            book_id=book_id,
            book_file_id=book_file_id,
            owner_key=owner_key,
            tracked_path=str(resolved),
            downloaded_by_grimmlink=True,
            provider_reference_count=provider_reference_count,
            expected_size=actual_size,
        ))

    @staticmethod
    def _removal_response_from_recorded(body: str, shelf_id: int, book_id: int) -> GrimmlinkShelfRemovalResponse:
        try:
            value = json.loads(body)
            if isinstance(value, dict):
                return GrimmlinkShelfRemovalResponse.model_validate(value)
        except (TypeError, ValueError):
            pass
        return GrimmlinkShelfRemovalResponse(
            shelfId=shelf_id, bookId=book_id, shelfType="regular", removed=True,
            status="removed", message="Shelf membership already removed",
        )

    async def _refresh_complete_ownership_snapshot(self, bearer: str, owner_key: str) -> bool:
        """Refresh all shelf ownership only when every upstream read completes."""
        try:
            snapshot: list[tuple[int, str, list[int]]] = []
            regular_shelves = await self.official_client.get_regular_shelves(bearer)
            if not isinstance(regular_shelves, list):
                return False
            for shelf in regular_shelves:
                if not isinstance(shelf, dict) or not isinstance(shelf.get("id"), int):
                    return False
                books = await self.official_client.get_shelf_books(shelf["id"], bearer)
                if not isinstance(books, list) or not all(isinstance(item, dict) for item in books):
                    return False
                ids: list[int] = []
                for item in books:
                    value = item.get("id")
                    if isinstance(value, bool) or not isinstance(value, int):
                        return False
                    ids.append(value)
                snapshot.append((shelf["id"], "regular", ids))

            magic_shelves = await self.official_client.get_magic_shelves(bearer)
            if not isinstance(magic_shelves, list):
                return False
            for shelf in magic_shelves:
                if not isinstance(shelf, dict) or not isinstance(shelf.get("id"), int):
                    return False
                books = await self._get_all_magic_books(shelf["id"], bearer)
                snapshot.append((shelf["id"], "magic", [item["id"] for item in books]))
            await ShelfOwnershipCache.replace_snapshot(snapshot, owner_key)
            return True
        except OfficialClientError:
            return False
        except (TypeError, KeyError, ValueError):
            return False

    async def _delete_managed_copy_if_safe(self, book_id: int, owner_key: str) -> bool:
        """Delete only an exact adapter-owned path with no remaining references."""
        if self.managed_file_root is None:
            return False
        if await ShelfOwnershipCache.is_tracked_by_any_owner(book_id, -1, "none", owner_key):
            return False
        records = await ManagedFileCache.get_all(book_id, owner_key)
        if len(records) != 1:
            return False
        record = records[0]
        if (
            not record.downloaded_by_grimmlink
            or record.provider_reference_count > 0
            or record.expected_size is None
        ):
            return False
        tracked = Path(record.tracked_path)
        try:
            resolved = tracked.resolve(strict=True)
            root = self.managed_file_root.resolve()
            if resolved != root and root not in resolved.parents:
                return False
            current = await ManagedFileCache.get(book_id, record.book_file_id, owner_key)
            if current is None or current.model_dump() != record.model_dump():
                return False
            if record.expected_size is not None and resolved.stat().st_size != record.expected_size:
                return False
            if await ManagedFileCache.has_provider_reference_for_path(str(resolved)):
                return False
            tracked.unlink()
        except (FileNotFoundError, OSError, RuntimeError):
            return False
        await ManagedFileCache.remove(record.book_id, record.book_file_id, owner_key)
        return True
