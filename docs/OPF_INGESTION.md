# Session 03A — OPF API with Sidecar Fallback

## Scope

Import metadata from a deterministic OPF while protecting the ebook/PDF/CBX,
OPF, and cover source files. A caller-supplied `book_id` is only a hint: the
service proves an exact Grimmory partial-MD5 mapping through the configured
read-only DB lookup, then verifies the returned `bookId`, `bookFileId`, and
filename through Official's book-detail API. Missing and ambiguous matches
stop without writes.

## Modes

- `api_preferred`: call Official's metadata API only after a positive settings
  check confirms source-file persistence and file-moving are disabled. Use the
  sidecar fallback only when `METADATA_FALLBACK=sidecar` and the failure is an
  eligible transport/timeout/5xx or explicit unsupported-field 422.
- `api_only`: require the same safe settings check and fail on any metadata API
  error. Stock Official cover upload is disabled because it can rewrite the
  source ebook/PDF/CBX.
- `sidecar_only`: write Adapter-owned JSON/JPEG sidecars locally. Ask Official
  to import metadata only when its source-write settings are positively known
  safe; otherwise report `partial` with import pending.
- `dry_run=true`: parse, resolve and preview without API writes, sidecar writes,
  or SQLite state writes. Identity and settings reads may still occur.

Metadata requests use Grimmory's `MetadataUpdateWrapper`, with fields nested
under `metadata`, the verified `bookId`, flattened series fields,
`mergeCategories=false`, and `replaceMode=REPLACE_WHEN_PROVIDED`. Locked fields
stop before any write, including the global all-metadata lock.

## Covers and sidecars

Official's sidecar import applies metadata; cover application is a separate
operation. Covers are therefore written only as adjacent `<stem>.cover.jpg`
files and reported as pending. A cover keeps the result partial and retryable;
the Adapter never reports the cover as applied. Cover source must be JPEG.
Sidecars are written atomically and existing files are replaced only when the
metadata sidecar identifies the Adapter as owner and references that cover.

The book, OPF, and cover fingerprints are checked before and after ingestion.
The service does not call Official's cover upload endpoint. Official metadata
or sidecar-import writes are blocked whenever persistence settings are enabled,
missing, malformed, or unavailable.

## Deduplication and review

SQLite deduplication includes OPF fingerprint, cover fingerprint, normalized
metadata, book identity, and delivery mode. Only complete results are
deduplicated; partial and pending results remain retryable. HTTP 422 is
fallbackable only when its response explicitly identifies an unsupported,
unknown, or unrecognized field. Auth, permission, lock, identity, and other
client errors stop without fallback.

## Pi positive canary — 2026-09-27

Status: **PASS**. The canary ran from commit
`3edab2c64511f1f2c2ef689b2a86459dac535fbc`, image
`grimmlink-adapter:3edab2c`, digest
`sha256:78ef0d562f47b2e4e12b55c1a6ea01af29430e585575624c84ad1d35bb6fdd1c`.

- Target: `bookId=25`, `bookFileId=25`; exact hash and Official identity passed.
- Only `description` differed; the normalized OPF date `2018-10-14` matched
  Official.
- One `api_only` ingestion PUT returned success. Read-back with
  `GET /api/v1/books/25?withDescription=true` returned the exact OPF
  description. The earlier GET omitted this query parameter, and Grimmory
  intentionally omits description unless it is set.
- Source SHA-256 stayed
  `04f1e1ff23146423f607da0656af7586887e4326f87d6799270b83a11a1a8aee`; OPF
  SHA-256 stayed
  `ce6613fdac1023d3ad09cd88a1da68706a0c7e9b5f00f461b1d9ee902fc8020a`.
- No other metadata field changed. No direct Grimmory DB write, production
  Adapter SQLite write, sidecar import, cover upload, or rescan occurred.
- A rollback attempt was rejected because `clearFlags` was serialized as an
  array, while Grimmory expects an object. No retry was made. The current
  description is the intended OPF value.
- The production container remains on `grimmlink-adapter:355a197-md5fix`;
  the canary image was not deployed.

This closes the Session 03A runtime canary. The implementation was merged to
`main` at `438fa5a98ad949c836934c11b651aaf61bc58c50` after review and checks.
