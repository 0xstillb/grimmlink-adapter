# Inter-Session Handoff & Governance

- **Current Session:** Session 08 — Reading-session adapter
- **Implementer:** Gemini implementation followed by Sol gate fixes; unmerged working tree
- **Current Lifecycle State:** Session 08 gate findings addressed in working tree; verification complete, no merge yet
- **Timestamp:** 2026-09-28

---

## 1. Review Repair

The adapter now fails closed when Official Grimmory cannot prove a hash-to-book
identity. Stock Official GET /api/koreader/syncs/progress/{bookHash} returns
reading position fields but no bookId. An empty progress response cannot prove
that a book is absent. A Bearer-only request cannot query this KOReader MD5 route.

- Existing server/user/currentHash or initialHash mappings can be used with
  a same-user Bearer token after an upstream book and file access check.
- MD5-only requests use a same-user Official JWT after local account linking.
  Without a valid link they fail closed with 501. An unmapped Bearer hash
  returns 501 when no authoritative DB/API
  mapping exists; the adapter does not invent bookId or return a false 404.
- Upstream auth and transport failures remain distinct from unavailable hash
  evidence: 401/403 and 502 respectively.
- If an upstream extension supplies a positive bookId in the hash response,
  the adapter verifies the book with the caller's Bearer token before caching
  it. This conditional path is not claimed as stock Official functionality.
- When both MD5 and Bearer credentials arrive, each is authenticated and the
  upstream user IDs must match.
- The final book detail fetch is required. A revoked permission or malformed
  response cannot fall back to cached details. A changed book/file ID on the
second fetch returns 409. A file may be the primary or an alternative format.

The frozen OfficialBookDTO response model remains declared for successful
responses. Missing mapping evidence or a required account link returns 501.

## 2. Remaining Functional Blocker

The Pi read-only audit closed the sample hash diagnosis without KOReader:
`d65441b897513421215d1fe4cfc3e9a5` maps to one non-deleted Grimmory row
(`bookId=25`, `bookFileId=25`, `libraryId=4`). Stored `current_hash` and
`initial_hash` are `d654…`; current file bytes fingerprint as
`95b818e712f70c1eb0b6f2aaaa650e1e`. No library refresh was run.

The audit confirms Grimmory uses MariaDB 11.4.8. The DB is reachable as
`grimmory-mariadb:3306` only from Docker network `grimmory_default`; port 3306
is not published on the host. The existing application account has broad
write privileges. No read-only account or views were found, and the Adapter
is not deployed on the Pi. The audit inspected user/library permission tables;
the implemented provider only needs SELECT on `book`, `book_file`, `library`,
and `library_path`, since effective access is checked by Official's Bearer API.
`current_hash` is indexed; `initial_hash` is not. No Official Bearer token was
available for the `GET /api/v1/books/25` access check.

The user approved a narrow read-only DB lookup exception on 2026-09-26. The
Adapter now has an opt-in MariaDB provider. It runs parameterized exact
`current_hash` lookup first, checks `initial_hash` only on a current miss,
filters deleted/non-book rows, and fails on ambiguity. The provider contains
no DB write statements and is disabled by default. Candidate rows are verified
with a same-user Official Bearer book-detail request before being cached in
the Adapter SQLite store or returned. Configured DB lookup takes precedence
over older Adapter cache rows; DB errors fail closed with 502. MD5-only clients
can link their own account locally via
`python -m grimmlink_adapter.link_account <username>`. The link command verifies
KOReader MD5 auth and JWT profile refer to the same user, then saves tokens in
Adapter SQLite. The DB file must be restricted to the Adapter operator.

Runtime use is not configured yet. The audit found no dedicated SELECT-only
account, no host-published MariaDB port, no Adapter deployment on the Pi, and
no linked user for the Official access canary. Do not enable the provider or
reuse the broad Grimmory application account. Provision an account with SELECT
only on `book`, `book_file`, `library`, and `library_path`, restrict its network
source to the Adapter, connect the Adapter to `grimmory_default`, and keep DB
settings in the secret mechanism. Link the target user locally and test book 25
with an MD5-only request. Do not run a library refresh; it affects all of
library 4, not only book 25.

