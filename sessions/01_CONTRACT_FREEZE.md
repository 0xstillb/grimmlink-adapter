# Session 01 — Freeze Existing GrimmLink Contract

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8
> - **Reviewer:** SOL REVIEW REQUIRED before Session 02
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High; Flash may generate fixtures/tests

## Objective
ทำ legacy contract ให้เป็น executable tests ก่อนแทน fork

## Prompt

```text
Inspect current GrimmLink client and fork `/api/grimmlink/v1`.
Do not change plugin and do not call production write APIs.

Freeze contract for:
auth/capabilities, books/by-hash, book summary/download, regular/magic shelves, shelf books/removal, progress GET/PUT, metadata pull/batch, rating/bookmark/annotation, reading sessions single/batch, error/status shapes.

For each route record method/path/auth/request/response/optional fields/pagination/units/timestamps/errors.
Mark fields REQUIRED / OPTIONAL / DERIVED / LEGACY.

Create docs/LEGACY_GRIMMLINK_CONTRACT.md + contract fixtures/tests.
Do not copy Spring code mechanically. Update HANDOFF. Small commits only.
```
