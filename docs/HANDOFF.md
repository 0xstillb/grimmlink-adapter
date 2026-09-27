# Inter-Session Handoff & Governance

- **Current Session:** Session 04 — Shelf Read Sync
- **Implementer:** Codex implementation complete; Sol Shelf Review approved
- **Current Lifecycle State:** Session 04 gate passed on the unmerged working tree
- **Timestamp:** 2026-09-27

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
