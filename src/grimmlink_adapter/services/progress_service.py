"""EPUB/PDF progress normalization and the Official KOReader bridge."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status

from grimmlink_adapter.models.grimmlink import KoreaderProgressPayload
from grimmlink_adapter.models.internal import ProgressSnapshot
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import (
    OfficialAuthError,
    OfficialClientError,
    OfficialPermissionError,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.request_auth import get_official_bearer
from grimmlink_adapter.state.book_identity import BookIdentityStore
from grimmlink_adapter.state.cache import ProgressStateCache

REFLOWABLE_FORMATS = frozenset({"EPUB", "MOBI", "AZW3", "FB2", "HTML"})
FIXED_PAGE_FORMATS = frozenset({"PDF", "CBZ", "CBR", "DJVU"})


def calculate_display_percentage(current_page: int, total_pages: int) -> float:
    """Return a UI percentage from page numbers, never a 0..1 fraction."""
    if total_pages <= 0 or current_page < 0:
        return 0.0
    pct = (current_page / total_pages) * 100.0
    return round(min(100.0, max(0.0, pct)), 4)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_grimmlink_percent(value: Any) -> float | None:
    """Normalize a GrimmLink wire percentage (already expressed as 0..100)."""
    number = _finite_number(value)
    if number is None:
        return None
    return round(min(100.0, max(0.0, number)), 4)


def official_fraction_to_display_percent(value: Any) -> float | None:
    """Convert Official's 0..1 fraction to the GrimmLink 0..100 display value.

    Values above one are accepted as legacy Official percent values so old
    rows can be read without turning 33% into 3300%.
    """
    number = _finite_number(value)
    if number is None:
        return None
    if 0.0 <= number <= 1.0:
        return round(number * 100.0, 4)
    return normalize_grimmlink_percent(number)


def grimmlink_percent_to_official_fraction(value: Any) -> float | None:
    """Convert a GrimmLink 0..100 display value to Official's 0..1 fraction."""
    display = normalize_grimmlink_percent(value)
    return round(display / 100.0, 8) if display is not None else None


