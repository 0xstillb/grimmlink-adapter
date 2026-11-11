"""Single-worker durable Auto/Manual metadata runs for mounted NAS libraries."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import Iterable
from contextlib import suppress
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.automation import LibraryPolicy
from grimmlink_adapter.official.auth import parse_jwt_expiry
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import (
    OfficialAuthError,
    OfficialBadResponseError,
    OfficialPermissionError,
    OfficialTimeoutError,
    OfficialTransportError,
)
from grimmlink_adapter.opf.parser import OPFError, file_fingerprint
from grimmlink_adapter.opf.sidecar import InvalidMetadataSidecarError
from grimmlink_adapter.security.account_locks import credential_refresh_lock
from grimmlink_adapter.security.metadata_vault import metadata_cipher
from grimmlink_adapter.services.metadata_planner import (
    BOOK_SUFFIXES,
    equivalent,
    field_plan,
    file_stamp,
    read_source,
    source_candidates,
)
from grimmlink_adapter.services.opf_ingestion import OPFIngestionService
from grimmlink_adapter.state.linked_accounts import (
    LinkedAccount,
    LinkedAccountStore,
    digest_md5_key,
)
from grimmlink_adapter.state.metadata_automation import MetadataAutomationStore as Store

logger = logging.getLogger(__name__)
# Shared with the existing explicit per-file manual endpoints.
metadata_execution_lock = asyncio.Lock()
_TRANSIENT_UPSTREAM_STATUSES = {502, 503, 504}


def _is_transient_upstream_error(exc: Exception) -> bool:
    return isinstance(exc, (OfficialTransportError, OfficialTimeoutError)) or (
        isinstance(exc, OfficialBadResponseError) and exc.status_code in _TRANSIENT_UPSTREAM_STATUSES
    )


def _upstream_retry_delay(attempts: int) -> int:
    """Bounded exponential delay for upstream outages: 30s to 15m."""
    return int(min(900, 30 * 2 ** min(max(attempts, 0), 5)))


class MetadataBlocked(Exception):
    """Safe operator-visible reason code, without exception bodies or tokens."""


def server_key() -> str:
    return settings.GRIMMORY_BASE_URL.rstrip("/")


def configured_root(index: int) -> Path:
    if index < 0 or index >= len(settings.METADATA_IMPORT_ROOTS):
        raise MetadataBlocked("root_not_configured")
    root = Path(settings.METADATA_IMPORT_ROOTS[index]).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise MetadataBlocked("storage_unavailable")
    return root


def check_storage(policy: LibraryPolicy) -> dict[int, Path]:
    roots: dict[int, Path] = {}
    for mapping in policy.mappings:
        root = configured_root(mapping.rootIndex)
        if mapping.rootPath is not None and mapping.rootPath != settings.METADATA_IMPORT_ROOTS[mapping.rootIndex]:
            raise MetadataBlocked("configured_roots_changed_remap_library")
        marker = Path(mapping.marker)
        if marker.is_absolute() or ".." in marker.parts:
            raise MetadataBlocked("invalid_storage_marker")
        candidate = (root / marker).resolve(strict=True)
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise MetadataBlocked("storage_unavailable")
        # Read one byte: existence of a stale mount point is not enough.
        with candidate.open("rb") as stream:
            stream.read(1)
        mapped_root(root, mapping.localSubpath)
        roots[mapping.rootIndex] = root
    return roots


def mapped_root(root: Path, subpath: str | None) -> Path:
    if subpath is None:
        raise MetadataBlocked("library_folder_not_configured")
    relative = Path(subpath)
    if relative.is_absolute() or ".." in relative.parts:
        raise MetadataBlocked("invalid_library_folder")
    try:
        folder = (root / relative).resolve(strict=True)
    except OSError as exc:
        raise MetadataBlocked("library_folder_unavailable") from exc
    if not folder.is_relative_to(root) or not folder.is_dir():
        raise MetadataBlocked("invalid_library_folder")
    return folder


def _library_path_id(file: dict[str, Any], book: dict[str, Any], library: dict[str, Any]) -> int | None:
    library_path = book.get("libraryPath")
    path_id = library_path.get("id") if isinstance(library_path, dict) else None
    if isinstance(path_id, int) and not isinstance(path_id, bool):
        return path_id
    explicit_id = book.get("libraryPathId")
    if isinstance(explicit_id, int) and not isinstance(explicit_id, bool):
        return explicit_id

    raw_file_path = file.get("filePath")
    if not isinstance(raw_file_path, str):
        return None
    file_path = PurePosixPath(raw_file_path)
    if ".." in file_path.parts:
        return None
    matches: list[tuple[int, int]] = []
    for candidate in library.get("paths", []):
        if not isinstance(candidate, dict) or type(candidate.get("id")) is not int:
            continue
        raw_root = candidate.get("path")
        if not isinstance(raw_root, str):
            continue
        root = PurePosixPath(raw_root)
        try:
            relative = file_path.relative_to(root)
        except ValueError:
            continue
        if relative.parts:
            matches.append((len(root.parts), candidate["id"]))
    if not matches:
        return None
    longest = max(length for length, _ in matches)
    ids = {path_id for length, path_id in matches if length == longest}
    return next(iter(ids)) if len(ids) == 1 else None


def mapped_book(file: dict[str, Any], book: dict[str, Any], library: dict[str, Any], policy: LibraryPolicy, roots: dict[int, Path]) -> tuple[Path, Path]:
    library_path_id = _library_path_id(file, book, library)
    mappings = [m for m in policy.mappings if m.libraryPathId == library_path_id]
    paths = [p for p in library.get("paths", []) if p.get("id") == library_path_id]
    if len(mappings) != 1 or len(paths) != 1:
        raise MetadataBlocked("library_path_not_mapped")
    upstream_root = PurePosixPath(paths[0]["path"])
    upstream_file = PurePosixPath(file["filePath"])
    relative = upstream_file.relative_to(upstream_root)
    if ".." in relative.parts or not relative.parts:
        raise MetadataBlocked("file_path_escape")
    root = mapped_root(roots[mappings[0].rootIndex], mappings[0].localSubpath)
    return root, root.joinpath(*relative.parts)


def local_book(file: dict[str, Any], book: dict[str, Any], library: dict[str, Any], policy: LibraryPolicy, roots: dict[int, Path]) -> Path:
    root, path = mapped_book(file, book, library, policy, roots)
    candidate = path.resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file() or candidate.name != file.get("fileName"):
        raise MetadataBlocked("file_identity_mismatch")
    return candidate


def inspect_sources(
    book: dict[str, Any], library: dict[str, Any], policy: LibraryPolicy, roots: dict[int, Path] | None = None,
) -> dict[str, Any]:
    """Read small source artifacts; whole-book hashing is reserved for actual writes."""
    if roots is None:
        roots = check_storage(policy)
    found: list[dict[str, Any]] = []
    for file in [book.get("primaryFile"), *(book.get("alternativeFormats") or [])]:
        if not isinstance(file, dict) or file.get("folderBased"):
            continue
        path = local_book(file, book, library, policy, roots)
        candidates = source_candidates(path)
        if not candidates:
            continue
        groups: tuple[list[Path], ...] = (candidates,)
        if policy.sourcePreference == "opf":
            opf_candidates = [p for p in candidates if p.suffix.lower() == ".opf"]
            json_candidates = [p for p in candidates if p.name.lower().endswith(".metadata.json")]
            # Use one deterministic OPF, then the first JSON sidecar only when
            # that OPF is absent or malformed.
            groups = ([opf_candidates[0]] if opf_candidates else [], json_candidates[:1])
        elif policy.sourcePreference == "json":
            groups = ([p for p in candidates if p.name.lower().endswith(".metadata.json")][:1],
                      [p for p in candidates if p.suffix.lower() == ".opf"][:1])
        stat = file_stamp(path.stat())
        for group in groups:
            count = len(found)
            for source in group:
                try:
                    parsed = read_source(source)
                except (OPFError, InvalidMetadataSidecarError):
                    # A malformed preferred source should not block the
                    # configured fallback format.
                    continue
                if parsed is None:
                    continue
                document, revision = parsed
                payload = document.metadata.official_payload(book["id"])["metadata"]
                payload.pop("bookId", None)
                cover = document.cover_path
                if cover and (not cover.is_file() or not cover.resolve().is_relative_to(path.parent)):
                    raise MetadataBlocked("cover_path_escape")
                # The worker never uploads covers, so checking their path and stat suffices.
                cover_stamp = [str(cover), file_stamp(cover.stat())] if cover else None
                found.append({"file_id": file["id"], "book_path": str(path), "source_path": str(source),
                              "source": payload, "cover": bool(cover),
                              "signature": [file["id"], str(path), str(source), revision, stat, cover_stamp]})
            if len(found) > count:
                break
    if not found:
        return {"state": "no_source"}
    first = found[0]
    if any(item["source"] != first["source"] for item in found[1:]):
        return {"state": "needs_review", "reason": "multiple_metadata_sources"}
    first["state"] = "ready"
    first["revision"] = hashlib.sha256(json.dumps(first["signature"], ensure_ascii=False).encode()).hexdigest()
    return first


def remember_book_paths(
    known: set[Path], books: Iterable[dict[str, Any]], library: dict[str, Any], policy: LibraryPolicy, roots: dict[int, Path],
) -> None:
    for book in books:
        for file in [book.get("primaryFile"), *(book.get("alternativeFormats") or [])]:
            if isinstance(file, dict) and not file.get("folderBased"):
                with suppress(ValueError, KeyError, MetadataBlocked):
                    _, path = mapped_book(file, book, library, policy, roots)
                    known.add(path)


def pending_sources(known: set[Path], roots: dict[int, Path], policy: LibraryPolicy) -> list[str]:
    """Report adjacent sources whose book files have not entered the library yet."""
    pending: list[str] = []
    folders = {mapped_root(roots[mapping.rootIndex], mapping.localSubpath) for mapping in policy.mappings}
    for root in folders:
        for directory, dirs, names in os.walk(root, followlinks=False):
            parent = Path(directory)
            dirs[:] = [name for name in dirs if not (parent / name).is_symlink()]
            for name in names:
                target = parent / name
                if target.suffix.lower() not in BOOK_SUFFIXES or target in known or target.is_symlink():
                    continue
                try:
                    has_source = any(source.suffix.lower() == ".opf" or read_source(source) is not None
                                     for source in source_candidates(target))
                except (OSError, ValueError):
                    continue
                if has_source:
                    pending.append(str(target.relative_to(root)))
                    if len(pending) >= 100:
                        return pending
    return pending


class MetadataAutomation:
    def __init__(self) -> None:
        self.task: asyncio.Task[None] | None = None
        self.wake = asyncio.Event()

    async def start(self) -> None:
        self.wake = asyncio.Event()
        # Single worker/replica: interrupted writes are reconciled through intent + readback.
        await Store.execute("UPDATE metadata_runs SET state='queued' WHERE state='running'")
        if settings.METADATA_WORKER_ENABLED:
            self.task = asyncio.create_task(self.loop(), name="metadata-worker")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def bind_account(
        self, user_id: int, bearer: str, *, username: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        linked = None
        if not username:
            linked = await LinkedAccountStore.get(server_key(), str(user_id))
            if not linked or not linked.refresh_token:
                raise MetadataBlocked("link_grimmory_account_with_refresh_token_first")
            username, refresh_token = linked.username, linked.refresh_token
        if not refresh_token:
            raise MetadataBlocked("grimmory_refresh_token_required_for_background_sync")
        async with OfficialGrimmoryClient() as client:
            profile = await client.get_current_user(bearer)
            if str(profile.get("id", profile.get("userId"))) != str(user_id):
                raise MetadataBlocked("account_identity_mismatch")
        payload = {"username": username, "access": bearer, "refresh": refresh_token,
                   "source": "direct" if linked is None else "linked"}
        encrypted = metadata_cipher().encrypt(json.dumps(payload).encode()).decode()
        await Store.execute("INSERT INTO metadata_accounts(server,user_id,encrypted_credentials) VALUES(?,?,?) ON CONFLICT(server,user_id) DO UPDATE SET encrypted_credentials=excluded.encrypted_credentials,blocked_reason=NULL", (server_key(), user_id, encrypted))

    async def link_reader_account(
        self, username: str, md5_key: str, *, expected_user_id: int | None = None,
    ) -> int | None:
        """Bind a verified KOReader key to a matching Metadata account without a CLI step.

        Callers must first verify the key with Grimmory's KOReader auth endpoint.
        The already-bound Metadata JWT then supplies the numeric identity that the
        stock KOReader auth response omits. Only an exact username/profile match
        is accepted.
        """
        rows = await Store.query(
            "SELECT user_id,encrypted_credentials FROM metadata_accounts WHERE server=? AND blocked_reason IS NULL",
            (server_key(),),
        )
        candidates: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            try:
                data = json.loads(metadata_cipher().decrypt(row["encrypted_credentials"].encode()))
            except Exception:
                continue
            user_id = int(row["user_id"])
            if data.get("username") != username:
                continue
            if expected_user_id is not None and user_id != expected_user_id:
                continue
            candidates.append((user_id, data))
        if len(candidates) != 1:
            return None

        user_id, _ = candidates[0]
        access = await self.bearer(user_id)
        async with OfficialGrimmoryClient() as client:
            profile = await client.get_current_user(access)
        profile_id = profile.get("id", profile.get("userId"))
        if str(profile_id) != str(user_id) or profile.get("username") != username:
            return None

        # bearer() may have rotated the token, so reload its durable credentials
        # after profile verification and make Reader and Metadata share one
        # refresh-token chain.
        current = await Store.query(
            "SELECT encrypted_credentials FROM metadata_accounts WHERE server=? AND user_id=?",
            (server_key(), user_id),
        )
        if len(current) != 1:
            return None
        data = json.loads(metadata_cipher().decrypt(current[0]["encrypted_credentials"].encode()))
        if data.get("username") != username or data.get("access") != access:
            return None
        data["source"] = "linked"
        encrypted = metadata_cipher().encrypt(json.dumps(data).encode()).decode()
        await Store.execute(
            "UPDATE metadata_accounts SET encrypted_credentials=? WHERE server=? AND user_id=?",
            (encrypted, server_key(), user_id),
        )
        await LinkedAccountStore.put(LinkedAccount(
            server=server_key(),
            user_id=str(user_id),
            username=username,
            md5_key_digest=digest_md5_key(md5_key),
            access_token=access,
            refresh_token=data.get("refresh"),
        ))
        logger.info("Linked verified KOReader credentials to existing Metadata account for user %s", user_id)
        return user_id

    async def bearer(self, user_id: int) -> str:
        async with credential_refresh_lock:
            return await self._bearer(user_id)

    async def _bearer(self, user_id: int) -> str:
        rows = await Store.query("SELECT encrypted_credentials,blocked_reason FROM metadata_accounts WHERE server=? AND user_id=?", (server_key(), user_id))
        if not rows:
            raise MetadataBlocked("background_account_not_connected")
        if rows[0]["blocked_reason"]:
            raise MetadataBlocked("background_account_reconnect_required")
        try:
            saved = json.loads(metadata_cipher().decrypt(rows[0]["encrypted_credentials"].encode()))
        except Exception as exc:
            raise MetadataBlocked("background_credential_key_unavailable") from exc
        data = saved.copy()
        linked = await LinkedAccountStore.get(server_key(), str(user_id)) if saved.get("source") != "direct" else None
        if linked:
            data.update(access=linked.access_token, refresh=linked.refresh_token, username=linked.username)
        async with OfficialGrimmoryClient() as client:
            client.set_jwt_credentials(username=data["username"], access_token=data["access"], refresh_token=data["refresh"])
            expiry = parse_jwt_expiry(data["access"])
            needs_refresh = expiry is not None and expiry < time.time() + 60
            try:
                if needs_refresh:
                    raise OfficialAuthError("Expired")
                profile = await client.get_current_user(data["access"])
            except (OfficialAuthError, OfficialPermissionError):
                if not data.get("refresh"):
                    raise MetadataBlocked("background_account_reconnect_required") from None
                refreshed = await client.refresh_token()
                data.update(access=refreshed.token, refresh=refreshed.refreshToken or data["refresh"])
                profile = await client.get_current_user(data["access"])
                if linked:
                    await LinkedAccountStore.put(LinkedAccount(server_key(), str(user_id), linked.username, linked.md5_key_digest, data["access"], data["refresh"]))
            if str(profile.get("id", profile.get("userId"))) != str(user_id):
                raise MetadataBlocked("account_identity_mismatch")
        if data != saved:
            encrypted = metadata_cipher().encrypt(json.dumps(data).encode()).decode()
            await Store.execute("UPDATE metadata_accounts SET encrypted_credentials=? WHERE server=? AND user_id=?", (encrypted, server_key(), user_id))
        return str(data["access"])

    async def loop(self) -> None:
        while True:
            self.wake.clear()
            worked = False
            try:
                worked = await self.tick()
            except Exception as exc:
                logger.error("Metadata worker error (%s)", type(exc).__name__)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=0.25 if worked else 5)

    async def tick(self) -> bool:
        now = time.time()
        libraries = await Store.query(
            """SELECT library.* FROM metadata_libraries AS library
               JOIN metadata_accounts AS account ON account.server=library.server AND account.user_id=library.owner_id
               WHERE library.server=? AND library.next_due<=? AND account.blocked_reason IS NULL ORDER BY library.next_due""",
            (server_key(), now),
        )
        for row in libraries:
            policy = LibraryPolicy.model_validate_json(row["config"])
            if policy.autoEnabled:
                await Store.enqueue(server_key(), row["library_id"], row["owner_id"], "check", "auto")
                await Store.execute("UPDATE metadata_libraries SET next_due=? WHERE server=? AND library_id=?", (now + policy.intervalSeconds, server_key(), row["library_id"]))
        runs = await Store.query("SELECT * FROM metadata_runs WHERE server=? AND state='queued' AND available_at<=? ORDER BY CASE WHEN requested_by='manual' THEN 0 ELSE 1 END, updated_at LIMIT 1", (server_key(), now))
        if runs:
            await self.run(runs[0])
        return bool(runs)

    async def progress(self, run_id: str, result: dict[str, Any], state: str = "running") -> None:
        await Store.execute(
            """UPDATE metadata_runs SET state=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE ? END,
               result=?,updated_at=? WHERE id=?""",
            (state, json.dumps(result, ensure_ascii=False), time.time(), run_id),
        )

    async def retry_run(self, run_id: str, result: dict[str, Any], delay: int, *, rediscover: bool) -> None:
        retry_at = time.time() + delay
        result["retryAt"] = retry_at
        if rediscover:
            result["phase"] = "waiting_retry"
            await Store.execute("DELETE FROM metadata_run_books WHERE run_id=?", (run_id,))
        await Store.execute(
            """UPDATE metadata_runs SET state=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END,
               result=?,available_at=?,attempts=attempts+1,updated_at=? WHERE id=?""",
            (json.dumps(result, ensure_ascii=False), retry_at, time.time(), run_id),
        )

    async def cancelled(self, run_id: str) -> bool:
        rows = await Store.query("SELECT cancel_requested FROM metadata_runs WHERE id=?", (run_id,))
        return bool(rows and rows[0]["cancel_requested"])

    async def run(self, run: dict[str, Any]) -> None:
        prior = json.loads(run["result"])
        # Older workers stored the complete ID list in progress. Rebuild it once in the new table.
        resume = prior.get("phase") == "processing" and "_bookIds" not in prior
        result: dict[str, Any] = prior if resume else {"checked": 0, "counts": {}, "phase": "discovery"}
        result.pop("retryAt", None)
        result.pop("reason", None)
        await self.progress(run["id"], result)
        try:
            if await self.cancelled(run["id"]):
                await self.progress(run["id"], result, "cancelled")
                return
            rows = await Store.query("SELECT * FROM metadata_libraries WHERE server=? AND library_id=? AND owner_id=?", (server_key(), run["library_id"], run["owner_id"]))
            if not rows:
                raise MetadataBlocked("library_policy_missing")
            row = rows[0]
            policy = LibraryPolicy.model_validate_json(row["config"])
            roots = await asyncio.to_thread(check_storage, policy)
            bearer = await self.bearer(run["owner_id"])
            verified_at = time.monotonic()
            async with OfficialGrimmoryClient() as client:
                library = await client.get_library(run["library_id"], bearer)
                if not resume:
                    await Store.execute("DELETE FROM metadata_run_books WHERE run_id=?", (run["id"],))
                    known: set[Path] = set()
                    total = 0
                    async for page in client.iter_library_book_pages(run["library_id"], bearer):
                        if await self.cancelled(run["id"]):
                            await self.progress(run["id"], result, "cancelled")
                            return
                        remember_book_paths(known, page, library, policy, roots)
                        await Store.add_book_page(run["id"], total, page)
                        total += len(page)
                    result["waitingForLibrary"] = await asyncio.to_thread(pending_sources, known, roots, policy)
                    del known
                    result.update(total=total, phase="processing", position=0)
                    await self.progress(run["id"], result)
                batch = await Store.book_batch(run["id"], result["position"], 20)
                if len(batch) != min(20, result["total"] - result["position"]):
                    raise MetadataBlocked("library_snapshot_changed")
                for book in batch:
                    if await self.cancelled(run["id"]):
                        await self.progress(run["id"], result, "cancelled")
                        return
                    if time.monotonic() - verified_at >= 30:
                        bearer = await self.bearer(run["owner_id"])
                        verified_at = time.monotonic()
                    try:
                        async with metadata_execution_lock:
                            state = await self.process(client, bearer, row, policy, library, book, run["mode"], run["id"], roots=roots)
                    except (OfficialAuthError, OfficialPermissionError):
                        raise
                    except Exception as exc:
                        if _is_transient_upstream_error(exc):
                            # Preserve the current book position and retry the same durable run.
                            # process() stores a before/after intent before metadata writes and
                            # reconciles it against a fresh upstream read before another write.
                            raise
                        if isinstance(exc, OSError):
                            # A missing book may be transient even though the NAS is still online.
                            await asyncio.to_thread(check_storage, policy)
                        state = "blocked" if isinstance(exc, MetadataBlocked) else "failed"
                        item = await Store.item(server_key(), row["library_id"], book["id"])
                        item["result"] = {"state": state, "reason": str(exc) if isinstance(exc, MetadataBlocked) else type(exc).__name__, "title": book.get("title"), "book_id": book["id"]}
                        await Store.save_item(server_key(), row["library_id"], book["id"], item.get("file_id", 0), item)
                    result["checked"] += 1
                    result["position"] += 1
                    result["counts"][state] = result["counts"].get(state, 0) + 1
                    if state == "no_source" and len(result.setdefault("missingMetadata", [])) < 5:
                        result["missingMetadata"].append({"bookId": book["id"], "title": book.get("title")})
                    if run["requested_by"] != "auto":
                        await self.progress(run["id"], result)
                    if run["requested_by"] == "auto" and self.wake.is_set() and result["position"] < result["total"]:
                        manual = await Store.query(
                            "SELECT id FROM metadata_runs WHERE server=? AND state='queued' AND requested_by='manual' AND available_at<=? LIMIT 1",
                            (server_key(), time.time()),
                        )
                        if manual:
                            await self.progress(run["id"], result, "queued")
                            return
                if result["position"] < result["total"]:
                    await self.progress(run["id"], result, "queued")
                    return  # Yield to other libraries after twenty books.
            result["phase"] = "done"
            retryable = any(result["counts"].get(key, 0) for key in ("waiting_stable", "retry_wait", "failed"))
            if (retryable and run["attempts"] < 4) or result.get("waitingForLibrary"):
                delay = max(policy.settleSeconds, min(900, 30 * 2 ** min(run["attempts"], 5)))
                await self.retry_run(run["id"], result, delay, rediscover=True)
            else:
                await self.progress(run["id"], result, "completed")
        except (OfficialAuthError, OfficialPermissionError) as exc:
            if exc.status_code == 403:
                result["reason"] = "library_or_metadata_permission_denied"
            else:
                result["reason"] = "background_account_reconnect_required"
                await Store.execute("UPDATE metadata_accounts SET blocked_reason=? WHERE server=? AND user_id=?", (result["reason"], server_key(), run["owner_id"]))
            await self.progress(run["id"], result, "blocked")
        except Exception as exc:
            if _is_transient_upstream_error(exc):
                result["reason"] = "grimmory_unavailable"
                delay = _upstream_retry_delay(run["attempts"])
                logger.warning(
                    "Metadata run %s paused while Grimmory is unavailable; retrying in %ds (attempt %d)",
                    run["id"], delay, run["attempts"] + 1,
                )
                await self.retry_run(run["id"], result, delay, rediscover=result["phase"] != "processing")
            elif isinstance(exc, OSError):
                result["reason"] = "storage_unavailable"
                delay = _upstream_retry_delay(run["attempts"])
                await self.retry_run(run["id"], result, delay, rediscover=result["phase"] != "processing")
            else:
                result["reason"] = str(exc) if isinstance(exc, MetadataBlocked) else type(exc).__name__
                await self.progress(run["id"], result, "blocked" if isinstance(exc, MetadataBlocked) else "failed")
        finally:
            latest = await Store.query("SELECT followup,cancel_requested,state FROM metadata_runs WHERE id=?", (run["id"],))
            if latest and latest[0]["followup"] and not latest[0]["cancel_requested"] and latest[0]["state"] not in ("queued", "running"):
                await Store.enqueue(server_key(), run["library_id"], run["owner_id"], run["mode"], "followup")

    async def process(
        self, client: OfficialGrimmoryClient, bearer: str, row: dict[str, Any], policy: LibraryPolicy,
        library: dict[str, Any], book: dict[str, Any], mode: str, run_id: str | None = None,
        *, roots: dict[int, Path] | None = None,
    ) -> str:
        library_id, book_id = row["library_id"], book["id"]
        item = await Store.item(server_key(), library_id, book_id)
        previous_result = item["result"]
        selected = await asyncio.to_thread(inspect_sources, book, library, policy, roots)
        result: dict[str, Any] = {"state": selected["state"], "book_id": book_id, "title": book.get("title"), "cover": "pending" if selected.get("cover") else "none"}
        item["result"] = result
        file_id = selected.get("file_id", item.get("file_id", 0))

        async def finish(state: str) -> str:
            result["state"] = state
            if state != "no_source" or previous_result != result:
                await Store.save_item(server_key(), library_id, book_id, file_id, item)
            return state

        if selected["state"] != "ready":
            result.update(reason=selected.get("reason"))
            return await finish(selected["state"])
        result.update(source=Path(selected["source_path"]).name, revision=selected["revision"], file_id=file_id)
        observation = item["observation"]
        if observation.get("signature") != selected["signature"]:
            observation.update(signature=selected["signature"], stable_since=time.time())
            return await finish("waiting_stable")
        if time.time() - observation.get("stable_since", time.time()) < policy.settleSeconds:
            return await finish("waiting_stable")
        policy_hash = hashlib.sha256(policy.model_dump_json().encode()).hexdigest()
        if (mode == "check" and previous_result.get("revision") == selected["revision"]
                and previous_result.get("state") in ("updated", "in_sync", "skipped", "backfill_required")
                and observation.get("policy_hash") == policy_hash
                and time.time() - observation.get("verified_at", 0) < 1800):
            item["result"] = previous_result
            state = str(previous_result["state"])
            return "in_sync" if state == "updated" else state
        service = OPFIngestionService(official_client=client)
        identity, detail = await service._resolve_identity(Path(selected["book_path"]), bearer, book_id)
        if detail.get("libraryId") != library_id:
            raise MetadataBlocked("book_moved_to_another_library")
        if identity["book_file_id"] != file_id:
            raise MetadataBlocked("file_identity_mismatch")
        # Browsing summaries may omit addedOn. Use the verified detail for enrollment.
        if not observation.get("adopted") and mode not in ("backfill_missing", "backfill_source", "preview"):
            try:
                added = datetime.fromisoformat(detail["addedOn"].replace("Z", "+00:00")).timestamp()
            except (KeyError, ValueError, TypeError, AttributeError):
                added = 0
            if added < row["enrolled_at"]:
                observation.update(policy_hash=policy_hash, verified_at=time.time())
                return await finish("backfill_required")
        current = detail.get("metadata")
        if not isinstance(current, dict):
            raise MetadataBlocked("metadata_lock_state_unavailable")
        baseline = item["baseline"]
        # Recover a request that succeeded upstream before this process stopped.
        for field, intent in item["intent"].items():
            if equivalent(field, current.get(field), intent["after"]):
                baseline[field] = current.get(field)
            elif not equivalent(field, current.get(field), intent["before"]):
                baseline[field] = intent["before"]
        item["intent"] = {}
        plan = field_plan(selected["source"], current, baseline, policy.fields, mode)
        resolutions = observation.get("resolutions", {})
        if resolutions.get("revision") == selected["revision"]:
            for field, resolution in resolutions.get("fields", {}).items():
                # Explicit source choice applies only to the exact remote value reviewed.
                if (field in plan["conflicts"] or field in plan["changes"]) and equivalent(field, current.get(field), resolution["before"]):
                    if resolution["choice"] == "source":
                        plan["changes"][field] = selected["source"][field]
                    else:
                        baseline[field] = current.get(field)
                        plan["changes"].pop(field, None)
                        plan["skipped"][field] = "kept_grimmory"
                    plan["conflicts"].pop(field, None)
        result.update(plan)
        result["before"] = {field: current.get(field) for field in plan["changes"]}
        if mode == "preview":
            return await finish("preview")
        for field, value in selected["source"].items():
            if field in policy.fields and equivalent(field, current.get(field), value):
                baseline[field] = value
        if not plan["changes"]:
            observation["adopted"] = True
            observation.update(policy_hash=policy_hash, verified_at=time.time())
            return await finish("needs_review" if plan["conflicts"] else "skipped" if plan["skipped"] else "in_sync")
        if not await service._source_file_writes_disabled(bearer):
            raise MetadataBlocked("disable_grimmory_file_persistence_and_moves_or_grant_settings_read")
        fresh_roots = await asyncio.to_thread(check_storage, policy)
        book_path = Path(selected["book_path"])
        before_book = await asyncio.to_thread(file_fingerprint, book_path)
        recheck = await asyncio.to_thread(inspect_sources, detail, library, policy, fresh_roots)
        if recheck.get("revision") != selected["revision"]:
            return await finish("waiting_stable")
        identity, fresh = await service._resolve_identity(book_path, bearer, book_id)
        if identity["book_file_id"] != file_id:
            raise MetadataBlocked("file_identity_mismatch")
        if fresh.get("libraryId") != library_id or fresh.get("metadata") != current:
            return await finish("retry_wait")
        if any(fresh.get(key) != detail.get(key) for key in ("libraryPath", "primaryFile", "alternativeFormats")):
            return await finish("retry_wait")
        latest_policy = await Store.query("SELECT config FROM metadata_libraries WHERE server=? AND library_id=?", (server_key(), library_id))
        if not latest_policy or latest_policy[0]["config"] != row["config"]:
            raise MetadataBlocked("library_policy_changed_run_again")
        if run_id and await self.cancelled(run_id):
            return await finish("skipped_cancelled")
        # Persist before writing. No blind replay after a timeout/restart.
        item["intent"] = {field: {"before": current.get(field), "after": value} for field, value in plan["changes"].items()}
        result["state"] = "applying"
        await Store.save_item(server_key(), library_id, book_id, file_id, item)
        await client.update_book_metadata(book_id, bearer, {"metadata": {"bookId": book_id, **plan["changes"]}})
        after = await client.get_book_by_id(book_id, bearer)
        if await asyncio.to_thread(file_fingerprint, book_path) != before_book:
            raise MetadataBlocked("book_file_changed_during_metadata_update")
        if (await asyncio.to_thread(inspect_sources, after, library, policy)).get("revision") != selected["revision"]:
            raise MetadataBlocked("source_changed_during_metadata_update")
        verified: list[str] = []
        for field, value in plan["changes"].items():
            if equivalent(field, (after.get("metadata") or {}).get(field), value):
                baseline[field] = value
                verified.append(field)
        result["verified"] = verified
        observation["adopted"] = True
        observation.update(policy_hash=policy_hash, verified_at=time.time())
        # Unverified intent remains available for reconciliation after a partial result.
        item["intent"] = {field: value for field, value in item["intent"].items() if field not in verified}
        return await finish("partial" if len(verified) != len(plan["changes"]) or plan["conflicts"] or plan["skipped"] else "updated")


metadata_automation = MetadataAutomation()
