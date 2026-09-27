# Session 03A — OPF Metadata Ingestion with Sidecar Fallback

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8
> - **Reviewer:** GPT-5.6 Sol High — DEEP REVIEW REQUIRED before merge
> - Gemini must inspect, implement, test, update `docs/HANDOFF.md`, then STOP before merge.
> - Merge only after Sol returns APPROVE.

## Objective

ทำ OPF ingestion โดยใช้ Official Grimmory Metadata API เป็น primary path และใช้ Sidecar JSON เป็น fallback:

```text
metadata.opf / adjacent .opf
        ↓
GrimmLink Adapter
  parse + normalize
        ↓
resolve exact bookId
        ↓
PRIMARY:
PUT Official Grimmory metadata API
        ↓
success → done

failure / unsupported / explicit fallback policy
        ↓
FALLBACK:
generate .metadata.json (+ .cover.jpg if needed)
        ↓
Official Grimmory Sidecar Import
```

Official Grimmory ต้อง stock.
ห้าม direct DB write.
ห้ามแก้ PDF/EPUB/CBX เพื่อฝัง metadata.

## Primary path — Official API

Metadata:

```text
PUT /api/v1/books/{bookId}/metadata
?mergeCategories=false
&replaceMode=REPLACE_WHEN_PROVIDED
```

Cover:

```text
POST /api/v1/books/{bookId}/metadata/cover/upload
```

Do not assume payload shapes from memory.
Inspect pinned Official source/models/tests before implementing typed request models.

## Fallback path — Sidecar JSON

Generate Official-compatible sidecar:

```json
{
  "version": "1.0",
  "generatedAt": "...",
  "generatedBy": "grimmlink-adapter",
  "metadata": {
    "title": "...",
    "authors": ["..."],
    "series": {
      "name": "...",
      "number": 1
    }
  },
  "cover": {
    "source": "external",
    "path": "book.cover.jpg"
  }
}
```

Never emit legacy forms such as:

```json
"cover": "book.cover.jpg"
```

Use deterministic names:

```text
book.ext
book.metadata.json
book.cover.jpg
```

## Fallback policy

API is PRIMARY.

Use Sidecar fallback only when one of these is true:

1. Official metadata API is unavailable after bounded retry.
2. Official rejects a supported metadata field due to deployed-version incompatibility.
3. Cover API fails but sidecar cover path is supported.
4. User explicitly configures `metadata_ingestion_mode=sidecar`.
5. Dry-run/preview mode requests sidecar generation only.

Do NOT fallback for:
- ambiguous book identity
- unauthorized/forbidden access
- invalid OPF
- unsafe path
- mapping conflict
- field-lock conflict
- wrong library/book match

Those must stop with BLOCKED/ERROR, not silently create sidecars.

## OPF discovery

Support:
- Calibre `metadata.opf`
- adjacent `<book-stem>.opf`

Safety:
- read-only source traversal
- path containment
- no path escape
- deterministic OPF→book pairing
- ambiguous match => BLOCKED
- malformed OPF => skip with diagnostic
- never title-only fuzzy match to bookId

## Canonical metadata model

Parse/normalize at least:

- title
- subtitle
- authors
- publisher
- publishedDate
- description
- language
- categories
- ISBN-10
- ISBN-13
- series.name
- series.number
- series.total if available

Normalize numeric fields before API/sidecar output.
Example:

```text
series number "1" → 1
```

## Book identity dependency

This session depends on Session 03 identity.

Required resolution order must preserve current migration design:

```text
currentHash
→ initialHash
→ verified cached bookId
→ defensive Official lookup
→ ambiguity = STOP
```

Do not inject metadata into an uncertain book.

## File/hash safety

Primary and fallback flows must not modify the original book file.

Before/after canary:
- compute book hash
- verify identical after metadata API update
- verify identical after cover upload
- verify identical after sidecar generation/import

If hash changes unexpectedly:
STOP and report BLOCKER.

Also verify Official settings do not auto-write metadata back into book files during acceptance.

