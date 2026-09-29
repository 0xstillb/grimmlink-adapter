"""Metadata synchronization service for bookmarks, annotations, and ratings.

Architecture & Field Mapping:
============================
Official Grimmory remains the single source of truth for personal ratings and bookmarks.
The adapter fans out legacy GrimmLink metadata batch payloads to Official endpoints,
maintaining an auxiliary SQLite state for:
  1. Remote ID <-> local ID / dedupeKey bi-directional mappings
  2. Applied history & content hash deduplication
  3. Scoped cursor isolation: strictly (server, user, book, file, type)
  4. Device ID tracking for same-device skip on pull
  5. Source scale retention to prevent 1-10 <-> 1-5 conversion drift

Field Mapping Table:
--------------------
| Entity | Legacy Field | Internal Normalized Field | Official Grimmory Field | Loss Policy / Notes |
|---|---|---|---|---|
| Rating | value / rating (1-10) | rating_value: float | rating: int (1-5) | Converted with deterministic half-up rounding and clamped to 1-5. Source scale & value saved in SQLite history to prevent conversion drift. |
| Rating | scale (default 10) | source_scale: int | N/A | Preserved in local SQLite applied history. |
| Rating | val <= 0 / deleted / reset | is_reset: bool | POST /api/v1/books/reset-personal-rating | Explicit reset; removes personal rating. |
| Rating | review | review: str | N/A | Dropped upstream (not supported by Official rating endpoint); preserved in SQLite history. |
| Bookmark | bookmark_id / dedupeKey | local_id / dedupe_key | N/A | Bi-directional remote ID mapping maintained in SQLite. |
| Bookmark | title | title: str | title: str | Directly mapped. |
| Bookmark | notes | notes: str | notes: str | Directly mapped. |
| Bookmark | chapter | chapter: str | N/A | Preserved in SQLite history. |
| Bookmark | page / location.pageno | page_number: int | pageNumber: int | Directly mapped. |
| Bookmark | location.cfi | cfi: str | cfi: str | Valid EPUB CFI mapped directly; raw XPointers never forced into CFI. |
| Bookmark | location.pos0 / pos1 | pos0 / pos1 | N/A | Preserved in SQLite; never corrupts location. |
| Bookmark | deleted: True | deleted: True | DELETE /api/v1/bookmarks/{id} | Local mapping deleted ONLY after confirmed upstream success. |
| Annotation | annotation_id / dedupeKey | local_id / dedupe_key | N/A | Bi-directional remote ID mapping in SQLite. |
| Annotation | text | text: str | title: str | Mapped to Official bookmark title. |
| Annotation | note | note: str | notes: str | Mapped to Official bookmark notes. |
| Annotation | color | color: str | color: str | Directly mapped. |
| Annotation | drawer | drawer: str | N/A | Dropped upstream; preserved in SQLite payload_json. |
| Annotation | style | style: str | N/A | Dropped upstream; preserved in SQLite payload_json. |
| Annotation | chapter | chapter: str | N/A | Preserved in SQLite payload_json. |
| Annotation | page | page_number: int | pageNumber: int | Directly mapped. |
| Annotation | location.cfi | cfi: str | cfi: str | Mapped ONLY if valid CFI (epubcfi(...)). |
| Annotation | location.pos0 / pos1 | pos0 / pos1 | N/A | Never forced into cfi; preserved in SQLite. |
| Annotation | deleted: True | deleted: True | DELETE /api/v1/bookmarks/{id} | Local mapping deleted ONLY after confirmed upstream success. |
| Dedupe | deviceId | device_id: str | N/A | Preserved in SQLite; used for same-device skip on pull. |
| Dedupe | dedupeKey / contentHash | dedupe_key / content_hash | N/A | Checked before upstream call; DUPLICATE returned without re-creating. |
| Cursor | since / cursor | cursor: str | N/A | Keyed strictly by (server, user, book, file, type); never global. |
"""

import base64
import contextlib
import hashlib
import json
import logging
import math
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.grimmlink import (
    GrimmlinkAnnotationPayload,
    GrimmlinkBookmarkPayload,
    GrimmlinkItemResult,
    GrimmlinkMetadataBatchResponse,
    GrimmlinkMetadataPullItem,
    GrimmlinkMetadataPullResponse,
    GrimmlinkMetadataSyncRequest,
    GrimmlinkMetadataSyncResponse,
    GrimmlinkMetadataSyncResults,
    GrimmlinkRatingPayload,
)
from grimmlink_adapter.models.internal import (
    MetadataDedupeRecord,
    NormalizedAnnotation,
    NormalizedBookmark,
    NormalizedRating,
    OutboxAction,
)
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import OfficialTimeoutError
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.request_auth import get_official_bearer
from grimmlink_adapter.state.book_identity import BookIdentityStore
from grimmlink_adapter.state.cache import MetadataSyncStore
from grimmlink_adapter.state.outbox import IdempotencyManager, OutboxManager

logger = logging.getLogger(__name__)


def is_valid_cfi(cfi: str | None) -> bool:
    """Return True if string is a valid EPUB CFI, and not raw XPointer."""
    if not cfi or not isinstance(cfi, str):
        return False
    stripped = cfi.strip()
    return stripped.startswith("epubcfi(") and stripped.endswith(")")


