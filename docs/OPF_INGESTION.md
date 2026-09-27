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

No Pi end-to-end evidence is included. Before merge, review the pinned Grimmory
payload/settings/lock contracts and run an authorized Pi canary. Tests and
static checks have not been run for this review repair.
