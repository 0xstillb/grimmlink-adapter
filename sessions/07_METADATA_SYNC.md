# Session 07 — Rating + Bookmark + Annotation + Cursor/Dedupe

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8
> - **Reviewer:** SOL DEEP REVIEW REQUIRED before merge
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High for semantics; Flash for CRUD plumbing

## Objective
แทน metadata batch ด้วย Official APIs โดยคง dedupe/device/cursor/delete

## Prompt

```text
Replace fork metadata batch with Official adapters.

Rating: preserve GrimmLink 1–10; map to Official 1–5; explicit reset; prevent conversion drift.
Bookmarks: Official CRUD; keep remote id ↔ local mapping; preserve page/CFI/title/notes; delete only after confirmed success.
Annotations: map pos0/pos1/location/color/style/chapter where supported; unsupported fields must be explicit, never silently corrupt location.
Dedupe: preserve dedupeKey/content hash/device/deviceId/same-device skip/applied-history.
Cursor: key by server/user/book/file/type, never global.
Outbox: retry-safe/idempotent.

Create field mapping table legacy → internal → Official → loss policy.
Tests: rating conversion/reset, duplicate bookmark, deletion, unsupported annotation field, same-device skip, restart/retry, cursor isolation, timeout-after-possible-commit.
Update HANDOFF.
```
