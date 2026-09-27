# Inter-Session Handoff & Governance

- **Current Session:** Session 03A — OPF API with Sidecar Fallback
- **Implementer:** Codex implementation; Sol deep review pending
- **Current Lifecycle State:** Session 03A positive canary passed and merged to main
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