Sources:
- [Official KOReader progress response](https://grimmory.org/api/operations/getprogress/)
- [Official file metadata response](https://grimmory.org/api/operations/getfilemetadata/)
- references/OFFICIAL_GRIMMORY_API_PARITY_AUDIT.md, section B3

## 3. Verification Evidence

- uv run --no-sync pytest -q → **168 passed** (including linked MD5 and DB/cache precedence tests).
- uv run --no-sync ruff check . → **All checks passed**.
- uv run --no-sync mypy src → **No issues in 42 source files**.
- git diff --check → no whitespace errors (line-ending notice only).

New regressions cover the stock progress DTO without bookId, Bearer-only
cache miss, upstream transport failure, final-detail 403, file replacement during the final fetch, and mismatched
MD5/Bearer user IDs.

## 4. Session 03 Files

- src/grimmlink_adapter/services/book_service.py — safe resolution and
  distinct unavailable/transport errors.
- src/grimmlink_adapter/api/books.py — verified user binding, status mapping,
  and required final book detail.
- src/grimmlink_adapter/official/identity_lookup.py — parameterized,
  read-only MariaDB hash lookup, disabled unless configured.
- src/grimmlink_adapter/config.py and `.env.example` — optional DB connection
  settings; credentials are intended for the secret mechanism.
- src/grimmlink_adapter/state/book_identity.py and
  migrations/002_book_identity.sql — scoped mapping and ambiguity handling.
- tests/unit/test_book_identity.py — acceptance and review regressions.
- Contract tests preserve the frozen OfficialBookDTO route model.

## 5. Next Gate

To close Session 03 on the Pi:

1. Provision a dedicated MariaDB account with SELECT only on `book`,
   `book_file`, `library`, and `library_path`. Verify its grants and connect
   the Adapter container to the existing restricted `grimmory_default` network.
   Deploy the Adapter with `GRIMMORY_DB_ENABLED=true`, its own persistent SQLite
   volume, and the new migrations. Do not deploy the example Compose file as-is;
   it defines a second Grimmory service.
2. Run `python -m grimmlink_adapter.link_account <username>` inside the Adapter
   container as the target Grimmory user. It must share the running Adapter's
   `GRIMMORY_BASE_URL` and `SQLITE_DB_PATH`. Do not expose the password or MD5
   key in a shell command or log.
3. Select an active book whose current on-disk partial MD5 equals its
   `book_file.current_hash`. Send a read-only MD5-only GET to
   `/api/grimmlink/v1/books/by-hash/{hash}` without a caller Bearer token.
   Require HTTP 200 and the exact expected `bookId` and `bookFileId` in
   `primaryFile` or `alternativeFormats`. Repeat with an unknown hash (404),
   an invalid MD5 key (401), and a user without book access (403). Record
   redacted request/response evidence and confirm no unexpected DB writes.
4. Review the runtime evidence and diff, then commit/merge through the
   required Session 03 review gate.

Do not use the known stale `d654…` sample alone as a pass criterion: book 25's
current bytes hash to `95b8…`. A 200 for `d654…` would prove DB mapping and
permission checks but would not prove current file-byte identity. No library
refresh is required for this canary; choose a file whose DB hash already
matches its bytes. Runtime deployment and review remain open.

## 6. Session 03A — OPF API with Sidecar Fallback

The review repair now resolves the target from an exact partial-MD5 lookup in
the configured read-only Grimmory DB, then verifies the book and file IDs with
Official's API. A supplied `book_id` cannot override that identity. Global and
per-field locks stop before writes. Official API metadata writes are allowed
only after settings confirm source-file persistence and file-moving are off;
missing or unreadable settings block API writes.

The stock Official cover-upload endpoint is disabled in this flow because its
implementation can rewrite the source ebook/PDF/CBX. Covers are emitted only
as local JPEG sidecars, and Official sidecar import is not claimed to apply a
cover. Partial results remain retryable. Sidecar overwrites require Adapter
ownership markers and use atomic replacement. Fallback is controlled by
`METADATA_FALLBACK`; HTTP 422 falls back only for explicit unsupported-field
messages. The book, OPF, and cover source fingerprints are checked after work.

`api_only`, `api_preferred`, and `sidecar_only` remain supported, with dry-run
and SQLite deduplication state. Pi positive canary passed on commit
`3edab2c64511f1f2c2ef689b2a86459dac535fbc` (image `grimmlink-adapter:3edab2c`,
digest `sha256:78ef0d562f47b2e4e12b55c1a6ea01af29430e585575624c84ad1d35bb6fdd1c`).
For `bookId=25` / `bookFileId=25`, exact hash and Official identity passed; the
OPF date normalized to `2018-10-14`; only `description` differed. One `api_only`
ingestion PUT was followed by read-only
`GET /api/v1/books/25?withDescription=true`, which returned the exact OPF
description. The earlier GET omitted that query parameter, so Grimmory
intentionally omitted description from its response. The source SHA-256 stayed
`04f1e1ff23146423f607da0656af7586887e4326f87d6799270b83a11a1a8aee`; OPF
SHA-256 stayed
`ce6613fdac1023d3ad09cd88a1da68706a0c7e9b5f00f461b1d9ee902fc8020a`.
No direct Grimmory DB write, production Adapter SQLite write, sidecar import,
cover upload, or rescan occurred. A rollback attempt was rejected because the
harness serialized `clearFlags` as an array; no retry was made. The verified
description is the intended OPF value. The production container remains on
`grimmlink-adapter:355a197-md5fix`; the canary image was not deployed. Session
03A was merged to `main` at `438fa5a98ad949c836934c11b651aaf61bc58c50` after
the canary and pre-merge checks passed. Grimmory DB writes are not part of
this session.

---

## 7. Session 04 — Shelf Read Sync

Regular and magic shelf read/download paths are implemented in the Adapter.
Regular shelves and their complete book lists use Official's unpaginated APIs;
magic shelves use the app API and their books are accumulated page by page.
The adapter reads a magic shelf twice and returns it only when both complete
scans have identical book IDs and primary file IDs. Each scan requires
`hasNext=false` with consistent page, size, total, previous/next metadata, and
item counts. A request failure, malformed page, duplicate book ID, or changed
membership fails the read rather than exposing a partial snapshot. Session 05
must revalidate before any cleanup because Official does not provide an atomic
snapshot token. Magic membership remains rule-derived; only regular shelf
unassignment is enabled in Session 05.

Official emits the same zero-total response for a genuinely empty magic shelf
and for a page whose filtered content hides later books. The adapter therefore
returns 502 for that ambiguous response; an empty magic shelf cannot currently
be reported as a successful read without an authoritative upstream count.

Official `Book`/`BookFile` records map to the frozen flat GrimmLink summary,
including IDs, title, author, series, filename, format, and size. Entries
without a primary file remain readable with nullable file fields. Regular
duplicate book/file rows are collapsed; magic duplicate book IDs fail closed so
an incomplete snapshot cannot feed cleanup. Regular and magic shelves with the
same numeric ID remain separate because type is preserved. `extension` comes
from the filename in lowercase; `fileFormat` comes from the Official file type
in uppercase. Shelf pagination uses a book ID cursor, gives an explicit offset
precedence, and defaults/clamps the limit to 100.

Shelf and download calls verify incoming Bearer identity; MD5-only callers use
their locally linked same-user JWT, and requests carrying both credential types
must resolve to the same Official user. Downloads verify the Official book and
primary file, reject empty/error-document responses, then stream the binary
with its filename and content length. First-read timeout, transport, and
protocol failures close both upstream resources before returning 502. KOReader
remains responsible for its existing on-device signature and size verification
before committing a file.

Verification on 2026-09-27:

- `.venv\Scripts\ruff.exe check .` — all checks passed.
- `.venv\Scripts\mypy.exe src` — no issues in 48 source files.
- `.venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-gate-all` — 203
  passed. Pytest could not update its cache because of workspace permissions.
- `git diff --check` — passed (Git reported LF-to-CRLF notices for two edited
  source files).

Review repairs included a dedicated Official `AppBookSummary` normalizer for
magic shelves, two matching complete scans and strict page/total metadata checks,
explicit closure of the per-request auth client, preserved 403 shelf errors,
direct `seriesName`/`seriesNumber` mapping from Official metadata, and closed
first-read stream failures. Tests exercise the real app summary shape,
inconsistent page metadata, duplicate IDs, empty snapshots, and stream errors.

Implementation is complete but has been committed on `main` as `dfa36c8`.
**Required Sol Shelf Review returned APPROVE with no actionable findings on
2026-09-27.** The reviewer
confirmed the two matching magic scans, legacy pagination and file fields,
and the Session 05 revalidation requirement. The known empty magic shelf 502
remains a documented fail-closed limitation and is not a gate blocker. Include
the untracked `request_auth.py` and `test_shelf_read_sync.py` in the Session 04
commit. No Session 05 mutation, local deletion, deployment, or canary was
performed.

---

## 8. Session 05 — Shelf Mutation and Safe Cleanup

Regular shelf removal calls Official's bulk `POST /api/v1/books/shelves`
with `bookIds`, `shelvesToAssign=[]`, and `shelvesToUnassign=[shelfId]`.
Magic shelves remain rule-derived and reject manual removal with HTTP 400.
The remote mutation is completed before local ownership is changed. Each
request has a deterministic outbox/idempotency key; timeout and transport
failures leave the same action retryable and do not remove local ownership.

Local file deletion is disabled by default. When explicitly enabled, the
adapter requires a complete successful regular and magic shelf snapshot, no
remaining shelf ownership across any authenticated owner, an adapter-managed
file marker registered after a local copy is actually written, zero provider
references, a matching managed-file root, an unchanged expected size, and
`downloaded_by_grimmlink=true`. User-owned files and incomplete snapshots are
retained. The streaming download route does not invent a local path; only an
explicit managed-copy pipeline may call the registration hook. A failed
cleanup verification does not undo a successful Official unassignment.

Shelf ownership and managed-file rows are scoped by authenticated owner. A
complete snapshot replaces only that owner's rows, while deletion checks for
references from every owner before unlinking a shared path. Completed outbox
operations are not reused for a later remove/re-add/remove cycle; only pending
or failed actions reuse the original operation key.

Verification on 2026-09-27:

- `.venv\Scripts\ruff.exe check .` — all checks passed after the Session 05
  import repair.
- `.venv\Scripts\mypy.exe src` — no issues in 48 source files.
- `.venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-session05-all` —
  223 passed. Pytest could not update its cache because of workspace
  permissions.
- `git diff --check` — passed with Git's expected LF-to-CRLF notices.

Session 05 implementation remains uncommitted. **Mandated Sol deep safety
review returned APPROVE with no critical findings on 2026-09-27 and merge is
allowed.** The review confirmed verified user scoping, atomic owner snapshots,
UUID operation keys, remote-first finalization, global shelf/provider guards,
and strict managed-file path/size checks. No production Official mutation or
cleanup was performed during verification. Migration 007 scopes shelf and
managed-file state by verified Official user identity; cleanup remains opt-in
and requires the explicit managed-copy registration hook.

---

## 9. Session 06 — EPUB/PDF Progress and WebUI Bridge

The adapter now exposes the frozen progress routes and advertises progress,
WebUI progress, and PDF bridge capability. GrimmLink wire percentages remain
explicit 0..100 display values; Official KOReader percentages are converted at
the boundary to and from 0..1 fractions. A canonical `ProgressSnapshot` keeps
the two units named separately.

Reflowable formats use the native KOReader location (`location` first, then
`progress`) as the authoritative position. Numeric-only locations are rejected
for EPUB-like formats. When trustworthy page data exists, the display percent
is derived as `currentPage / totalPages * 100`; for example, 55/16653 is about
0.33%, not 33%. PDF and other fixed-page formats preserve page fields and use
the same percentage projection.

Progress writes perform an upstream read before mutation. Older timestamps and
an unexpected `expectedUpdatedAt` return an explicit conflict without writing;
`force=true` overrides only the remote-progress timestamp check. A newer manual
status always wins, including forced replay. A scoped SQLite progress cache
(migration 008) stores the last normalized snapshot; `manual_status_state` and
the per-hash marker retain authoritative WebUI status timestamps.

Bearer writes perform the same app-progress timestamp preflight and use stable
verified user identity for cache scope, so token rotation cannot bypass manual
status precedence. App/WebUI percentages are parsed as display units even when
below 1%; the scoped canonical snapshot supplies PDF total pages when the app
response omits them. XPointer values are never labelled as EPUB CFI: MD5
reflowable writes remain on Official's KOReader conversion path, while Bearer
app writes require a real `epubcfi(...)` location.

Bearer requests use verified server/user-scoped hash mappings and Official's
app progress endpoint. PDF writes send the real `pdfProgress` page/percentage
schema and EPUB writes send `epubProgress`; MD5 requests continue to use the
KOReader endpoint and mirror through the JWT projection when a linked session
is available. Manual status writes use Official `POST /api/v1/books/status`
and persist the returned `readStatusModifiedTime` before returning success.
Fixed-page MD5 writes require a verified book mapping and use the Official app
PDF write as the single authoritative mutation; an unmapped PDF fails before
any KOReader write, and an app-write failure remains retryable without a
partially advanced KOReader timestamp.

Verification on 2026-09-27:

- `.venv\\Scripts\\ruff.exe check src tests` — all checks passed.
- `.venv\\Scripts\\mypy.exe src` — no issues in 48 source files.
- `.venv\\Scripts\\python.exe -m pytest -q --basetemp .pytest-tmp-session06-final7` — 239 passed.
- `git diff --check` — passed (Git reported expected LF-to-CRLF notices).

Session 06 remains unmerged after the required Sol deep progress review.
Sol APPROVE was received on 2026-09-27 with no remaining safety blockers.
No production Official progress mutation or WebUI canary was run.

---

## 10. Session 07 — Rating + Bookmark + Annotation + Cursor/Dedupe

The adapter now replaces the fork metadata batch routes (`POST /api/grimmlink/v1/syncs/metadata`,
`POST /api/grimmlink/v1/syncs/metadata/batch`, and `GET /api/grimmlink/v1/syncs/metadata`)
with Official Grimmory API adapters while preserving full deduplication, client device tracking,
scoped cursors, conversion drift prevention, and delete safety.

### 10.1 Field Mapping Table

| Entity | Legacy GrimmLink Field | Internal Normalized Field | Official Grimmory API / Field | Loss Policy & Integrity Invariants |
| :--- | :--- | :--- | :--- | :--- |
| **Rating** | `rating` (1–10 float/int) | `NormalizedRating.rating_value` | `PUT /api/v1/books/personal-rating` (`rating: 1..5`) | Mapped via `(val + 1) // 2`. Original 1–10 value and source scale recorded in `metadata_applied_history`. Conversion drift prevented on pull by looking up exact source value when `official_value` matches. External WebUI ratings map to even integers (`val * 2`). |
| **Rating** | `val <= 0`, `deleted=True`, or `reset=True` | `NormalizedRating.is_reset = True` | `POST /api/v1/books/reset-personal-rating` | Explicit reset upstream. Clears user rating without ambiguity. |
| **Rating** | `review` | `NormalizedRating.review` | N/A | Preserved in adapter SQLite `metadata_applied_history` payload. Dropped upstream as Official Grimmory has no separate review field on personal rating. |
| **Bookmark** | `id` / `bookmark_id` / `dedupeKey` | `NormalizedBookmark.local_id` | `GET /api/v1/bookmarks/book/{id}` | Bi-directional mapping maintained in `metadata_remote_mappings` `(server, user, book, type, local_id, official_id)`. |
| **Bookmark** | `title` | `NormalizedBookmark.title` | `OfficialBookmarkDTO.title` | Directly mapped upstream (string). |
| **Bookmark** | `notes` / `text` | `NormalizedBookmark.notes` | `OfficialBookmarkDTO.notes` | Directly mapped upstream (string). |
| **Bookmark** | `page` | `NormalizedBookmark.page_number` | `OfficialBookmarkDTO.pageNumber` | Directly mapped upstream (integer). |
| **Bookmark** | `location.cfi` | `NormalizedBookmark.cfi` | `OfficialBookmarkDTO.cfi` | Mapped only if valid CFI (`epubcfi(...)`). |
| **Bookmark** | `deleted: True` | `NormalizedBookmark.deleted = True` | `DELETE /api/v1/bookmarks/{id}` | **Delete Invariant:** Local mapping is deleted ONLY after confirmed upstream success (HTTP 200, 204, or 404). Network/5xx keep mapping intact for outbox retry. |
| **Annotation** | `id` / `annotation_id` / `dedupeKey` | `NormalizedAnnotation.local_id` | `OfficialBookmarkDTO.id` | Mapped to Official Bookmarks with bi-directional ID tracking in `metadata_remote_mappings`. |
| **Annotation** | `text` | `NormalizedAnnotation.text` | `OfficialBookmarkDTO.title` | Highlighted text mapped to Official bookmark `title`. |
| **Annotation** | `note` | `NormalizedAnnotation.note` | `OfficialBookmarkDTO.notes` | User note mapped to Official bookmark `notes`. |
| **Annotation** | `color` | `NormalizedAnnotation.color` | `OfficialBookmarkDTO.color` | Directly mapped upstream (string hex/name). |
| **Annotation** | `page` | `NormalizedAnnotation.page_number` | `OfficialBookmarkDTO.pageNumber` | Directly mapped upstream (integer). |
| **Annotation** | `location.cfi` | `NormalizedAnnotation.cfi` | `OfficialBookmarkDTO.cfi` | Mapped only if valid CFI (`epubcfi(...)`). |
| **Annotation** | `pos0` / `pos1` (XPointer) | `NormalizedAnnotation.pos0`, `pos1` | N/A (**Never injected into `cfi`**) | **Location Integrity Invariant:** Raw XPointer coordinates are strictly isolated and preserved in adapter SQLite `metadata_applied_history.payload_json`. Never injected into Official `cfi`, preventing reader location corruption. |
| **Annotation** | `drawer`, `style`, `chapter` | `NormalizedAnnotation.*` | N/A | Unsupported upstream; preserved losslessly in adapter SQLite `metadata_applied_history.payload_json` and included in dedupe hashes so unsupported-only edits are retained. |
| **Dedupe** | `dedupeKey` / `contentHash` | `MetadataDedupeRecord.content_hash` | N/A | Deterministic SHA-256 hash computed across canonical item fields. Duplicate items receive `status: DUPLICATE` without re-invoking upstream mutation. |
| **Dedupe** | `deviceId` | `MetadataDedupeRecord.device_id` | N/A | Preserved in `metadata_applied_history`. On metadata pull, items authored by the requesting device are skipped to eliminate KOReader sync echo loops. |
| **Cursor** | `since` / `cursor` | `scoped_metadata_cursors.cursor` | N/A | **Scoped Cursor Invariant:** Cursors are strictly keyed by the 5-tuple `(server, user, book, file, type)`. Opaque continuation cursors retain the snapshot cutoff plus offset, reject cross-scope reuse, and do not advance the final timestamp until the page is exhausted. |
| **Outbox** | Mutation action | `OutboxAction` | Upstream mutation | Every production rating/bookmark/annotation mutation is persisted before dispatch and executed through `IdempotencyManager`. **Timeout-after-commit mitigation:** retried bookmark creates probe Official before re-creating and adopt the confirmed upstream ID. |

Mapped bookmark and annotation pulls merge current Official title/notes/color/page/CFI fields with locally preserved unsupported fields. Confirmed records missing from a complete Official bookmark snapshot are returned as explicit deletion tombstones. Unmapped delete requests return `FAILED`; they are never reported as successful without an upstream confirmation.

Rating reset is explicit: `reset=true`, `deleted=true`, or a non-positive value invokes Official reset. A rating object without a value or explicit reset is rejected rather than being interpreted as deletion. Reviews are retained in applied history and restored on pull.

### 10.2 Verification Evidence

- `.venv\Scripts\ruff.exe check src tests` — all checks passed.
- `.venv\Scripts\mypy.exe src` — no issues in 48 source files.
- `.venv\Scripts\python.exe -m pytest -q --basetemp .codex-tmp\sol-session07-fixed-final3` — **259 passed in 20.66s**.
- `git diff --check` — passed cleanly with no trailing whitespace or syntax errors.

### 10.3 Execution Rule & Governance Status

- **Implementer:** Gemini Flash 3.8 completed the initial implementation; Sol remediation expanded verification to 19 dedicated metadata tests plus 240 regression tests and updated this handoff.
- **Rule:** Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
- **Sol remediation:** Fixed production outbox integration, timeout recovery, lossless cursor continuation, explicit rating reset validation, unsupported-field dedupe, unmapped deletion reporting, fresh Official-field reconciliation, remote deletion tombstones, and capability advertisement. Added seven focused regression tests covering those paths.
- **Current Status:** Session 07 implementation and focused remediation are complete. Sol re-review is `APPROVE`; the working tree remains unmerged.

---

## 11. Session 08 — Reading-Session Adapter

The adapter implements `GET/POST /api/grimmlink/v1/reading-sessions` and `POST /api/grimmlink/v1/reading-sessions/batch`. Writes fan out to Official single-session POSTs; reads use Official paginated history with the requesting user's bearer.

### 11.1 Key Architecture & Design Invariants

1. **Deterministic Batch Fanout & Result Aggregation:**
   - Legacy batch endpoint (`POST /api/grimmlink/v1/reading-sessions/batch`) fans out to Official single-session posts (`POST /api/v1/reading-sessions`).
   - Results contain `totalRequested`, `successCount`, and per-item `results` (`created`, `duplicate`, `pending`, or `error`).
   - Supports partial success: an invalid duration/order or non-retryable error in one item records an error status for that specific item without aborting remaining valid sessions in the batch.

2. **Canonical Idempotency Key Format:**
   - Key is constructed deterministically from preserved legacy identity:
     `{server}/{user}/{book}/{hash}/{start}/{end}/{device}`
   - `server`: Normalized Official base URL (e.g. `http://localhost:6060`).
   - `user`: Resolved username or user ID.
   - `book`: Upstream `bookId` (or mapped book ID).
   - `hash`: Book hash (if supplied; empty string if omitted).
   - `start` / `end`: RFC3339 timestamps (`startTime` and `endTime`).
   - `device`: Preserved client device identity (combines `device` and `device_id` as `{device}:{device_id}` if both distinct, or whichever is present).
   - Invariant: Same timestamps on different client devices produce distinct idempotency keys, avoiding cross-device collisions.

3. **Pending / Committed State Persistence (SQLite `reading_sessions_state`):**
   - Migration `010_reading_sessions.sql` creates the table; `011_reading_session_book_type.sql` safely adds `book_type` to databases that already applied 010.
   - States: `POSTING` (atomic in-flight claim before upstream POST), `PENDING` (outcome not confirmed), `COMMITTED` (upstream accepted or probed match). A stale `POSTING` claim becomes eligible for reconciliation after two minutes.
   - Timestamps, duration, book type, page numbers, CFI locations, progress, and error details are persisted.

4. **Timeout-After-POST Mitigation (No Duplicate Blind Retries):**
   - If an upstream `POST /api/v1/reading-sessions` times out (`OfficialTimeoutError` / HTTP 504), the session remains in `PENDING` state.
   - On retry or timeout recovery, the adapter does NOT blindly issue another POST. Instead, it probes Official Grimmory's read endpoint: `GET /api/v1/reading-sessions/book/{bookId}`.
   - Probe exhausts pages and fails closed on read errors or ambiguous matches; an Official ID already claimed by another device is never adopted for this key.
   - If upstream confirms a unique unclaimed match, the adapter adopts its ID. If no match is visible, it leaves the record `PENDING`: a timed-out POST might still commit later. Single retry returns HTTP 503; batch marks that item `pending`. There is deliberately no blind automatic replay; unresolved rows need later reconciliation or an operator decision.
   - Reconciliation runs on the next authenticated session-write request, scoped to that Official server and user. It does not run at startup without user credentials and never replays another user's rows with the caller's token.

5. **Local Preservation of Unsupported Legacy Fields:**
   - Official POST receives `bookId`, `bookType`, times, duration, progress fractions, and start/end locations. It may return HTTP 202 with no session ID.
   - Unsupported legacy fields (`book_hash`, `device`, `device_id`, `current_page`, `total_pages`, `start_page`, `end_page`) remain in local SQLite; they are not transmitted upstream. Legacy progress percentages always divide by 100, including values below 1%.

6. **Timing Validation:**
   - Enforces chronological order (`endTime > startTime`), compatible timezone notation, positive duration, and progress in 0–100%. Invalid single sessions return HTTP 400; invalid batch items report `error` without upstream calls.

7. **Capability Advertisement:**
   - `GET /api/grimmlink/v1/syncs/capabilities` advertises `readingSessions: true`; legacy GET is implemented through authenticated Official paginated GET.

8. **Fork DB Isolation:**
   - MariaDB / fork database was NOT touched. All state and idempotency tracking are isolated within adapter SQLite.

### 11.2 Verification Evidence

- `.venv\Scripts\ruff.exe check src tests` — **All checks passed!**
- `.venv\Scripts\mypy.exe src` — **Success: no issues found in 49 source files**.
- `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .codex-tmp/sol-session08-final3` — **288 passed in 22.05s**. Added regression cases for failed probe, later-page match, cross-device collision (including Official 202 without ID), concurrent same-key POSTs, user-scoped recovery, sub-1% progress, authenticated GET, Official HTTP 202, and upgrade from migration 010.
- `git diff --check` — **Passed cleanly with zero whitespace or line-ending errors**.

### 11.3 Governance Status

- **Implementer:** Gemini initial implementation; Sol gate fixes in this working tree.
- **Rule:** No merge until the corrected diff passes final review.
- **Current Status:** Gate findings addressed and tests green; no merge or commit performed. Safety tradeoff: ambiguous or absent upstream confirmation leaves `PENDING` rather than risking duplicate replay.
