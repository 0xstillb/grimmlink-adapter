"""OPF ingestion with verified identity and fail-closed source-file protection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.opf_metadata import IngestionPreview, IngestionResult
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import (
    OfficialAuthError,
    OfficialBadResponseError,
    OfficialClientError,
    OfficialPermissionError,
    OfficialTimeoutError,
    OfficialTransportError,
)
from grimmlink_adapter.official.identity_lookup import (
    GrimmoryIdentityLookup,
    GrimmoryIdentityLookupError,
)
from grimmlink_adapter.opf.parser import (
    discover_opf,
    file_fingerprint,
    grimmory_file_fingerprint,
    parse_opf,
)
from grimmlink_adapter.opf.sidecar import write_sidecars
from grimmlink_adapter.state.opf_ingestion import OPFStateStore


class OPFIngestionError(Exception):
    """Base error for an ingestion that must stop without claiming success."""


class OPFIdentityError(OPFIngestionError):
    """No exact, unambiguous hash-to-book/file identity was proven."""


class OPFFieldLockError(OPFIngestionError):
    """A requested metadata field is locked in Official Grimmory."""


class OPFAuthorizationError(OPFIngestionError):
    """Official rejected access to settings, book, metadata, or sidecar import."""


class OPFSourceMutationProtectionError(OPFIngestionError):
    """Official's current settings could allow a write to the source book file."""


class OPFSourceMutationError(OPFIngestionError):
    """The source ebook/PDF changed while ingestion was running."""


