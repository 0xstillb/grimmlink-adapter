# Session 05 — Shelf Mutation + Safe Cleanup

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8
> - **Reviewer:** SOL DEEP SAFETY REVIEW REQUIRED before merge
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High

## Objective
ย้าย regular shelf mutation พร้อม multi-shelf safety

## Prompt

```text
Only after Session 04 green.
Use Official POST `/api/v1/books/shelves` with bookIds/shelvesToAssign/shelvesToUnassign.

Rules:
- regular unassign supported
- magic removal MUST NOT become remote manual unassign
- add-to-shelf only if explicitly required
- local managed copy deletion requires COMPLETE successful snapshot + cleanup policy + no other shelf/provider reference + expected tracked path + downloaded_by_grimmlink
- server success first, then finalize local mapping
- use idempotent outbox/retry

Tests: one shelf, two shelves, regular+magic, incomplete snapshot, timeout, retry, 401/403, user local file never deleted.
Update HANDOFF.
```