def _parse_timestamp(value: Any) -> tuple[int | None, datetime | None]:
    if isinstance(value, bool) or value is None:
        return None, None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        epoch = int(value)
        return epoch, datetime.fromtimestamp(epoch, tz=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None, None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        parsed = parsed.astimezone(UTC)
        return int(parsed.timestamp()), parsed
    return None, None


def _native_location(progress: str | None, location: str | None, *, reflowable: bool) -> str | None:
    for candidate in (location, progress):
        if candidate is None or not str(candidate).strip():
            continue
        value = str(candidate).strip()
        if reflowable:
            try:
                float(value)
            except ValueError:
                return value
        else:
            return value
    return None


def _format(payload: dict[str, Any]) -> str | None:
    value = payload.get("fileFormat") or payload.get("format") or payload.get("bookType")
    return str(value).upper() if value is not None and str(value).strip() else None


def _page(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def snapshot_from_grimmlink(payload: KoreaderProgressPayload) -> ProgressSnapshot:
    """Normalize an incoming GrimmLink payload before talking to Official."""
    raw = payload.model_dump(by_alias=True, exclude_none=True)
    book_hash = payload.bookHash or payload.document
    if not book_hash or not book_hash.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bookHash or document is required")
    fmt = _format(raw)
    reflowable = (fmt or "").upper() in REFLOWABLE_FORMATS
    native = _native_location(payload.progress, payload.location, reflowable=reflowable)
    if reflowable and native is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="KOReader-native location is required for reflowable progress",
        )

    current_page = _page(payload.currentPage)
    total_pages = _page(payload.totalPages)
    if current_page is not None and total_pages is not None and total_pages > 0:
        display_percent: float | None = calculate_display_percentage(current_page, total_pages)
    else:
        display_percent = normalize_grimmlink_percent(payload.percentage)
        if display_percent is None and not reflowable and native is not None:
            try:
                page_from_progress = int(float(native))
            except ValueError:
                page_from_progress = None
            if page_from_progress is not None and total_pages and total_pages > 0:
                current_page = page_from_progress
                display_percent = calculate_display_percentage(current_page, total_pages)

    epoch, updated_at = _parse_timestamp(payload.updatedAt or payload.timestamp)
    return ProgressSnapshot(
        book_hash=book_hash.strip(),
        book_id=payload.bookId,
        book_file_id=payload.bookFileId,
        format=fmt,
        native_location=native,
        current_page=current_page,
        total_pages=total_pages,
        display_percent=display_percent,
        official_fraction=grimmlink_percent_to_official_fraction(display_percent),
        device=payload.device,
        device_id=payload.device_id,
        timestamp_epoch=epoch,
        updated_at=updated_at,
        source=payload.source or payload.device,
        expected_updated_at=payload.expectedUpdatedAt,
        force=payload.force is True,
    )


def snapshot_from_official(payload: dict[str, Any], requested_hash: str) -> ProgressSnapshot | None:
    """Normalize Official's KOReader response at the adapter boundary."""
    if not isinstance(payload, dict) or not payload:
        return None
    fmt = _format(payload)
    reflowable = (fmt or "").upper() in REFLOWABLE_FORMATS
    native = _native_location(
        payload.get("progress"), payload.get("location"), reflowable=reflowable,
    )
    raw_current_page = payload["currentPage"] if "currentPage" in payload else payload.get("current_page")
    raw_total_pages = payload["totalPages"] if "totalPages" in payload else payload.get("total_pages")
    current_page = _page(raw_current_page)
    total_pages = _page(raw_total_pages)
    if current_page is not None and total_pages is not None and total_pages > 0:
        display_percent: float | None = calculate_display_percentage(current_page, total_pages)
    else:
        display_percent = official_fraction_to_display_percent(payload.get("percentage"))
    epoch, updated_at = _parse_timestamp(payload.get("updatedAt") or payload.get("timestamp"))
    return ProgressSnapshot(
        book_hash=str(payload.get("bookHash") or payload.get("document") or requested_hash),
        book_id=payload.get("bookId"),
        book_file_id=payload.get("bookFileId"),
        format=fmt,
        native_location=native,
        current_page=current_page,
        total_pages=total_pages,
        display_percent=display_percent,
        official_fraction=grimmlink_percent_to_official_fraction(display_percent),
        device=payload.get("device"),
        device_id=payload.get("deviceId") or payload.get("device_id"),
        timestamp_epoch=epoch,
        updated_at=updated_at,
        source=payload.get("source") or payload.get("device"),
    )


def snapshot_to_grimmlink(snapshot: ProgressSnapshot) -> KoreaderProgressPayload:
    """Project a canonical snapshot onto the frozen GrimmLink wire DTO."""
    return KoreaderProgressPayload(
        timestamp=snapshot.timestamp_epoch,
        document=snapshot.book_hash,
        bookHash=snapshot.book_hash,
        bookId=snapshot.book_id,
        bookFileId=snapshot.book_file_id,
        fileFormat=snapshot.format,
        percentage=snapshot.display_percent,
        progress=snapshot.native_location,
        location=snapshot.native_location if snapshot.is_reflowable else None,
        currentPage=snapshot.current_page,
        totalPages=snapshot.total_pages,
        updatedAt=snapshot.updated_at.isoformat().replace("+00:00", "Z") if snapshot.updated_at else None,
        device=snapshot.device,
        device_id=snapshot.device_id,
        source=snapshot.source,
    )


class ProgressService:
    """Translate GrimmLink progress to and from Official's KOReader API."""

    def __init__(self, official_client: OfficialGrimmoryClient | None = None) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()

    @staticmethod
    def _owner_key(creds: ClientCredentials) -> str:
        if creds.username:
            return f"user:{creds.username}"
        token = creds.bearer_token or "anonymous"
        return f"token:{hashlib.sha256(token.encode()).hexdigest()[:24]}"

    @staticmethod
    def _require_md5(creds: ClientCredentials | None) -> tuple[str, str]:
        if creds is None or not creds.username or not creds.md5_key:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Progress bridge requires KOReader MD5 credentials for Official Grimmory.",
            )
        return creds.username, creds.md5_key

    @staticmethod
    async def _resolve_bearer_book(
        creds: ClientCredentials, book_hash: str, requested_book_id: int | None = None,
    ) -> tuple[str, int, int | None, str | None]:
        """Resolve a verified Bearer identity and its scoped hash mapping."""
        from grimmlink_adapter.config import settings

        bearer = await get_official_bearer(creds)
        user_id = str(getattr(bearer, "user_id", ""))
        if not user_id:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Bearer identity was not verified.")
        server = settings.GRIMMORY_BASE_URL.rstrip("/")
        rows = await BookIdentityStore.lookup_by_current_hash(server, user_id, book_hash)
        if not rows:
            rows = await BookIdentityStore.lookup_by_initial_hash(server, user_id, book_hash)
        if requested_book_id is not None:
            rows = [row for row in rows if row.book_id == requested_book_id]
        if len(rows) != 1:
            code = status.HTTP_409_CONFLICT if len(rows) > 1 else status.HTTP_501_NOT_IMPLEMENTED
            detail = "Book hash mapping is ambiguous." if len(rows) > 1 else "A verified hash-to-book mapping is required for Bearer progress."
            raise HTTPException(status_code=code, detail=detail)
        row = rows[0]
        return bearer, row.book_id, row.book_file_id or None, row.format

    @staticmethod
    def _reconcile_identity(
        snapshot: ProgressSnapshot, mapped_file_id: int | None, mapped_format: str | None,
    ) -> None:
        if mapped_file_id is not None and snapshot.book_file_id not in (None, mapped_file_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="bookFileId does not match the verified book mapping.")
        if mapped_format and snapshot.format and snapshot.format.upper() != mapped_format.upper():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="fileFormat does not match the verified book mapping.")
        snapshot.book_file_id = mapped_file_id or snapshot.book_file_id
        snapshot.format = (mapped_format or snapshot.format or "").upper() or None
        if snapshot.is_reflowable and snapshot.native_location is not None:
            try:
                float(snapshot.native_location)
            except ValueError:
                return
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Numeric-only location is invalid for reflowable progress.")

    @staticmethod
    def _app_progress_snapshot(
        payload: dict[str, Any], requested_hash: str, book_id: int, book_file_id: int | None,
        prior: ProgressSnapshot | None = None,
    ) -> ProgressSnapshot | None:
        """Project Official's app progress response into the canonical snapshot."""
        if not isinstance(payload, dict):
            return None
        raw: dict[str, Any] = {
            "bookHash": requested_hash, "bookId": book_id, "bookFileId": book_file_id,
            "updatedAt": payload.get("lastReadTime"),
        }
        pdf = payload.get("pdfProgress")
        epub = payload.get("epubProgress")
        if isinstance(pdf, dict) and pdf.get("page") is not None:
            raw.update({
                "fileFormat": "PDF", "progress": str(pdf.get("page")),
                "currentPage": pdf.get("page"), "percentage": pdf.get("percentage"),
            })
            if prior is not None and prior.total_pages is not None:
                raw["totalPages"] = prior.total_pages
        elif isinstance(epub, dict):
            raw.update({
                "fileFormat": "EPUB", "progress": epub.get("cfi"),
                "location": epub.get("cfi"), "percentage": epub.get("percentage"),
            })
        else:
            koreader = payload.get("koreaderProgress")
            if isinstance(koreader, dict):
                raw.update(koreader)
                raw["bookHash"] = requested_hash
                raw["bookId"] = book_id
                raw["bookFileId"] = book_file_id
        snapshot = snapshot_from_official(raw, requested_hash)
        if snapshot is not None:
            # App/WebUI DTO percentages are already 0..100 display values;
            # fraction conversion is reserved for the KOReader endpoint.
            if isinstance(pdf, dict) and pdf.get("percentage") is not None:
                snapshot.display_percent = normalize_grimmlink_percent(pdf.get("percentage"))
                snapshot.official_fraction = grimmlink_percent_to_official_fraction(snapshot.display_percent)
            elif isinstance(epub, dict) and epub.get("percentage") is not None:
                snapshot.display_percent = normalize_grimmlink_percent(epub.get("percentage"))
                snapshot.official_fraction = grimmlink_percent_to_official_fraction(snapshot.display_percent)
        return snapshot

    @staticmethod
    def _projection_payload(snapshot: ProgressSnapshot) -> dict[str, Any]:
        if snapshot.display_percent is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Progress percentage is required.")
        if snapshot.is_fixed_page:
            page = snapshot.current_page
            if page is None and snapshot.native_location is not None:
                page = _page(snapshot.native_location)
            if page is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PDF page is required.")
            return {"pdfProgress": {"page": page, "percentage": snapshot.display_percent}}
        if snapshot.is_reflowable:
            if snapshot.native_location is None or not snapshot.native_location.lower().startswith("epubcfi("):
                raise HTTPException(
                    status_code=status.HTTP_501_NOT_IMPLEMENTED,
                    detail="Official app projection requires a verified EPUB CFI; use KOReader native sync for XPointer locations.",
                )
            return {"epubProgress": {
                "cfi": snapshot.native_location,
                "href": None,
                "percentage": snapshot.display_percent,
            }}
        if snapshot.book_file_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bookFileId is required for this format.")
        return {"fileProgress": {
            "bookFileId": snapshot.book_file_id,
            "positionData": snapshot.native_location,
            "positionHref": None,
            "progressPercent": snapshot.display_percent,
        }}

    @staticmethod
    def _upstream_error(exc: OfficialClientError) -> HTTPException:
        if isinstance(exc, OfficialAuthError) or exc.status_code == 401:
            return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Upstream authentication failed.")
        if isinstance(exc, OfficialPermissionError) or exc.status_code == 403:
            return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Progress access was denied by Official Grimmory.")
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Official progress service is unavailable.")

    @staticmethod
    async def _verify_dual_credentials(creds: ClientCredentials) -> None:
        """Reject a request whose MD5 and Bearer identities differ."""
        if creds.bearer_token and creds.username and creds.md5_key:
            await get_official_bearer(creds)

    async def get_progress(
        self, book_hash: str, creds: ClientCredentials | None = None,
    ) -> KoreaderProgressPayload:
        assert creds is not None
        owner_key = self._owner_key(creds)
        if creds.bearer_token:
            await self._verify_dual_credentials(creds)
            bearer, book_id, book_file_id, _ = await self._resolve_bearer_book(creds, book_hash)
            owner_key = f"user:{getattr(bearer, 'user_id', '')}"
            prior = await ProgressStateCache.get(owner_key, book_hash)
            try:
                raw = await self.official_client.get_app_book_progress(book_id, bearer)
            except OfficialClientError as exc:
                raise self._upstream_error(exc) from exc
            snapshot = self._app_progress_snapshot(raw, book_hash, book_id, book_file_id, prior)
        else:
            username, md5_key = self._require_md5(creds)
            try:
                raw = await self.official_client.get_koreader_progress(book_hash, username, md5_key)
            except OfficialClientError as exc:
                raise self._upstream_error(exc) from exc
            snapshot = snapshot_from_official(raw, book_hash)
        if snapshot is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No progress found for book hash.")
        await ProgressStateCache.put(owner_key, snapshot)
        return snapshot_to_grimmlink(snapshot)

    async def update_progress(
        self, progress: KoreaderProgressPayload, creds: ClientCredentials | None = None,
    ) -> dict[str, Any]:
        assert creds is not None
        snapshot = snapshot_from_grimmlink(progress)
        md5_identity_required = not creds.bearer_token
        md5_current: ProgressSnapshot | None = None
        username: str | None = None
        md5_key: str | None = None
        if md5_identity_required:
            username, md5_key = self._require_md5(creds)
            try:
                md5_current = snapshot_from_official(
                    await self.official_client.get_koreader_progress(
                        snapshot.book_hash, username, md5_key,
                    ),
                    snapshot.book_hash,
                )
            except OfficialClientError as exc:
                raise self._upstream_error(exc) from exc
        verified_bearer: str | None = None
        resolved_bearer: str | None = None
        resolved_book_id: int | None = snapshot.book_id
        if creds.bearer_token:
            await self._verify_dual_credentials(creds)
            verified_bearer = await get_official_bearer(creds)
            owner_key = f"user:{getattr(verified_bearer, 'user_id', '')}"
            resolved_bearer, resolved_book_id, resolved_file_id, resolved_format = await self._resolve_bearer_book(
                creds, snapshot.book_hash, snapshot.book_id,
            )
            snapshot.book_id = resolved_book_id
            self._reconcile_identity(snapshot, resolved_file_id, resolved_format)
        else:
            owner_key = self._owner_key(creds)
            # A newer manual status must win without requiring a network lookup.
            if snapshot.is_fixed_page and snapshot.book_id is None and await ProgressStateCache.manual_status_is_newer(
                owner_key, snapshot.book_hash, snapshot.timestamp_epoch, snapshot.book_id,
            ):
                return {
                    "status": "conflict",
                    "updated": False,
                    "conflictDetected": True,
                    "message": "A newer manual read status is preserved over this progress update.",
                }
            # Fixed-page formats (especially PDF) must use the Official app
            # projection. KOReader may omit bookId while still providing a
            # verified bookHash; resolve that hash before requiring a numeric ID.
            if snapshot.book_id is not None or snapshot.format is None or snapshot.is_fixed_page:
                try:
                    _, resolved_book_id, resolved_file_id, resolved_format = await self._resolve_bearer_book(
                        creds, snapshot.book_hash, snapshot.book_id,
                    )
                    snapshot.book_id = resolved_book_id
                    self._reconcile_identity(snapshot, resolved_file_id, resolved_format)
                except HTTPException:
                    if snapshot.book_id is not None or snapshot.format is None or snapshot.is_fixed_page:
                        raise
        if await ProgressStateCache.manual_status_is_newer(
            owner_key, snapshot.book_hash, snapshot.timestamp_epoch, snapshot.book_id,
        ):
            return {
                "status": "conflict",
                "updated": False,
                "conflictDetected": True,
                "message": "A newer manual read status is preserved over this progress update.",
            }
        if creds.bearer_token:
            bearer = resolved_bearer or verified_bearer
            if bearer is None or snapshot.book_id is None:
                raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Bearer book mapping is required.")
            owner_key = f"user:{getattr(bearer, 'user_id', '')}"
            prior = await ProgressStateCache.get(owner_key, snapshot.book_hash)
            try:
                current_app = self._app_progress_snapshot(
                    await self.official_client.get_app_book_progress(snapshot.book_id, bearer),
                    snapshot.book_hash, snapshot.book_id, snapshot.book_file_id, prior,
                )
            except OfficialClientError as exc:
                if getattr(exc, "status_code", None) == 404:
                    current_app = None
                else:
                    raise self._upstream_error(exc) from exc
            conflict = self._timestamp_conflict(snapshot, current_app)
            if conflict is not None:
                return conflict
            try:
                await self.official_client.update_book_progress(
                    snapshot.book_id, self._projection_payload(snapshot), bearer,
                )
            except OfficialClientError as exc:
                raise self._upstream_error(exc) from exc
            await ProgressStateCache.put(owner_key, snapshot)
            return {"status": "progress updated", "projection": "official-app"}

        assert username is not None and md5_key is not None
        if snapshot.is_fixed_page:
            if snapshot.book_id is None:
                raise HTTPException(
                    status_code=status.HTTP_501_NOT_IMPLEMENTED,
                    detail="A verified bookId is required for PDF projection.",
                )
            try:
                projection_bearer, resolved_book_id, resolved_file_id, resolved_format = await self._resolve_bearer_book(
                    creds, snapshot.book_hash, snapshot.book_id,
                )
                snapshot.book_id = resolved_book_id
                snapshot.book_file_id = snapshot.book_file_id or resolved_file_id
                self._reconcile_identity(snapshot, resolved_file_id, resolved_format)
                if creds.bearer_token:
                    owner_key = f"user:{getattr(projection_bearer, 'user_id', '')}"
                prior = await ProgressStateCache.get(owner_key, snapshot.book_hash)
                current_app = self._app_progress_snapshot(
                    await self.official_client.get_app_book_progress(
                        snapshot.book_id, projection_bearer,
                    ),
                    snapshot.book_hash, snapshot.book_id, snapshot.book_file_id, prior,
                )
            except OfficialClientError as exc:
                if getattr(exc, "status_code", None) == 404:
                    current_app = None
                else:
                    raise self._upstream_error(exc) from exc
            conflict = self._timestamp_conflict(snapshot, current_app)
            if conflict is not None:
                return conflict
            try:
                await self.official_client.update_book_progress(
                    snapshot.book_id, self._projection_payload(snapshot), projection_bearer,
                )
            except OfficialClientError as exc:
                raise self._upstream_error(exc) from exc
            await ProgressStateCache.put(owner_key, snapshot)
            return {"status": "progress updated", "projection": "official-app"}

        current = md5_current

        current_epoch = current.timestamp_epoch if current else None
        if not snapshot.force and current_epoch is not None:
            if snapshot.expected_updated_at is not None and snapshot.expected_updated_at != current_epoch:
                return self._conflict(current_epoch)
            if snapshot.timestamp_epoch is not None and snapshot.timestamp_epoch < current_epoch:
                return self._conflict(current_epoch)

        payload: dict[str, Any] = {
            "document": snapshot.book_hash,
            "bookHash": snapshot.book_hash,
            "bookId": snapshot.book_id,
            "bookFileId": snapshot.book_file_id,
            "fileFormat": snapshot.format,
            "progress": snapshot.native_location,
            "location": snapshot.native_location if snapshot.is_reflowable else None,
            "percentage": snapshot.official_fraction,
            "currentPage": snapshot.current_page,
            "totalPages": snapshot.total_pages,
            "device": snapshot.device,
            "deviceId": snapshot.device_id,
            "timestamp": snapshot.timestamp_epoch,
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        try:
            result = await self.official_client.update_koreader_progress(payload, username, md5_key)
        except OfficialClientError as exc:
            raise self._upstream_error(exc) from exc

        # When the request identifies a book, mirror the write through the
        # JWT projection so WebUI/file-level PDF and EPUB positions round-trip.
        await ProgressStateCache.put(owner_key, snapshot)
        extras = {key: value for key, value in result.items() if key in {"updated", "message"}}
        return {"status": "progress updated", **extras} if isinstance(result, dict) else {"status": "progress updated"}

    @staticmethod
    def _timestamp_conflict(
        incoming: ProgressSnapshot, current: ProgressSnapshot | None,
    ) -> dict[str, Any] | None:
        current_epoch = current.timestamp_epoch if current else None
        if incoming.force or current_epoch is None:
            return None
        if incoming.expected_updated_at is not None and incoming.expected_updated_at != current_epoch:
            return ProgressService._conflict(current_epoch)
        if incoming.timestamp_epoch is not None and incoming.timestamp_epoch < current_epoch:
            return ProgressService._conflict(current_epoch)
        return None

    @staticmethod
    def _conflict(current_epoch: int) -> dict[str, Any]:
        return {
            "status": "conflict",
            "updated": False,
            "conflictDetected": True,
            "expectedUpdatedAt": current_epoch,
            "message": "Remote progress is newer; retry with force=true to overwrite it.",
        }
