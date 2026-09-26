# Session 03 — Book Identity

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8
> - **Reviewer:** SOL DEEP REVIEW REQUIRED before merge
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High

## Objective
ย้าย currentHash → initialHash → bookId โดยไม่จับหนังสือผิดเล่ม

## Prompt

```text
Implement book identity without direct DB access.
Preserve: currentHash → initialHash → accessible candidate fallback.
Never silently choose ambiguity.

Use adapter SQLite for server/user + currentHash + initialHash + bookId/bookFileId mapping.
Filename/path/metadata are secondary evidence only.
Verify mapped book still exists and is accessible; invalidate stale mapping safely.
Expose legacy-compatible `/books/by-hash/{hash}` only in adapter if current plugin needs it.

Tests: exact currentHash, initialHash fallback, stale id, inaccessible candidate, ambiguity, rename, server/user isolation, restart persistence.
Document algorithm/failures. Update HANDOFF.
```
