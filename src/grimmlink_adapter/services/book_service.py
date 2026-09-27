"""Book identity resolution service.

Implements authoritative Grimmory DB currentHash → initialHash when configured;
otherwise uses the scoped Adapter cache and exact upstream evidence.

Algorithm:

    resolve(server, user, hash, creds):
      0. If read-only Grimmory DB is configured, query its current_hash first,
         then initial_hash. Verify the result and never fall back to cache.
      1. rows = cache.lookup_by_current_hash(server, user, hash)
         if len(rows) > 1 → raise AmbiguousBook (never silently choose)
         if len(rows) == 1:
           if verify_accessible(rows[0].bookId, rows[0].bookFileId) → return rows[0]
           else → invalidate(rows[0]), fall through

      2. rows = cache.lookup_by_initial_hash(server, user, hash)
         if len(rows) > 1 → raise AmbiguousBook (never silently choose)
         if len(rows) == 1:
           if verify_accessible(rows[0].bookId, rows[0].bookFileId) → return rows[0]
           else → invalidate(rows[0]), fall through

      3. Stock Official progress has no bookId and cannot identify a book.
         If an upstream extension supplies a verified bookId, verify access
         and persist; otherwise raise BookResolutionUnavailableError.

Failure Modes:
    | Scenario                          | HTTP  | Behaviour                              |
    |-----------------------------------|-------|----------------------------------------|
    | Hash unknown everywhere           | 404   | Not Found                              |
    | Multiple official matches         | 409   | Conflict (ambiguous)                   |
    | Cached book deleted server-side   | 501   | Mapping invalidated; exact ID unavailable |
    | Configured Grimmory DB unavailable| 502   | Fail closed without stale cache fallback  |
    | Cached book permissions revoked   | 403   | Error surfaced to plugin               |
    | File replaced upstream            | —     | Mapping invalidated, re-resolved       |
    | Network error during verify       | 502   | Bad Gateway                            |
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from grimmlink_adapter.models.official import OfficialBookDTO
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import (
    OfficialAuthError,
    OfficialBadResponseError,
    OfficialClientError,
    OfficialPermissionError,
)
from grimmlink_adapter.official.identity_lookup import (
    GrimmoryIdentityCandidate,
    GrimmoryIdentityLookup,
    GrimmoryIdentityLookupError,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.state.book_identity import BookIdentityRow, BookIdentityStore

logger = logging.getLogger(__name__)

SUPPORTED_READ_STATUSES = ["UNREAD", "READING", "COMPLETED", "ABANDONED"]


class BookNotFoundError(Exception):
    """Raised when no book matches the given hash anywhere."""


class AmbiguousBookError(Exception):
    """Raised when multiple candidate books match — never silently choose."""

    def __init__(self, message: str, candidate_count: int = 0) -> None:
        super().__init__(message)
        self.candidate_count = candidate_count


class BookInaccessibleError(Exception):
    """Raised when a mapped book exists but the user cannot access it."""

    def __init__(self, message: str, book_id: int | None = None) -> None:
        super().__init__(message)
        self.book_id = book_id


class BookVerificationError(Exception):
    """Raised when the upstream verification call fails (network/transport)."""


class BookResolutionUnavailableError(Exception):
    """Raised when Official exposes no trustworthy hash-to-book identity evidence."""


class BookService:
    """Handles book identity resolution, lookup, and metadata summarization."""

    def __init__(
        self,
        official_client: OfficialGrimmoryClient | None = None,
        identity_lookup: GrimmoryIdentityLookup | None = None,
    ) -> None:
        self.official_client = official_client or OfficialGrimmoryClient()
        self.identity_lookup = identity_lookup or GrimmoryIdentityLookup()

    # ------------------------------------------------------------------
    # Core Resolution Algorithm
    # ------------------------------------------------------------------

    async def resolve_book_by_hash(
        self,
        *,
        server: str,
        user: str,
        book_hash: str,
        creds: ClientCredentials,
    ) -> BookIdentityRow:
        """Resolve a plugin-supplied MD5 hash to an official book identity.

        Uses Grimmory DB first when configured; otherwise uses cached currentHash
        then initialHash. Stock Official progress has no book ID on a cache miss.
        Verifies accessibility at each step; invalidates stale mappings.
        Detects ambiguity at every step and refuses to choose silently.

        Args:
            server: Upstream server base URL.
            user: Verified user ID string from upstream auth response.
            book_hash: MD5 file hash from the plugin.
            creds: Authenticated credentials (MD5 or Bearer).

        Raises:
            BookNotFoundError: Hash unknown everywhere.
            AmbiguousBookError: Multiple candidates — never silently choose.
            BookInaccessibleError: Book exists but user cannot access it.
            BookVerificationError: Network/transport failure during verification.
        """
        # A configured Grimmory DB is authoritative. Its current hash must win
        # over any older adapter cache row, including an initial-hash hit.
        if self.identity_lookup.enabled:
            db_candidate = await self._lookup_grimmory_identity(book_hash)
            if db_candidate is None:
                raise BookNotFoundError(f"No active Grimmory book matches hash {book_hash}")
            accessible = await self._verify_accessible(
                db_candidate.book_id, db_candidate.book_file_id, book_hash, creds,
            )
            if not accessible:
                raise BookNotFoundError(
                    f"The Grimmory hash mapping for {book_hash} no longer identifies its file"
                )
            return await self._persist_match(
                server, user,
                {
                    "book_id": db_candidate.book_id,
                    "book_file_id": db_candidate.book_file_id,
                    "current_hash": db_candidate.current_hash,
                    "initial_hash": db_candidate.initial_hash,
                },
                book_hash,
            )

        # --- Step 1: currentHash exact match with ambiguity detection ---
        current_rows = await BookIdentityStore.lookup_by_current_hash(server, user, book_hash)
        if len(current_rows) > 1:
            raise AmbiguousBookError(
                f"Multiple cached books share currentHash {book_hash} for {server}/{user}. "
                f"Cannot resolve ambiguously — manual intervention required.",
                candidate_count=len(current_rows),
            )
        if len(current_rows) == 1:
            row = current_rows[0]
            accessible = await self._verify_accessible(
                row.book_id, row.book_file_id, book_hash, creds,
            )
            if accessible:
                return row
            await BookIdentityStore.invalidate(server, user, row.book_id, row.book_file_id)
            logger.info(
                "Invalidated stale currentHash mapping for %s/%s hash=%s book=%s",
                server, user, book_hash, row.book_id,
            )

        # --- Step 2: initialHash fallback with ambiguity detection ---
        initial_rows = await BookIdentityStore.lookup_by_initial_hash(server, user, book_hash)
        if len(initial_rows) > 1:
            raise AmbiguousBookError(
                f"Multiple cached books share initialHash {book_hash} for {server}/{user}. "
                f"Cannot resolve ambiguously — manual intervention required.",
                candidate_count=len(initial_rows),
            )
        if len(initial_rows) == 1:
            candidate = initial_rows[0]
            accessible = await self._verify_accessible(
                candidate.book_id, candidate.book_file_id, book_hash, creds,
            )
            if accessible:
                return candidate
            await BookIdentityStore.invalidate(
                server, user, candidate.book_id, candidate.book_file_id,
            )
            logger.info(
                "Invalidated stale initialHash mapping for %s/%s hash=%s book=%s",
                server, user, book_hash, candidate.book_id,
            )

        # --- Step 3: Remote search via official API (exact hash evidence only) ---
        match = await self._search_remote_by_hash(book_hash, creds)
        if match is None:
            raise BookNotFoundError(
                f"The previously identified book for hash {book_hash} no longer exists"
            )

        identity = await self._persist_match(server, user, match, book_hash)
        return identity

    async def _lookup_grimmory_identity(
        self, book_hash: str,
    ) -> GrimmoryIdentityCandidate | None:
        """Use current_hash first and initial_hash only if current has no row."""
        try:
            candidates = await self.identity_lookup.lookup_current_hash(book_hash)
            if len(candidates) > 1:
                raise AmbiguousBookError(
                    f"Multiple Grimmory books share current_hash {book_hash}; cannot choose.",
                    candidate_count=len(candidates),
                )
            if len(candidates) == 1:
                return candidates[0]

            candidates = await self.identity_lookup.lookup_initial_hash(book_hash)
            if len(candidates) > 1:
                raise AmbiguousBookError(
                    f"Multiple Grimmory books share initial_hash {book_hash}; cannot choose.",
                    candidate_count=len(candidates),
                )
            return candidates[0] if candidates else None
        except (AmbiguousBookError, BookResolutionUnavailableError):
            raise
        except GrimmoryIdentityLookupError as exc:
            raise BookVerificationError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Verification — does the book still exist and is the user authorized?
    # ------------------------------------------------------------------

    async def _verify_accessible(
        self, book_id: int, book_file_id: int, book_hash: str, creds: ClientCredentials,
    ) -> bool:
        """Check that a book still exists and the current user can access it.

        Also verifies the file ID belongs to the primary or an alternative format.

        Returns True if accessible, False if deleted/not-found or file replaced.
        Raises BookInaccessibleError on 403, BookVerificationError on transport.
        """
        bearer = await self._get_bearer(creds)
        if bearer:
            try:
                book_data = await self.official_client.get_book_by_id(book_id, bearer)
            except OfficialPermissionError as exc:
                raise BookInaccessibleError(
                    f"Book {book_id} exists but the current user does not have access",
                    book_id=book_id,
                ) from exc
            except OfficialBadResponseError as exc:
                if exc.status_code == 404:
                    return False
                raise BookVerificationError(
                    f"Upstream error verifying book {book_id}: {exc}"
                ) from exc
            except OfficialAuthError:
                raise
            except OfficialClientError as exc:
                raise BookVerificationError(
                    f"Transport error verifying book {book_id}: {exc}"
                ) from exc

            if not isinstance(book_data, dict) or book_data.get("id") != book_id:
                raise BookVerificationError(f"Upstream returned the wrong book for {book_id}")
            if book_file_id != 0 and not _book_contains_file(book_data, book_file_id):
                logger.warning(
                    "Book %d file identity changed: cached=%d",
                    book_id, book_file_id,
                )
                return False
            return True

        # Stock KOReader progress returns reading position, not bookId or fileId.
        # MD5 authentication alone cannot verify this cached book's access.
        raise BookResolutionUnavailableError(
            "Book access cannot be verified with KOReader MD5 credentials alone"
        )

    # ------------------------------------------------------------------
    # Remote search — exact hash verification via KOReader progress
    # ------------------------------------------------------------------

    async def _search_remote_by_hash(
        self, book_hash: str, creds: ClientCredentials,
    ) -> dict[str, Any] | None:
        """Search for a book matching this exact hash via KOReader progress.

        Stock Official GET /api/koreader/syncs/progress/{bookHash} returns a
        reading-position DTO without bookId. That response cannot identify
        a book and raises BookResolutionUnavailableError. An extended response
        with a positive bookId can be verified and used; a generic book listing
        is never treated as hash evidence.
        """
        progress_result = await self._probe_koreader_progress(book_hash, creds)
        book_id_raw = progress_result.get("bookId")
        if isinstance(book_id_raw, bool) or not isinstance(book_id_raw, int) or book_id_raw <= 0:
            raise BookResolutionUnavailableError(
                "Official KOReader progress does not provide a book ID for exact hash resolution"
            )
        book_id = book_id_raw
        bearer = await self._get_bearer(creds)
        if not bearer:
            raise BookResolutionUnavailableError(
                "A Bearer token is required to verify a hash-to-book candidate"
            )

        try:
            book_data = await self.official_client.get_book_by_id(book_id, bearer)
            book = OfficialBookDTO.model_validate(book_data)
        except OfficialPermissionError as exc:
            raise BookInaccessibleError(
                f"Book {book_id} exists but the current user does not have access",
                book_id=book_id,
            ) from exc
        except OfficialBadResponseError as exc:
            if exc.status_code == 404:
                return None
            raise BookVerificationError(
                f"Failed to fetch book {book_id}: {exc}"
            ) from exc
        except OfficialClientError as exc:
            raise BookVerificationError(
                f"Transport error fetching book {book_id}: {exc}"
            ) from exc
        except ValidationError as exc:
            raise BookVerificationError(
                f"Malformed upstream book {book_id} during hash resolution"
            ) from exc

        primary_file = book.primaryFile
        return {
            "book_id": book_id,
            "book_file_id": primary_file.id if primary_file and primary_file.id else 0,
            "current_hash": progress_result.get("currentHash") or book_hash,
            "initial_hash": progress_result.get("initialHash") or book_hash,
            "title": (book.title if book else None) or progress_result.get("document"),
            "authors": _extract_authors(book) if book else [],
            "format": (primary_file.bookType or primary_file.extension) if primary_file else None,
            "file_size": (primary_file.fileSizeKb * 1024) if primary_file and primary_file.fileSizeKb else None,
            "filename": primary_file.fileName if primary_file else None,
        }

    async def _probe_koreader_progress(
        self, book_hash: str, creds: ClientCredentials,
    ) -> dict[str, Any]:
        """Read exact-hash progress only when the caller supplied KOReader credentials.

        Stock Official responses contain reading position but no book ID.
        A missing progress row therefore cannot prove that a book is absent.
        """
        if not creds.username or not creds.md5_key:
            raise BookResolutionUnavailableError(
                "Bearer-only hash lookup needs a verified adapter mapping"
            )

        try:
            result = await self.official_client.get_koreader_progress(
                book_hash, creds.username, creds.md5_key,
            )
        except OfficialAuthError:
            raise
        except OfficialPermissionError as exc:
            raise BookInaccessibleError(
                "KOReader progress access is forbidden"
            ) from exc
        except OfficialClientError as exc:
            raise BookVerificationError(
                f"Failed to read KOReader progress: {exc}"
            ) from exc

        if not isinstance(result, dict) or not result:
            raise BookResolutionUnavailableError(
                "No book ID can be inferred from missing KOReader progress"
            )
        return result

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _get_bearer(creds: ClientCredentials) -> str | None:
        """Use only the caller's Bearer token for book-level access checks."""
        return creds.bearer_token

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @staticmethod
    async def _persist_match(
        server: str,
        user: str,
        match: dict[str, Any],
        queried_hash: str,
    ) -> BookIdentityRow:
        """Persist a resolved book identity to the store."""
        current_hash: str = str(match.get("current_hash") or queried_hash)
        initial_hash: str = str(match.get("initial_hash") or queried_hash)
        book_id: int = int(match["book_id"])
        book_file_id: int = int(match.get("book_file_id") or 0)
        authors_raw = match.get("authors")
        authors: list[str] = authors_raw if isinstance(authors_raw, list) else []

        await BookIdentityStore.upsert(
            server=server,
            user=user,
            current_hash=current_hash,
            initial_hash=initial_hash,
            book_id=book_id,
            book_file_id=book_file_id,
            title=match.get("title"),
            authors=authors,
            fmt=match.get("format"),
            file_size=match.get("file_size"),
            filename=match.get("filename"),
        )

        return BookIdentityRow(
            server=server,
            user=user,
            current_hash=current_hash,
            initial_hash=initial_hash,
            book_id=book_id,
            book_file_id=book_file_id,
            title=match.get("title"),
            authors=authors,
            format=match.get("format"),
            file_size=match.get("file_size"),
            filename=match.get("filename"),
        )

    # ------------------------------------------------------------------
    # Read Statuses
    # ------------------------------------------------------------------

    def get_supported_read_statuses(self) -> list[str]:
        """Return list of supported read status strings."""
        return list(SUPPORTED_READ_STATUSES)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _extract_authors(book: OfficialBookDTO) -> list[str]:
    """Extract author names from an OfficialBookDTO's metadata."""
    if book.metadata and isinstance(book.metadata, dict):
        authors = book.metadata.get("authors")
        if isinstance(authors, list):
            result: list[str] = []
            for a in authors:
                if isinstance(a, dict):
                    result.append(str(a.get("name", a)))
                else:
                    result.append(str(a))
            return result
    return []


def _book_contains_file(book: dict[str, Any], book_file_id: int) -> bool:
    """Check primary and alternative format IDs from an Official book response."""
    primary = book.get("primaryFile")
    if isinstance(primary, dict) and primary.get("id") == book_file_id:
        return True
    alternatives = book.get("alternativeFormats")
    return isinstance(alternatives, list) and any(
        isinstance(item, dict) and item.get("id") == book_file_id for item in alternatives
    )
