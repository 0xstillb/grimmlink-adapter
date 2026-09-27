# Session 03A — OPF API with Sidecar Fallback

## Scope

This flow imports metadata from an OPF sidecar while leaving the source
ebook/PDF/CBX bytes unchanged. It requires a verified exact Grimmory `book_id`;
it does not fuzzy-match by title, filename, author, or ISBN.

## Modes

- `api_preferred`: update Official Grimmory first; use sidecars only for
  transport, timeout, or upstream 5xx failure.
- `api_only`: update Official Grimmory and fail closed on any API error.
- `sidecar_only`: write local sidecars without calling Official Grimmory.
- `dry_run=true`: parse, normalize, and preview the operation without API,
  file, or SQLite writes.

The Official metadata request uses Grimmory's `MetadataUpdateWrapper`: the
normalized fields are sent under `metadata` with the verified `bookId`; series
is flattened to `seriesName`, `seriesNumber`, and `seriesTotal`. The request
uses `mergeCategories=false` and `replaceMode=REPLACE_WHEN_PROVIDED`.

## Discovery and normalization

Discovery accepts an explicit `.opf`, an adjacent `<book-stem>.opf`,
`metadata.opf`, or one OPF in a supplied directory. Multiple candidates and
paths escaping the source root stop with an error. Canonical fields include
title, subtitle, authors, publisher, publication date, description, language,
categories, ISBN-10/ISBN-13, series, and an optional cover.

## Fallback and safety rules

Official 401/403, malformed identity, malformed OPF, field locks, and 4xx
rejections never fall back, except HTTP 422 which Grimmory uses for a deployed
field incompatibility. Sidecars use an object cover shape and are written next
to the book as `<stem>.metadata.json` and `<stem>.cover.jpg`. The source book
fingerprint is checked before and after the operation. SQLite state makes the
same source fingerprint, metadata hash, and book identity idempotent.

## Review gate

Local tests verify parsing, discovery, ambiguity, traversal protection, dry-run,
deduplication, lock handling, API success, and permitted fallback. A Sol review
must still validate the pinned Grimmory request/response contract and Pi canary
before this session is merged.