## Metadata locks

Inspect current Official lock semantics.

Rules:
- never silently overwrite locked fields
- use `REPLACE_WHEN_PROVIDED`
- if field-lock state cannot be safely respected, STOP and report BLOCKER
- do not use clear flags unless explicit user action requires clearing

## Cover handling

Priority:
1. explicit OPF manifest/property cover if available
2. adjacent matching cover
3. known Calibre cover fallback

Output/API:
- API primary: upload cover through Official endpoint
- fallback: write `.cover.jpg` + object-form `cover` block in sidecar

Do not rewrite the source ebook/PDF.

## State / idempotency

SQLite should track:

- source OPF path
- source fingerprint/hash
- resolved bookId
- last normalized metadata hash
- last API result
- last sidecar result
- fallback reason
- last successful ingestion timestamp

Do not resend unchanged metadata on every scan.

## Dry-run

Add a dry-run/preview mode that shows:

```text
OPF source
Book target
Resolved bookId
Fields to update
Fields skipped
Locked/conflicting fields
Cover source
Primary action
Fallback action
Would modify book file? NO
```

Dry-run must not call write APIs and must not write sidecar files.

## Tests

At minimum:

- title/authors/publisher/language
- series name + numeric series number
- ISBN10/ISBN13
- malformed OPF
- ambiguous OPF/book pairing
- unsafe path escape
- locked fields
- unchanged metadata dedupe
- API success → no sidecar written
- API timeout → sidecar fallback
- API 401/403 → NO fallback
- invalid mapping → NO fallback
- cover API failure → valid object-form sidecar cover fallback
- legacy string cover schema is never emitted
- original book hash unchanged
- retry/restart idempotency
- fallback sidecar can be imported by Official Grimmory

## Deliverables

Create/update:

```text
src/grimmlink_adapter/opf/
src/grimmlink_adapter/services/opf_ingestion.py
src/grimmlink_adapter/models/opf_metadata.py
tests/unit/test_opf_*.py
tests/integration/test_opf_ingestion_*.py
docs/OPF_INGESTION.md
docs/HANDOFF.md
```

Document config:

```text
metadata_ingestion_mode=api_preferred
metadata_fallback=sidecar
```

Optional modes:

```text
api_only
api_preferred
sidecar_only
```

Default should be:

```text
api_preferred + sidecar fallback
```

Do not modify Official Grimmory.
Do not direct-write Grimmory DB.
Do not retire Grimmory Bridge yet; keep it as migration/recovery tool until E2E cutover passes.

## Runtime acceptance record — 2026-09-27

**Positive canary: PASS.** Commit `3edab2c64511f1f2c2ef689b2a86459dac535fbc`
was tested from a clean detached worktree using image
`grimmlink-adapter:3edab2c` (`sha256:78ef0d562f47b2e4e12b55c1a6ea01af29430e585575624c84ad1d35bb6fdd1c`).
For `bookId=25` / `bookFileId=25`, exact hash and Official identity passed.
The normalized OPF date `2018-10-14` matched Official; only `description`
differed. One `api_only` PUT was verified by read-only
`GET /api/v1/books/25?withDescription=true`; the returned description exactly
matched the OPF, and no other field changed.

The source SHA-256 remained
`04f1e1ff23146423f607da0656af7586887e4326f87d6799270b83a11a1a8aee`; OPF
SHA-256 remained
`ce6613fdac1023d3ad09cd88a1da68706a0c7e9b5f00f461b1d9ee902fc8020a`.
No direct Grimmory DB write, production Adapter SQLite write, sidecar import,
cover upload, or rescan occurred. An earlier GET without `withDescription=true`
hid the saved description. A rollback attempt was rejected because the harness
sent `clearFlags` as an array; it was not retried. The verified current value
is the intended OPF description. The production container remains on
`grimmlink-adapter:355a197-md5fix`; the canary image was not deployed. Runtime
canary is closed. Session 03A was merged to `main` at
`438fa5a98ad949c836934c11b651aaf61bc58c50` after review and checks.