class OPFIngestionService:
    """Resolve an exact Grimmory file, then update metadata without file writes."""

    def __init__(
        self,
        official_client: OfficialGrimmoryClient | None = None,
        state_store: type[OPFStateStore] = OPFStateStore,
        identity_lookup: GrimmoryIdentityLookup | None = None,
    ) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()
        self.state_store = state_store
        self.identity_lookup = identity_lookup or GrimmoryIdentityLookup()

    async def ingest(
        self,
        *,
        book_path: Path,
        opf_path: Path | None = None,
        bearer_token: str,
        book_id: int | None = None,
        mode: str | None = None,
        dry_run: bool = False,
        locked_fields: set[str] | None = None,
    ) -> IngestionResult:
        mode = (mode or settings.METADATA_INGESTION_MODE).lower()
        if mode not in {"api_only", "api_preferred", "sidecar_only"}:
            raise ValueError(f"Unsupported metadata ingestion mode: {mode}")
        fallback_mode = settings.METADATA_FALLBACK.lower()
        if fallback_mode not in {"sidecar", "none"}:
            raise ValueError(f"Unsupported metadata fallback mode: {fallback_mode}")
        if not bearer_token:
            raise OPFAuthorizationError("A Bearer token is required to verify book identity")

        book_path = book_path.resolve(strict=True)
        if not book_path.is_file():
            raise OPFIngestionError("Book target is not a regular file")
        before_hash = file_fingerprint(book_path)
        resolved_opf = (opf_path or discover_opf(book_path, root=book_path.parent)).resolve(strict=True)
        document = parse_opf(resolved_opf, root=book_path.parent)
        if document.cover_path:
            with document.cover_path.open("rb") as cover:
                if cover.read(3) != b"\xff\xd8\xff":
                    raise OPFIngestionError("OPF cover must be JPEG before any Official write")

        identity, book_detail = await self._resolve_identity(book_path, bearer_token, book_id)
        payload = document.metadata.official_payload(identity["book_id"])
        metadata_fields = {field: value for field, value in payload["metadata"].items() if field != "bookId"}
        lock_map = self._book_field_locks(book_detail)
        if locked_fields:
            # Callers may provide additional verified locks; they cannot suppress
            # the lock state read from the Official book response.
            for name in locked_fields:
                lock_map[name] = True
        locked = sorted(
            field for field in metadata_fields
            if lock_map.get("*", False) or lock_map.get(field, False)
        )
        if locked:
            raise OPFFieldLockError(f"Metadata fields are locked: {', '.join(locked)}")

        source_key = str(document.source_path)
        source_hash = file_fingerprint(document.source_path)
        cover_hash = file_fingerprint(document.cover_path) if document.cover_path else None
        input_fingerprints = {
            document.source_path: source_hash,
            **({document.cover_path: cover_hash} if document.cover_path and cover_hash else {}),
        }
        mode_fingerprint = f"{mode}:{fallback_mode}"
        metadata_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        # This read is mandatory. A missing setting, denied read, or malformed
        # response cannot establish that Official will preserve source bytes.
        persistence_safe = await self._source_file_writes_disabled(bearer_token)
        existing = await self.state_store.get(source_key)
        if (
            existing
            and existing.source_fingerprint == source_hash
            and existing.cover_fingerprint == cover_hash
            and existing.metadata_hash == metadata_hash
            and existing.book_id == identity["book_id"]
            and existing.delivery_mode == mode_fingerprint
            and existing.last_success_at is not None
        ):
            return IngestionResult(
                status=existing.last_result_status,
                book_id=identity["book_id"],
                metadata_hash=metadata_hash,
                primary_action="deduped",
                cover_status="sidecar_pending" if document.cover_path else None,
            )

        if mode == "api_only" and document.cover_path:
            raise OPFSourceMutationProtectionError(
                "Stock Official cover upload can rewrite the source file; use sidecar mode for covers"
            )

        preview = IngestionPreview(
            opf_source=document.source_path,
            book_target=book_path,
            book_id=identity["book_id"],
            fields_to_update=sorted(metadata_fields),
            locked_fields=locked,
            cover_source=document.cover_path,
            primary_action="sidecar" if mode == "sidecar_only" else "official_api",
            fallback_action="sidecar" if mode == "api_preferred" else "none",
            would_modify_book_file=False,
        )
        if dry_run:
            return IngestionResult(
                status="dry_run", book_id=identity["book_id"], metadata_hash=metadata_hash,
                primary_action=preview.primary_action, preview=preview,
                cover_status="sidecar_pending" if document.cover_path else None,
            )

        if mode == "api_only" and not persistence_safe:
            raise OPFSourceMutationProtectionError(
                "Official file persistence or file-moving settings are enabled"
            )
        if mode == "api_preferred" and fallback_mode == "none" and not persistence_safe:
            raise OPFSourceMutationProtectionError(
                "Official file persistence is unknown or enabled and sidecar fallback is disabled"
            )

        api_result: str | None = None
        sidecar_result: str | None = None
        fallback_reason: str | None = None
        metadata_sidecar: Path | None = None
        cover_sidecar: Path | None = None
        result_status = "success"

        if mode == "sidecar_only":
            metadata_sidecar, cover_sidecar = write_sidecars(book_path, document.metadata, document.cover_path)
            sidecar_result, fallback_reason = await self._import_or_defer_sidecar(
                identity["book_id"], bearer_token, persistence_safe,
            )
            result_status = "partial" if document.cover_path or sidecar_result != "imported" else "success"
        elif not persistence_safe:
            if mode == "api_only":
                raise OPFSourceMutationProtectionError("Official source-file write protection is not active")
            if fallback_mode == "none":
                raise OPFSourceMutationProtectionError("Sidecar fallback is disabled")
            metadata_sidecar, cover_sidecar = write_sidecars(book_path, document.metadata, document.cover_path)
            sidecar_result = "pending_import"
            fallback_reason = "official_file_persistence_enabled"
            result_status = "partial"
        else:
            try:
                await self.official_client.update_book_metadata(
                    identity["book_id"], bearer_token, payload,
                )
                api_result = "success"
                if document.cover_path:
                    metadata_sidecar, cover_sidecar = write_sidecars(
                        book_path, document.metadata, document.cover_path,
                    )
                    sidecar_result = "cover_pending"
                    result_status = "partial"
            except (OfficialAuthError, OfficialPermissionError) as exc:
                raise OPFAuthorizationError("Official metadata API rejected access") from exc
            except OfficialBadResponseError as exc:
                field_incompatibility = _is_field_incompatibility(exc)
                server_error = exc.status_code is not None and 500 <= exc.status_code < 600
                if mode == "api_only" or not (field_incompatibility or server_error):
                    raise OPFIngestionError("Official metadata update was rejected") from exc
                if fallback_mode == "none":
                    raise OPFIngestionError("Official metadata update failed and sidecar fallback is disabled") from exc
                metadata_sidecar, cover_sidecar = write_sidecars(book_path, document.metadata, document.cover_path)
                sidecar_result, fallback_reason = await self._import_or_defer_sidecar(
                    identity["book_id"], bearer_token, persistence_safe,
                )
                fallback_reason = fallback_reason or (
                    "metadata_field_incompatible" if field_incompatibility else "metadata_api_server_error"
                )
                result_status = "partial" if document.cover_path or sidecar_result != "imported" else "success"
            except (OfficialTimeoutError, OfficialTransportError) as exc:
                if mode == "api_only":
                    raise OPFIngestionError("Official metadata API is unavailable") from exc
                if fallback_mode == "none":
                    raise OPFIngestionError("Official metadata API is unavailable and sidecar fallback is disabled") from exc
                metadata_sidecar, cover_sidecar = write_sidecars(book_path, document.metadata, document.cover_path)
                sidecar_result, fallback_reason = await self._import_or_defer_sidecar(
                    identity["book_id"], bearer_token, persistence_safe,
                )
                fallback_reason = fallback_reason or "metadata_api_unavailable"
                result_status = "partial" if document.cover_path or sidecar_result != "imported" else "success"

        after_hash = file_fingerprint(book_path)
        if before_hash != after_hash:
            raise OPFSourceMutationError("Original book file changed during OPF ingestion")
        for source_path, expected_hash in input_fingerprints.items():
            if file_fingerprint(source_path) != expected_hash:
                raise OPFSourceMutationError("OPF metadata or cover source changed during ingestion")

        # Partial results must remain retryable (notably sidecar covers, which
        # Grimmory's sidecar importer does not apply).
        success = result_status == "success" and sidecar_result != "pending_import"
        await self.state_store.put(
            source_opf_path=source_key,
            source_fingerprint=source_hash,
            cover_fingerprint=cover_hash,
            book_id=identity["book_id"],
            metadata_hash=metadata_hash,
            delivery_mode=mode_fingerprint,
            api_result=api_result,
            sidecar_result=sidecar_result,
            result_status=result_status,
            fallback_reason=fallback_reason,
            success=success,
        )
        return IngestionResult(
            status=result_status,
            book_id=identity["book_id"],
            metadata_hash=metadata_hash,
            primary_action="sidecar" if sidecar_result and not api_result else "official_api",
            fallback_reason=fallback_reason,
            sidecar_metadata_path=metadata_sidecar,
            sidecar_cover_path=cover_sidecar,
            cover_status="sidecar_pending" if document.cover_path else None,
        )

    async def _resolve_identity(
        self, book_path: Path, bearer_token: str, requested_book_id: int | None,
    ) -> tuple[dict[str, int], dict[str, Any]]:
        fingerprint = grimmory_file_fingerprint(book_path)
        try:
            candidates = await self.identity_lookup.lookup_current_hash(fingerprint)
            if not candidates:
                candidates = await self.identity_lookup.lookup_initial_hash(fingerprint)
        except GrimmoryIdentityLookupError as exc:
            raise OPFIdentityError("Read-only exact hash lookup is unavailable") from exc
        if len(candidates) != 1:
            reason = "ambiguous" if candidates else "no exact hash match"
            raise OPFIdentityError(f"Cannot resolve OPF target: {reason}")
        candidate = candidates[0]
        if requested_book_id is not None and requested_book_id != candidate.book_id:
            raise OPFIdentityError("Provided book ID does not match the file hash identity")
        try:
            detail = await self.official_client.get_book_by_id(candidate.book_id, bearer_token)
        except (OfficialAuthError, OfficialPermissionError) as exc:
            raise OPFAuthorizationError("Official book access verification failed") from exc
        except OfficialClientError as exc:
            raise OPFIdentityError("Official book identity verification failed") from exc
        if not isinstance(detail, dict) or detail.get("id") != candidate.book_id:
            raise OPFIdentityError("Official returned a different book identity")
        files = [detail.get("primaryFile"), *(detail.get("alternativeFormats") or [])]
        matching_file = next(
            (item for item in files if isinstance(item, dict) and item.get("id") == candidate.book_file_id),
            None,
        )
        if not matching_file or matching_file.get("fileName") != book_path.name:
            raise OPFIdentityError("Resolved Grimmory file does not match the OPF target path")
        return (
            {"book_id": candidate.book_id, "book_file_id": candidate.book_file_id},
            detail,
        )

    async def _source_file_writes_disabled(self, bearer_token: str) -> bool:
        try:
            app_settings = await self.official_client.get_app_settings(bearer_token)
        except OfficialClientError:
            # A local sidecar remains safe, but no Official write may proceed
            # until persistence settings are positively known.
            return False
        persistence = app_settings.get("metadataPersistenceSettings")
        if not isinstance(persistence, dict):
            return False
        save_to_file = persistence.get("saveToOriginalFile")
        if not isinstance(save_to_file, dict):
            return False
        if persistence.get("moveFilesToLibraryPattern") is not False:
            return False
        formats = ("epub", "pdf", "cbx", "audiobook")
        for fmt in formats:
            entry = save_to_file.get(fmt)
            if not isinstance(entry, dict) or entry.get("enabled") is not False:
                return False
        return True

    @staticmethod
    def _book_field_locks(book_detail: dict[str, Any]) -> dict[str, bool]:
        metadata = book_detail.get("metadata")
        if not isinstance(metadata, dict):
            raise OPFFieldLockError("Official book response did not include metadata lock state")
        if metadata.get("allMetadataLocked") is True:
            return {"*": True}
        mapping = {
            "title": "titleLocked", "subtitle": "subtitleLocked", "authors": "authorsLocked",
            "publisher": "publisherLocked", "publishedDate": "publishedDateLocked",
            "description": "descriptionLocked", "language": "languageLocked",
            "categories": "categoriesLocked", "isbn10": "isbn10Locked", "isbn13": "isbn13Locked",
            "seriesName": "seriesNameLocked", "seriesNumber": "seriesNumberLocked",
            "seriesTotal": "seriesTotalLocked",
        }
        return {field: metadata.get(lock_name) is True for field, lock_name in mapping.items()}

    async def _import_or_defer_sidecar(
        self, book_id: int, bearer_token: str, persistence_safe: bool,
    ) -> tuple[str, str | None]:
        if not persistence_safe:
            return "pending_import", "official_file_persistence_enabled"
        try:
            await self.official_client.import_book_sidecar(book_id, bearer_token)
        except (OfficialAuthError, OfficialPermissionError) as exc:
            raise OPFAuthorizationError("Official rejected sidecar import access") from exc
        except (OfficialTimeoutError, OfficialTransportError, OfficialBadResponseError):
            return "pending_import", "sidecar_import_unavailable"
        return "imported", None


def _is_field_incompatibility(exc: OfficialBadResponseError) -> bool:
    """Only treat explicit unsupported/unknown-field 422 responses as fallbackable."""
    if exc.status_code != 422 or not exc.response_body:
        return False
    body = exc.response_body.lower()
    return any(marker in body for marker in ("unsupported field", "unknown field", "unrecognized field"))