def normalize_rating(raw_rating: float | int | None, source_scale: int = 10) -> float | int | None:
    """Normalize rating from source scale (1-10 or 1-5) to 1-5 official scale."""
    if raw_rating is None:
        return None
    try:
        val = float(raw_rating)
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    if isinstance(raw_rating, float):
        if source_scale == 10:
            return max(1.0, min(5.0, round(val / 2.0, 1)))
        return max(1.0, min(5.0, round(val, 1)))
    if source_scale == 10:
        return max(1, min(5, (int(val) + 1) // 2))
    return max(1, min(5, int(math.floor(val + 0.5))))


def compute_content_hash(item_type: str, data: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash for item contents to detect duplicate submissions."""
    canonical_repr = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(f"{item_type}:{canonical_repr}".encode()).hexdigest()


class MetadataService:
    """Handles metadata push/pull operations and item fanout to Official Grimmory."""

    def __init__(self, official_client: OfficialGrimmoryClient | None = None) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()

    async def _resolve_book_id(
        self,
        book_id: int | None,
        book_hash: str | None,
        owner_key: str,
        server: str,
    ) -> int:
        """Resolve authoritative book ID from bookId or bookHash."""
        if book_id is not None:
            return book_id
        if not book_hash or not book_hash.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either bookId or bookHash is required for metadata sync.",
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

    def _normalize_rating_input(
        self,
        rating_input: GrimmlinkRatingPayload | dict[str, Any] | None,
        book_id: int,
    ) -> NormalizedRating | None:
        """Normalize rating payload respecting scale and explicit reset."""
        if rating_input is None:
            return None
        if isinstance(rating_input, dict):
            val = rating_input.get("value")
            if val is None:
                val = rating_input.get("rating")
            scale = int(rating_input.get("scale") or 10)
            review = rating_input.get("review")
            dedupe_key = rating_input.get("dedupeKey") or f"rating:{book_id}:koreader"
            updated_at = rating_input.get("updatedAt")
            explicit_reset = bool(rating_input.get("reset")) or bool(rating_input.get("deleted"))
        else:
            val = rating_input.value if rating_input.value is not None else rating_input.rating
            scale = int(rating_input.scale or 10)
            review = rating_input.review
            dedupe_key = rating_input.dedupeKey or f"rating:{book_id}:koreader"
            updated_at = rating_input.updatedAt
            explicit_reset = rating_input.reset or rating_input.deleted

        if scale not in (5, 10):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Rating scale must be 5 or 10.",
            )
        if val is None and not explicit_reset:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Rating requires value/rating or an explicit reset/deleted flag.",
            )
        if val is not None and float(val) > scale:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Rating must not exceed its declared {scale}-point scale.",
            )
        is_reset = explicit_reset or (val is not None and float(val) <= 0)

        norm = normalize_rating(val, scale)
        official_int_rating = (
            int(math.floor(float(norm) + 0.5))
            if norm is not None and not is_reset else None
        )
        return NormalizedRating(
            rating_value=float(val) if val is not None and not is_reset else None,
            source_scale=scale,
            official_rating=official_int_rating,
            is_reset=is_reset,
            review=review,
            dedupe_key=dedupe_key,
            updated_at=updated_at,
        )

    @staticmethod
    def _outbox_key(
        server: str,
        owner_key: str,
        book_id: int,
        item_type: str,
        dedupe_key: str,
        operation: str,
        content_hash: str,
    ) -> str:
        raw = ":".join(
            (server, owner_key, str(book_id), item_type, dedupe_key, operation, content_hash)
        )
        return f"metadata:{hashlib.sha256(raw.encode()).hexdigest()}"

    async def _execute_persisted_action(
        self,
        *,
        action_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        bearer: str,
    ) -> dict[str, Any]:
        """Persist an upstream intent before dispatch and retain failures for retry."""
        action = await OutboxManager.get_by_idempotency_key(idempotency_key)
        if action is None:
            action_id = await OutboxManager.enqueue(action_type, payload, idempotency_key)
            action = await OutboxManager.get_by_idempotency_key(idempotency_key)
            if action is None:  # pragma: no cover - protects against a corrupt outbox write
                raise RuntimeError(f"Unable to load newly queued metadata action {action_id}")
        elif action.status == "FAILED":
            await OutboxManager.requeue(action.id)
            action = await OutboxManager.get_by_idempotency_key(idempotency_key)
            if action is None:  # pragma: no cover
                raise RuntimeError("Unable to reload requeued metadata action")

        try:
            result = await self.execute_outbox_metadata_action(action, bearer)
        except Exception as exc:
            await OutboxManager.mark_failed(action.id, type(exc).__name__)
            raise
        await OutboxManager.mark_completed(action.id)
        return result

    @staticmethod
    def _result_payload(result: dict[str, Any]) -> dict[str, Any]:
        """Recover the original response from an idempotency replay wrapper."""
        body = result.get("body")
        if result.get("status") == "ALREADY_COMPLETED" and isinstance(body, str):
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    return parsed
        return result

    @staticmethod
    def _cursor_scope(
        server: str, owner_key: str, book_id: int, book_file_id: int, item_type: str
    ) -> str:
        raw = f"{server}\0{owner_key}\0{book_id}\0{book_file_id}\0{item_type}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    @staticmethod
    def _encode_page_cursor(scope: str, since: str | None, offset: int) -> str:
        raw = json.dumps(
            {"v": 1, "scope": scope, "since": since, "offset": offset},
            separators=(",", ":"),
        ).encode()
        return "m1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_page_cursor(cursor: str, expected_scope: str) -> tuple[str | None, int] | None:
        if not cursor.startswith("m1."):
            return None
        try:
            encoded = cursor[3:]
            encoded += "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded).decode())
            if payload.get("v") != 1 or payload.get("scope") != expected_scope:
                raise ValueError("cursor scope mismatch")
            offset = int(payload.get("offset", 0))
            if offset < 0:
                raise ValueError("negative cursor offset")
            since = payload.get("since")
            return (str(since) if since is not None else None, offset)
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or out-of-scope metadata cursor.",
            ) from exc

    def _normalize_bookmark_input(
        self,
        bm: GrimmlinkBookmarkPayload | dict[str, Any],
        book_id: int,
    ) -> NormalizedBookmark:
        """Normalize bookmark payload separating valid CFI from unsupported locations."""
        if isinstance(bm, dict):
            local_id = str(bm.get("bookmark_id") or bm.get("id") or bm.get("dedupeKey") or "")
            dedupe_key = str(bm.get("dedupeKey") or local_id or f"bookmark:{book_id}")
            title = bm.get("title")
            notes = bm.get("notes")
            chapter = bm.get("chapter")
            page_number = bm.get("page")
            deleted = bool(bm.get("deleted"))
            loc = bm.get("location") or {}
            cfi = loc.get("cfi") if isinstance(loc, dict) else getattr(loc, "cfi", None)
            pos0 = loc.get("pos0") if isinstance(loc, dict) else getattr(loc, "pos0", None)
            pos1 = loc.get("pos1") if isinstance(loc, dict) else getattr(loc, "pos1", None)
            loc_page = loc.get("page") or loc.get("pageno") if isinstance(loc, dict) else getattr(loc, "page", None)
            if page_number is None and loc_page is not None:
                page_number = int(loc_page)
        else:
            local_id = str(bm.bookmark_id or bm.dedupeKey or "")
            dedupe_key = str(bm.dedupeKey or local_id or f"bookmark:{book_id}")
            title = bm.title
            notes = bm.notes
            chapter = bm.chapter
            page_number = bm.page
            deleted = bool(bm.deleted)
            cfi = bm.location.cfi if bm.location else None
            pos0 = bm.location.pos0 if bm.location else None
            pos1 = bm.location.pos1 if bm.location else None
            if page_number is None and bm.location:
                page_number = bm.location.page or bm.location.pageno

        # CFI validation: never silently shove raw XPointer into cfi!
        clean_cfi = cfi if is_valid_cfi(cfi) else None
        if not local_id:
            local_id = dedupe_key

        return NormalizedBookmark(
            local_id=local_id,
            book_id=book_id,
            title=title,
            notes=notes,
            chapter=chapter,
            page_number=page_number,
            cfi=clean_cfi,
            pos0=str(pos0) if pos0 is not None else None,
            pos1=str(pos1) if pos1 is not None else None,
            deleted=deleted,
            dedupe_key=dedupe_key,
        )

    def _normalize_annotation_input(
        self,
        ann: GrimmlinkAnnotationPayload | dict[str, Any],
        book_id: int,
    ) -> NormalizedAnnotation:
        """Normalize annotation payload preserving unsupported fields explicitly."""
        if isinstance(ann, dict):
            local_id = str(ann.get("annotation_id") or ann.get("id") or ann.get("dedupeKey") or "")
            dedupe_key = str(ann.get("dedupeKey") or local_id or f"annotation:{book_id}")
            ann_type = str(ann.get("type") or "highlight")
            text = ann.get("text")
            note = ann.get("note")
            color = ann.get("color")
            drawer = ann.get("drawer")
            style = ann.get("style")
            chapter = ann.get("chapter")
            page_number = ann.get("page")
            deleted = bool(ann.get("deleted"))
            loc = ann.get("location") or {}
            cfi = loc.get("cfi") if isinstance(loc, dict) else getattr(loc, "cfi", None)
            pos0 = loc.get("pos0") if isinstance(loc, dict) else getattr(loc, "pos0", None)
            pos1 = loc.get("pos1") if isinstance(loc, dict) else getattr(loc, "pos1", None)
            loc_page = loc.get("page") or loc.get("pageno") if isinstance(loc, dict) else getattr(loc, "page", None)
            if page_number is None and loc_page is not None:
                page_number = int(loc_page)
        else:
            local_id = str(ann.annotation_id or ann.dedupeKey or "")
            dedupe_key = str(ann.dedupeKey or local_id or f"annotation:{book_id}")
            ann_type = str(ann.type or "highlight")
            text = ann.text
            note = ann.note
            color = ann.color
            drawer = ann.drawer
            style = ann.style
            chapter = ann.chapter
            page_number = ann.page
            deleted = bool(ann.deleted)
            cfi = ann.location.cfi if ann.location else None
            pos0 = ann.location.pos0 if ann.location else None
            pos1 = ann.location.pos1 if ann.location else None
            if page_number is None and ann.location:
                page_number = ann.location.page or ann.location.pageno

        # CFI validation: never corrupt location by stuffing pos0 into cfi!
        clean_cfi = cfi if is_valid_cfi(cfi) else None
        if not local_id:
            local_id = dedupe_key

        unsupported: dict[str, Any] = {}
        if drawer is not None:
            unsupported["drawer"] = drawer
        if style is not None:
            unsupported["style"] = style
        if pos0 is not None:
            unsupported["pos0"] = pos0
        if pos1 is not None:
            unsupported["pos1"] = pos1

        return NormalizedAnnotation(
            local_id=local_id,
            book_id=book_id,
            type=ann_type,
            text=text,
            note=note,
            color=color,
            drawer=drawer,
            style=style,
            chapter=chapter,
            page_number=page_number,
            cfi=clean_cfi,
            pos0=str(pos0) if pos0 is not None else None,
            pos1=str(pos1) if pos1 is not None else None,
            deleted=deleted,
            dedupe_key=dedupe_key,
            unsupported_fields=unsupported,
        )

    async def sync_metadata(
        self,
        request: GrimmlinkMetadataSyncRequest | None,
        creds: ClientCredentials | None = None,
    ) -> GrimmlinkMetadataSyncResponse:
        """Handle metadata push operations (rating, bookmarks, annotations)."""
        if request is None:
            return GrimmlinkMetadataSyncResponse(
                bookId=None, ok=True, status="ok", appliedCount=0, skippedCount=0,
                results=GrimmlinkMetadataSyncResults(),
            )

        if creds is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credentials required.")
        bearer = await get_official_bearer(creds)
        owner_key = str(getattr(bearer, "user_id", "default"))
        server = settings.GRIMMORY_BASE_URL.rstrip("/")

        book_id = await self._resolve_book_id(request.bookId, request.bookHash, owner_key, server)
        device = request.device
        device_id = request.deviceId

        results_rating: GrimmlinkItemResult | None = None
        results_annotations: list[GrimmlinkItemResult] = []
        results_bookmarks: list[GrimmlinkItemResult] = []

        applied_count = 0
        skipped_count = 0

        # 1. Process Rating
        rating_norm = self._normalize_rating_input(request.rating, book_id)
        if rating_norm is not None:
            dedupe_key = rating_norm.dedupe_key or f"rating:{book_id}:koreader"
            content_hash = compute_content_hash("rating", {
                "val": rating_norm.rating_value,
                "scale": rating_norm.source_scale,
                "reset": rating_norm.is_reset,
                "review": rating_norm.review,
                "updatedAt": rating_norm.updated_at,
            })

            existing = await MetadataSyncStore.get_applied_record(owner_key, book_id, "rating", dedupe_key)
            if existing and existing.content_hash == content_hash and not rating_norm.is_reset:
                results_rating = GrimmlinkItemResult(
                    type="RATING", dedupeKey=dedupe_key, status="duplicate", id=f"rating-{book_id}",
                )
                skipped_count += 1
            else:
                if rating_norm.is_reset:
                    action_key = self._outbox_key(
                        server, owner_key, book_id, "rating", dedupe_key, "reset", content_hash
                    )
                    await self._execute_persisted_action(
                        action_type="RATING_RESET",
                        payload={"bookId": book_id},
                        idempotency_key=action_key,
                        bearer=bearer,
                    )
                    record = MetadataDedupeRecord(
                        owner_key=owner_key,
                        book_id=book_id,
                        item_type="rating",
                        dedupe_key=dedupe_key,
                        content_hash=content_hash,
                        device=device,
                        device_id=device_id,
                        source_scale=rating_norm.source_scale,
                        source_value=None,
                        official_value=None,
                        official_id=None,
                        payload_json=json.dumps({
                            "review": rating_norm.review,
                            "updatedAt": rating_norm.updated_at,
                            "reset": True,
                        }),
                        is_deleted=True,
                    )
                    await MetadataSyncStore.record_applied(record)
                    results_rating = GrimmlinkItemResult(
                        type="RATING", dedupeKey=dedupe_key, status="synced", id=f"rating-{book_id}",
                    )
                    applied_count += 1
                else:
                    assert rating_norm.official_rating is not None
                    action_key = self._outbox_key(
                        server, owner_key, book_id, "rating", dedupe_key, "update", content_hash
                    )
                    await self._execute_persisted_action(
                        action_type="RATING_UPDATE",
                        payload={"bookId": book_id, "rating": rating_norm.official_rating},
                        idempotency_key=action_key,
                        bearer=bearer,
                    )
                    record = MetadataDedupeRecord(
                        owner_key=owner_key,
                        book_id=book_id,
                        item_type="rating",
                        dedupe_key=dedupe_key,
                        content_hash=content_hash,
                        device=device,
                        device_id=device_id,
                        source_scale=rating_norm.source_scale,
                        source_value=rating_norm.rating_value,
                        official_value=rating_norm.official_rating,
                        official_id=None,
                        payload_json=json.dumps({
                            "review": rating_norm.review,
                            "updatedAt": rating_norm.updated_at,
                        }),
                        is_deleted=False,
                    )
                    await MetadataSyncStore.record_applied(record)
                    results_rating = GrimmlinkItemResult(
                        type="RATING", dedupeKey=dedupe_key, status="synced", id=f"rating-{book_id}",
                    )
                    applied_count += 1

        # 2. Process Bookmarks
        for bm_item in request.bookmarks:
            bm = self._normalize_bookmark_input(bm_item, book_id)
            content_hash = compute_content_hash("bookmark", {
                "title": bm.title, "notes": bm.notes, "chapter": bm.chapter,
                "page": bm.page_number, "cfi": bm.cfi, "pos0": bm.pos0,
                "pos1": bm.pos1, "del": bm.deleted,
            })
            existing = await MetadataSyncStore.get_applied_record(owner_key, book_id, "bookmark", bm.dedupe_key or bm.local_id)

            if bm.deleted:
                remote_id = await MetadataSyncStore.get_remote_id(
                    owner_key, book_id, "bookmark", bm.dedupe_key or bm.local_id
                )
                if remote_id is None and existing and existing.official_id:
                    remote_id = existing.official_id
                if remote_id is not None:
                    # Remote deletion: only purge local state after confirmed success
                    action_key = self._outbox_key(
                        server, owner_key, book_id, "bookmark",
                        bm.dedupe_key or bm.local_id, "delete", content_hash,
                    )
                    await self._execute_persisted_action(
                        action_type="BOOKMARK_DELETE",
                        payload={"bookmarkId": remote_id},
                        idempotency_key=action_key,
                        bearer=bearer,
                    )
                    await MetadataSyncStore.delete_remote_mapping(owner_key, book_id, "bookmark", bm.dedupe_key or bm.local_id)
                    await MetadataSyncStore.mark_applied_deleted(owner_key, book_id, "bookmark", bm.dedupe_key or bm.local_id)
                    results_bookmarks.append(
                        GrimmlinkItemResult(
                            type="BOOKMARK", dedupeKey=bm.dedupe_key,
                            status="synced", id=f"bm-{remote_id}",
                        )
                    )
                    applied_count += 1
                else:
                    results_bookmarks.append(
                        GrimmlinkItemResult(
                            type="BOOKMARK", dedupeKey=bm.dedupe_key, status="failed",
                            reason="No confirmed Official bookmark mapping is available for deletion.",
                        )
                    )
                    skipped_count += 1
            else:
                if existing and existing.content_hash == content_hash and not existing.is_deleted:
                    results_bookmarks.append(
                        GrimmlinkItemResult(
                            type="BOOKMARK", dedupeKey=bm.dedupe_key, status="duplicate",
                            id=f"bm-{existing.official_id or bm.local_id}",
                        )
                    )
                    skipped_count += 1
                else:
                    remote_id = await MetadataSyncStore.get_remote_id(
                        owner_key, book_id, "bookmark", bm.dedupe_key or bm.local_id
                    )
                    payload: dict[str, Any] = {
                        "bookId": book_id,
                        "title": bm.title or "Bookmark",
                        "notes": bm.notes,
                        "pageNumber": bm.page_number,
                        "cfi": bm.cfi,
                    }
                    if remote_id is not None:
                        action_key = self._outbox_key(
                            server, owner_key, book_id, "bookmark",
                            bm.dedupe_key or bm.local_id, "update", content_hash,
                        )
                        await self._execute_persisted_action(
                            action_type="BOOKMARK_UPDATE",
                            payload={"bookmarkId": remote_id, "bookmark": payload},
                            idempotency_key=action_key,
                            bearer=bearer,
                        )
                    else:
                        action_key = self._outbox_key(
                            server, owner_key, book_id, "bookmark",
                            bm.dedupe_key or bm.local_id, "create", content_hash,
                        )
                        result = await self._execute_persisted_action(
                            action_type="BOOKMARK_CREATE",
                            payload=payload,
                            idempotency_key=action_key,
                            bearer=bearer,
                        )
                        resp = self._result_payload(result)
                        remote_id = int(resp.get("id") or 0)
                        if not remote_id:
                            raise RuntimeError("Official bookmark creation succeeded without an id")
                        await MetadataSyncStore.set_remote_mapping(
                            owner_key, book_id, "bookmark", bm.local_id, remote_id,
                            bm.dedupe_key or bm.local_id,
                        )

                    raw_json = json.dumps({
                        "title": bm.title, "notes": bm.notes, "chapter": bm.chapter,
                        "page": bm.page_number, "cfi": bm.cfi, "pos0": bm.pos0, "pos1": bm.pos1,
                    })
                    record = MetadataDedupeRecord(
                        owner_key=owner_key,
                        book_id=book_id,
                        item_type="bookmark",
                        dedupe_key=bm.dedupe_key or bm.local_id,
                        content_hash=content_hash,
                        device=device,
                        device_id=device_id,
                        official_id=remote_id,
                        payload_json=raw_json,
                        is_deleted=False,
                    )
                    await MetadataSyncStore.record_applied(record)
                    results_bookmarks.append(
                        GrimmlinkItemResult(
                            type="BOOKMARK", dedupeKey=bm.dedupe_key, status="synced", id=f"bm-{remote_id}",
                        )
                    )
                    applied_count += 1

        # 3. Process Annotations
        for ann_item in request.annotations:
            ann = self._normalize_annotation_input(ann_item, book_id)
            content_hash = compute_content_hash("annotation", {
                "type": ann.type, "text": ann.text, "note": ann.note,
                "color": ann.color, "drawer": ann.drawer, "style": ann.style,
                "chapter": ann.chapter, "page": ann.page_number, "cfi": ann.cfi,
                "pos0": ann.pos0, "pos1": ann.pos1, "del": ann.deleted,
            })
            existing = await MetadataSyncStore.get_applied_record(owner_key, book_id, "annotation", ann.dedupe_key or ann.local_id)

            if ann.deleted:
                remote_id = await MetadataSyncStore.get_remote_id(
                    owner_key, book_id, "annotation", ann.dedupe_key or ann.local_id
                )
                if remote_id is None and existing and existing.official_id:
                    remote_id = existing.official_id
                if remote_id is not None:
                    action_key = self._outbox_key(
                        server, owner_key, book_id, "annotation",
                        ann.dedupe_key or ann.local_id, "delete", content_hash,
                    )
                    await self._execute_persisted_action(
                        action_type="BOOKMARK_DELETE",
                        payload={"bookmarkId": remote_id},
                        idempotency_key=action_key,
                        bearer=bearer,
                    )
                    await MetadataSyncStore.delete_remote_mapping(owner_key, book_id, "annotation", ann.dedupe_key or ann.local_id)
                    await MetadataSyncStore.mark_applied_deleted(owner_key, book_id, "annotation", ann.dedupe_key or ann.local_id)
                    results_annotations.append(
                        GrimmlinkItemResult(
                            type="ANNOTATION", dedupeKey=ann.dedupe_key,
                            status="synced", id=f"ann-{remote_id}",
                        )
                    )
                    applied_count += 1
                else:
                    results_annotations.append(
                        GrimmlinkItemResult(
                            type="ANNOTATION", dedupeKey=ann.dedupe_key, status="failed",
                            reason="No confirmed Official annotation mapping is available for deletion.",
                        )
                    )
                    skipped_count += 1
            else:
                if existing and existing.content_hash == content_hash and not existing.is_deleted:
                    results_annotations.append(
                        GrimmlinkItemResult(
                            type="ANNOTATION", dedupeKey=ann.dedupe_key, status="duplicate",
                            id=f"ann-{existing.official_id or ann.local_id}",
                        )
                    )
                    skipped_count += 1
                else:
                    remote_id = await MetadataSyncStore.get_remote_id(
                        owner_key, book_id, "annotation", ann.dedupe_key or ann.local_id
                    )
                    payload = {
                        "bookId": book_id,
                        "title": ann.text or ann.note or "Highlight",
                        "notes": ann.note or ann.text,
                        "color": ann.color,
                        "pageNumber": ann.page_number,
                        "cfi": ann.cfi,
                    }
                    if remote_id is not None:
                        action_key = self._outbox_key(
                            server, owner_key, book_id, "annotation",
                            ann.dedupe_key or ann.local_id, "update", content_hash,
                        )
                        await self._execute_persisted_action(
                            action_type="BOOKMARK_UPDATE",
                            payload={"bookmarkId": remote_id, "bookmark": payload},
                            idempotency_key=action_key,
                            bearer=bearer,
                        )
                    else:
                        action_key = self._outbox_key(
                            server, owner_key, book_id, "annotation",
                            ann.dedupe_key or ann.local_id, "create", content_hash,
                        )
                        result = await self._execute_persisted_action(
                            action_type="BOOKMARK_CREATE",
                            payload=payload,
                            idempotency_key=action_key,
                            bearer=bearer,
                        )
                        resp = self._result_payload(result)
                        remote_id = int(resp.get("id") or 0)
                        if not remote_id:
                            raise RuntimeError("Official annotation creation succeeded without an id")
                        await MetadataSyncStore.set_remote_mapping(
                            owner_key, book_id, "annotation", ann.local_id, remote_id,
                            ann.dedupe_key or ann.local_id,
                        )

                    raw_json = json.dumps({
                        "type": ann.type, "text": ann.text, "note": ann.note, "color": ann.color,
                        "drawer": ann.drawer, "style": ann.style, "chapter": ann.chapter,
                        "page": ann.page_number, "cfi": ann.cfi, "pos0": ann.pos0, "pos1": ann.pos1,
                    })
                    record = MetadataDedupeRecord(
                        owner_key=owner_key,
                        book_id=book_id,
                        item_type="annotation",
                        dedupe_key=ann.dedupe_key or ann.local_id,
                        content_hash=content_hash,
                        device=device,
                        device_id=device_id,
                        official_id=remote_id,
                        payload_json=raw_json,
                        is_deleted=False,
                    )
                    await MetadataSyncStore.record_applied(record)
                    results_annotations.append(
                        GrimmlinkItemResult(
                            type="ANNOTATION", dedupeKey=ann.dedupe_key, status="synced", id=f"ann-{remote_id}",
                        )
                    )
                    applied_count += 1

        results_obj = GrimmlinkMetadataSyncResults(
            rating=results_rating,
            annotations=results_annotations,
            bookmarks=results_bookmarks,
        )

        return GrimmlinkMetadataSyncResponse(
            bookId=book_id,
            ok=True,
            status="ok",
            appliedCount=applied_count,
            skippedCount=skipped_count,
            results=results_obj,
        )

    async def pull_metadata(
        self,
        book_id: int | None = None,
        book_hash: str | None = None,
        book_file_id: int | None = None,
        since: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        item_type: str | None = None,
        device_id: str | None = None,
        creds: ClientCredentials | None = None,
    ) -> GrimmlinkMetadataPullResponse:
        """Pull metadata updates since a given cursor or timestamp with same-device skip."""
        if creds is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credentials required.")
        bearer = await get_official_bearer(creds)
        owner_key = str(getattr(bearer, "user_id", "default"))
        server = settings.GRIMMORY_BASE_URL.rstrip("/")

        resolved_book_id = await self._resolve_book_id(book_id, book_hash, owner_key, server)
        limit_val = min(limit, 500) if limit and limit > 0 else 50
        type_filter = (item_type or "").strip().lower() or None
        if type_filter not in (None, "rating", "bookmark", "annotation"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Metadata type must be rating, bookmark, or annotation.",
            )

        # Cursor Isolation: Strictly keyed by (server, owner_key, book_id, book_file_id, item_type)
        cursor_type_key = type_filter or "all"
        file_scope = book_file_id or 0
        scope = self._cursor_scope(
            server, owner_key, resolved_book_id, file_scope, cursor_type_key
        )
        supplied_cursor = cursor or since
        if supplied_cursor is None and device_id is None:
            supplied_cursor = await MetadataSyncStore.get_scoped_cursor(
                server, owner_key, resolved_book_id, file_scope, cursor_type_key
            )
        page_state = self._decode_page_cursor(supplied_cursor, scope) if supplied_cursor else None
        if page_state is not None:
            effective_cursor, page_offset = page_state
        else:
            effective_cursor, page_offset = supplied_cursor, 0

        items: list[GrimmlinkMetadataPullItem] = []
        remote_deleted_records: list[MetadataDedupeRecord] = []

        # 1. Rating Pull
        if type_filter in (None, "rating"):
            official_rating = await self.official_client.get_personal_rating(resolved_book_id, bearer)
            last_applied = await MetadataSyncStore.get_applied_record(
                owner_key, resolved_book_id, "rating", f"rating:{resolved_book_id}:koreader"
            )
            if last_applied is None:
                last_applied = await MetadataSyncStore.get_latest_applied_for_type(
                    owner_key, resolved_book_id, "rating"
                )
            if official_rating is not None and official_rating > 0:
                remote_changed = bool(
                    last_applied is None
                    or last_applied.is_deleted
                    or last_applied.official_value != official_rating
                )
                # Same-device skip
                after_cursor = remote_changed or not (
                    effective_cursor and last_applied and last_applied.synced_at
                    and last_applied.synced_at.isoformat() <= effective_cursor
                )
                if after_cursor and not (
                    not remote_changed and last_applied and device_id
                    and last_applied.device_id == device_id
                ):
                    # Prevent conversion drift: If source scale 10 was preserved and matches official rating
                    if (
                        last_applied
                        and last_applied.official_value == official_rating
                        and last_applied.source_scale == 10
                        and last_applied.source_value is not None
                    ):
                        ret_val: float | int = last_applied.source_value
                        if ret_val.is_integer():
                            ret_val = int(ret_val)
                    else:
                        ret_val = official_rating * 2

                    now_iso = datetime.now(UTC).isoformat()
                    updated_at_val = (
                        last_applied.synced_at.isoformat()
                        if last_applied and last_applied.synced_at
                        else now_iso
                    )
                    rating_payload: dict[str, Any] = {
                        "value": ret_val,
                        "scale": 10,
                        "source": "grimmory-web",
                    }
                    if last_applied and last_applied.payload_json:
                        with contextlib.suppress(json.JSONDecodeError):
                            saved_rating = json.loads(last_applied.payload_json)
                            if isinstance(saved_rating, dict) and saved_rating.get("review") is not None:
                                rating_payload["review"] = saved_rating["review"]
                    items.append(
                        GrimmlinkMetadataPullItem(
                            id="grimmory-personal-rating",
                            type="rating",
                            bookId=resolved_book_id,
                            bookFileId=book_file_id or 101,
                            dedupeKey=f"grimmory-personal-rating:{ret_val}",
                            payload=rating_payload,
                            device="Grimmory Web",
                            updatedAt=updated_at_val,
                        )
                    )
            elif last_applied and not last_applied.is_deleted:
                # Official has no rating while local history still has one: emit an explicit reset.
                items.append(
                    GrimmlinkMetadataPullItem(
                        id="grimmory-personal-rating-reset",
                        type="rating",
                        bookId=resolved_book_id,
                        bookFileId=book_file_id,
                        dedupeKey=f"grimmory-personal-rating:reset:{last_applied.dedupe_key}",
                        payload={"value": 0, "scale": 10, "reset": True, "source": "grimmory-web"},
                        device="Grimmory Web",
                        updatedAt=datetime.now(UTC).isoformat(),
                    )
                )

        # 2. Bookmarks & Annotations Pull
        if type_filter in (None, "bookmark", "annotation"):
            official_bms = await self.official_client.get_bookmarks_for_book(resolved_book_id, bearer)
            official_ids = {
                int(obm["id"]) for obm in official_bms
                if obm.get("id") is not None and str(obm.get("id")).isdigit()
            }
            for obm in official_bms:
                obm_id = obm.get("id")
                if not obm_id:
                    continue

                rec = await MetadataSyncStore.get_applied_by_official_id(owner_key, resolved_book_id, obm_id)
                if rec:
                    payload_dict: dict[str, Any] = {}
                    if rec.payload_json:
                        with contextlib.suppress(json.JSONDecodeError):
                            decoded_payload = json.loads(rec.payload_json)
                            if isinstance(decoded_payload, dict):
                                payload_dict = decoded_payload
                    if rec.item_type == "annotation":
                        field_map = {
                            "title": "text", "notes": "note", "color": "color",
                            "pageNumber": "page", "cfi": "cfi",
                        }
                    else:
                        field_map = {
                            "title": "title", "notes": "notes",
                            "pageNumber": "page", "cfi": "cfi",
                        }
                    supported_payload = {
                        local_name: obm[official_name]
                        for official_name, local_name in field_map.items()
                        if official_name in obm
                    }
                    remote_changed = any(
                        payload_dict.get(key) != value
                        for key, value in supported_payload.items()
                    )
                    payload_dict.update(supported_payload)
                    if not remote_changed and device_id and rec.device_id == device_id:
                        continue
                    if (
                        not remote_changed and effective_cursor and rec.synced_at
                        and rec.synced_at.isoformat() <= effective_cursor
                    ):
                        continue
                    content_hash = (
                        compute_content_hash(rec.item_type, payload_dict)
                        if remote_changed else rec.content_hash
                    )
                    updated_at = (
                        datetime.now(UTC).isoformat()
                        if remote_changed
                        else (rec.synced_at.isoformat() if rec.synced_at else None)
                    )
                    if remote_changed:
                        await MetadataSyncStore.record_applied(
                            MetadataDedupeRecord(
                                owner_key=owner_key,
                                book_id=resolved_book_id,
                                item_type=rec.item_type,
                                dedupe_key=rec.dedupe_key,
                                content_hash=content_hash,
                                device="Grimmory Web",
                                device_id=None,
                                official_id=rec.official_id,
                                payload_json=json.dumps(payload_dict),
                                is_deleted=False,
                            )
                        )
                    items.append(
                        GrimmlinkMetadataPullItem(
                            id=rec.dedupe_key,
                            type=rec.item_type,
                            bookId=resolved_book_id,
                            bookFileId=book_file_id or 101,
                            dedupeKey=rec.dedupe_key,
                            contentHash=content_hash,
                            payload=payload_dict,
                            device="Grimmory Web" if remote_changed else rec.device,
                            deviceId=None if remote_changed else rec.device_id,
                            updatedAt=updated_at,
                        )
                    )
                else:
                    # Upstream bookmark created on Official WebUI
                    if type_filter in (None, "bookmark"):
                        upstream_updated = obm.get("updatedAt") or obm.get("updated_at")
                        if effective_cursor and upstream_updated and str(upstream_updated) <= effective_cursor:
                            continue
                        web_dedupe_key = f"bookmark:{resolved_book_id}:{obm_id}"
                        web_payload = {
                            "title": obm.get("title"),
                            "notes": obm.get("notes"),
                            "page": obm.get("pageNumber"),
                            "cfi": obm.get("cfi"),
                            "location": {"cfi": obm.get("cfi")} if obm.get("cfi") else None,
                        }
                        web_hash = compute_content_hash("bookmark", web_payload)
                        await MetadataSyncStore.set_remote_mapping(
                            owner_key, resolved_book_id, "bookmark", web_dedupe_key,
                            int(obm_id), web_dedupe_key,
                        )
                        await MetadataSyncStore.record_applied(
                            MetadataDedupeRecord(
                                owner_key=owner_key,
                                book_id=resolved_book_id,
                                item_type="bookmark",
                                dedupe_key=web_dedupe_key,
                                content_hash=web_hash,
                                device="Grimmory Web",
                                device_id=None,
                                official_id=int(obm_id),
                                payload_json=json.dumps(web_payload),
                                is_deleted=False,
                            )
                        )
                        items.append(
                            GrimmlinkMetadataPullItem(
                                id=f"bm-{obm_id}",
                                type="bookmark",
                                bookId=resolved_book_id,
                                bookFileId=book_file_id or 101,
                                dedupeKey=web_dedupe_key,
                                contentHash=web_hash,
                                payload=web_payload,
                                device="Grimmory Web",
                                updatedAt=str(upstream_updated) if upstream_updated else None,
                            )
                        )

            # A mapped record missing from Official is a confirmed remote deletion.
            history_type = type_filter if type_filter in ("bookmark", "annotation") else None
            active_history = await MetadataSyncStore.list_applied_history(
                owner_key, resolved_book_id, limit=100_000, item_type=history_type
            )
            for rec in active_history:
                if rec.item_type not in ("bookmark", "annotation") or rec.official_id is None:
                    continue
                if rec.official_id in official_ids:
                    continue
                remote_deleted_records.append(rec)
                items.append(
                    GrimmlinkMetadataPullItem(
                        id=rec.dedupe_key,
                        type=rec.item_type,
                        bookId=resolved_book_id,
                        bookFileId=book_file_id,
                        dedupeKey=rec.dedupe_key,
                        contentHash=rec.content_hash,
                        payload={"deleted": True},
                        device="Grimmory Web",
                        updatedAt=datetime.now(UTC).isoformat(),
                    )
                )

        items.sort(key=lambda item: (item.type, item.dedupeKey or item.id, item.id))
        page_items = items[page_offset:page_offset + limit_val]
        has_more = page_offset + limit_val < len(items)
        if has_more:
            next_cursor = self._encode_page_cursor(
                scope, effective_cursor, page_offset + limit_val
            )
        else:
            next_cursor = datetime.now(UTC).isoformat()
            for rec in remote_deleted_records:
                await MetadataSyncStore.mark_applied_deleted(
                    owner_key, resolved_book_id, rec.item_type, rec.dedupe_key
                )
        await MetadataSyncStore.set_scoped_cursor(
            server, owner_key, resolved_book_id, file_scope, cursor_type_key, next_cursor
        )

        return GrimmlinkMetadataPullResponse(
            bookId=resolved_book_id,
            bookFileId=book_file_id or 101,
            ok=True,
            since=effective_cursor,
            nextCursor=next_cursor,
            limit=limit_val,
            items=page_items,
            hasMore=has_more,
        )

    async def sync_metadata_batch(
        self,
        request: GrimmlinkMetadataSyncRequest | None,
        creds: ClientCredentials | None = None,
    ) -> GrimmlinkMetadataBatchResponse:
        """Handle metadata batch sync push followed by pull."""
        push_resp = await self.sync_metadata(request, creds)
        pull_resp = await self.pull_metadata(
            book_id=request.bookId if request else None,
            book_hash=request.bookHash if request else None,
            book_file_id=request.bookFileId if request else None,
            since=request.since if request else None,
            cursor=request.cursor if request else None,
            limit=request.limit if request else None,
            item_type=request.type if request else None,
            device_id=request.deviceId if request else None,
            creds=creds,
        )

        total_items = push_resp.appliedCount + push_resp.skippedCount
        return GrimmlinkMetadataBatchResponse(
            ok=True,
            push=push_resp,
            pull=pull_resp,
            status="ok",
            totalItems=total_items,
            successCount=push_resp.appliedCount,
            failureCount=0,
        )

    async def execute_outbox_metadata_action(
        self,
        action: OutboxAction,
        bearer_token: str,
    ) -> dict[str, Any]:
        """Execute an outbox metadata action idempotently with timeout-after-commit safety."""
        # 1. Check if already recorded in idempotency store
        recorded = await IdempotencyManager.get_response(action.idempotency_key)
        if recorded:
            return {"status": "ALREADY_COMPLETED", "status_code": recorded[0], "body": recorded[1]}

        action_type = action.action_type
        payload = action.payload

        if action_type == "BOOKMARK_CREATE":
            # Timeout-after-commit mitigation: probe remote bookmarks first before re-creating
            book_id_raw = payload.get("bookId")
            if book_id_raw is None:
                raise ValueError("bookId is required for BOOKMARK_CREATE")
            book_id = int(book_id_raw)
            title = payload.get("title")
            cfi = payload.get("cfi")
            page_number = payload.get("pageNumber")
            notes = payload.get("notes")
            color = payload.get("color")
            existing_bms: list[dict[str, Any]] = []
            if action.retry_count > 0:
                existing_bms = await self.official_client.get_bookmarks_for_book(
                    book_id, bearer_token
                )
            for e_bm in existing_bms:
                if (
                    e_bm.get("title") == title
                    and e_bm.get("cfi") == cfi
                    and e_bm.get("pageNumber") == page_number
                    and e_bm.get("notes") == notes
                    and e_bm.get("color") == color
                ):
                    res = {"id": e_bm.get("id"), "status": "ALREADY_COMMITTED"}
                    await IdempotencyManager.record_response(action.idempotency_key, action_type, 200, json.dumps(res))
                    return res

            try:
                res = await self.official_client.create_bookmark(payload, bearer_token)
                if not res.get("id"):
                    raise RuntimeError("Official bookmark creation succeeded without an id")
                await IdempotencyManager.record_response(action.idempotency_key, action_type, 200, json.dumps(res))
                return res
            except OfficialTimeoutError as exc:
                logger.warning("Timeout during bookmark creation; action remains retryable: %s", exc)
                raise

        elif action_type == "RATING_UPDATE":
            book_id_raw = payload.get("bookId")
            rating_raw = payload.get("rating")
            if book_id_raw is None or rating_raw is None:
                raise ValueError("bookId and rating are required for RATING_UPDATE")
            book_id = int(book_id_raw)
            rating = int(rating_raw)
            await self.official_client.update_personal_rating(book_id, rating, bearer_token)
            res = {"status": "SUCCESS"}
            await IdempotencyManager.record_response(action.idempotency_key, action_type, 200, json.dumps(res))
            return res

        elif action_type == "RATING_RESET":
            book_id_raw = payload.get("bookId")
            if book_id_raw is None:
                raise ValueError("bookId is required for RATING_RESET")
            await self.official_client.reset_personal_rating(int(book_id_raw), bearer_token)
            res = {"status": "RESET"}
            await IdempotencyManager.record_response(
                action.idempotency_key, action_type, 200, json.dumps(res)
            )
            return res

        elif action_type == "BOOKMARK_UPDATE":
            bookmark_id_raw = payload.get("bookmarkId")
            bookmark_payload = payload.get("bookmark")
            if bookmark_id_raw is None or not isinstance(bookmark_payload, dict):
                raise ValueError("bookmarkId and bookmark are required for BOOKMARK_UPDATE")
            res = await self.official_client.update_bookmark(
                int(bookmark_id_raw), bookmark_payload, bearer_token
            )
            await IdempotencyManager.record_response(
                action.idempotency_key, action_type, 200, json.dumps(res)
            )
            return res

        elif action_type == "BOOKMARK_DELETE":
            bookmark_id_raw = payload.get("bookmarkId")
            if bookmark_id_raw is None:
                raise ValueError("bookmarkId is required for BOOKMARK_DELETE")
            bookmark_id = int(bookmark_id_raw)
            await self.official_client.delete_bookmark(bookmark_id, bearer_token)
            res = {"status": "DELETED"}
            await IdempotencyManager.record_response(action.idempotency_key, action_type, 200, json.dumps(res))
            return res

        raise ValueError(f"Unknown outbox action type: {action_type}")
