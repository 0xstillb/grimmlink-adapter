"""Safe OPF metadata ingestion: Official API first, sidecar only on allowed failures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.opf_metadata import IngestionPreview, IngestionResult
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import (
    OfficialAuthError,
    OfficialBadResponseError,
    OfficialPermissionError,
    OfficialTimeoutError,
    OfficialTransportError,
)
from grimmlink_adapter.opf.parser import file_fingerprint, parse_opf
from grimmlink_adapter.opf.sidecar import write_sidecars
from grimmlink_adapter.state.opf_ingestion import OPFStateStore


class OPFIngestionError(Exception):
    """Base error for an ingestion that must stop without fallback."""


class OPFIdentityError(OPFIngestionError):
    """The caller did not provide a verified exact book identity."""


class OPFFieldLockError(OPFIngestionError):
    """A requested field is locked and cannot be safely changed."""


class OPFAuthorizationError(OPFIngestionError):
    """Official rejected the authenticated metadata operation."""


class OPFSourceMutationError(OPFIngestionError):
    """The source ebook/PDF changed while ingestion was running."""


IdentityResolver = Callable[[Path], Awaitable[int]]


class OPFIngestionService:
    """Parse, normalize, and deliver OPF metadata without editing book bytes."""

    def __init__(
        self,
        official_client: OfficialGrimmoryClient | None = None,
        state_store: type[OPFStateStore] = OPFStateStore,
        identity_resolver: IdentityResolver | None = None,
    ) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()
        self.state_store = state_store
        self.identity_resolver = identity_resolver

    async def ingest(
        self,
        *,
        book_path: Path,
        opf_path: Path,
        bearer_token: str | None = None,
        book_id: int | None = None,
        mode: str | None = None,
        dry_run: bool = False,
        locked_fields: set[str] | None = None,
    ) -> IngestionResult:
        mode = (mode or settings.METADATA_INGESTION_MODE).lower()
        if mode not in {"api_only", "api_preferred", "sidecar_only"}:
            raise ValueError(f"Unsupported metadata ingestion mode: {mode}")
        book_path = book_path.resolve(strict=True)
        if not book_path.is_file():
            raise OPFIngestionError("Book target is not a regular file")
        before_hash = file_fingerprint(book_path)
        document = parse_opf(opf_path, root=book_path.parent)
        if book_id is None:
            if self.identity_resolver is None:
                raise OPFIdentityError("A verified exact book ID is required")
            book_id = await self.identity_resolver(book_path)
        if not isinstance(book_id, int) or isinstance(book_id, bool) or book_id <= 0:
            raise OPFIdentityError("A verified exact book ID is required")

        payload = document.metadata.official_payload(book_id)
        metadata_fields = payload["metadata"]
        metadata_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        locked = sorted(field for field in (locked_fields or set()) if field in metadata_fields)
        if locked:
            raise OPFFieldLockError(f"Metadata fields are locked: {', '.join(locked)}")

        source_key = str(document.source_path)
        source_hash = file_fingerprint(document.source_path)
        existing = await self.state_store.get(source_key)
        if (
            existing and existing.source_fingerprint == source_hash
            and existing.metadata_hash == metadata_hash and existing.book_id == book_id
            and existing.last_success_at is not None
        ):
            return IngestionResult(
                status="skipped", book_id=book_id, metadata_hash=metadata_hash,
                primary_action="deduped", fallback_reason=None,
            )

        preview = IngestionPreview(
            opf_source=document.source_path,
            book_target=book_path,
            book_id=book_id,
            fields_to_update=sorted(metadata_fields),
            locked_fields=locked,
            cover_source=document.cover_path,
            primary_action="sidecar" if mode == "sidecar_only" else "official_api",
            fallback_action="sidecar" if mode == "api_preferred" else "none",
        )
        if dry_run:
            return IngestionResult(
                status="dry_run", book_id=book_id, metadata_hash=metadata_hash,
                primary_action=preview.primary_action, preview=preview,
            )

        fallback_reason: str | None = None
        api_result: str | None = None
        sidecar_result: str | None = None
        metadata_sidecar: Path | None = None
        cover_sidecar: Path | None = None

        if mode == "sidecar_only":
            metadata_sidecar, cover_sidecar = write_sidecars(book_path, document.metadata, document.cover_path)
            sidecar_result = "success"
        else:
            if not bearer_token:
                raise OPFAuthorizationError("Bearer token is required for Official metadata API")
            try:
                await self.official_client.update_book_metadata(book_id, bearer_token, payload)
                api_result = "success"
                if document.cover_path:
                    try:
                        await self.official_client.upload_book_cover(book_id, bearer_token, str(document.cover_path))
                    except (OfficialTimeoutError, OfficialTransportError) as exc:
                        if mode == "api_only":
                            raise OPFIngestionError("Official cover upload failed") from exc
                        fallback_reason = "cover_api_unavailable"
                        metadata_sidecar, cover_sidecar = write_sidecars(
                            book_path, document.metadata, document.cover_path,
                        )
                        sidecar_result = "cover_fallback"
                    except OfficialBadResponseError as exc:
                        if mode == "api_only" or (exc.status_code is not None and exc.status_code < 500):
                            raise OPFIngestionError("Official cover upload was rejected") from exc
                        fallback_reason = "cover_api_unavailable"
                        metadata_sidecar, cover_sidecar = write_sidecars(
                            book_path, document.metadata, document.cover_path,
                        )
                        sidecar_result = "cover_fallback"
            except (OfficialAuthError, OfficialPermissionError) as exc:
                raise OPFAuthorizationError("Official metadata API rejected access") from exc
            except OfficialBadResponseError as exc:
                # Grimmory uses 422 for a deployed-version field incompatibility;
                # preserve the source and use the documented sidecar fallback.
                if mode != "api_only" and exc.status_code == 422:
                    fallback_reason = "metadata_api_incompatible"
                    metadata_sidecar, cover_sidecar = write_sidecars(
                        book_path, document.metadata, document.cover_path,
                    )
                    sidecar_result = "success"
                elif mode == "api_only" or exc.status_code is None or exc.status_code < 500:
                    raise OPFIngestionError("Official metadata API rejected the update") from exc
                else:
                    fallback_reason = "metadata_api_unavailable"
                    metadata_sidecar, cover_sidecar = write_sidecars(
                        book_path, document.metadata, document.cover_path,
                    )
                    sidecar_result = "success"
            except (OfficialTimeoutError, OfficialTransportError) as exc:
                if mode == "api_only":
                    raise OPFIngestionError("Official metadata API is unavailable") from exc
                fallback_reason = "metadata_api_unavailable"
                metadata_sidecar, cover_sidecar = write_sidecars(
                    book_path, document.metadata, document.cover_path,
                )
                sidecar_result = "success"

        after_hash = file_fingerprint(book_path)
        if before_hash != after_hash:
            raise OPFSourceMutationError("Original book file changed during OPF ingestion")
        await self.state_store.put(
            source_opf_path=source_key, source_fingerprint=source_hash, book_id=book_id,
            metadata_hash=metadata_hash, api_result=api_result, sidecar_result=sidecar_result,
            fallback_reason=fallback_reason, success=True,
        )
        return IngestionResult(
            status="success", book_id=book_id, metadata_hash=metadata_hash,
            primary_action="sidecar" if sidecar_result and not api_result else "official_api",
            fallback_reason=fallback_reason,
            sidecar_metadata_path=metadata_sidecar, sidecar_cover_path=cover_sidecar,
        )
