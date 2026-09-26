# Session 06 — EPUB/PDF Progress + WebUI Bridge

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8 implements from locked spec
> - **Reviewer:** GPT-5.6 Sol High must define/review progress semantics; DEEP REVIEW REQUIRED
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High first; Flash only for mechanical tests after review

## Objective
รักษา EPUB หน้า/% และ native location รวมถึง PDF/conflict semantics

## Prompt

```text
Implement progress adapter preserving fork behavior.

EPUB/reflowable:
- native location/CFI/XPointer authoritative
- never replace native location with page number
- avoid numeric-only fake native location where legacy rejected it
- with trustworthy currentPage + totalPages: displayPercent = currentPage / totalPages * 100
- regression: 55 / 16653 ≈ 0.33%, NOT 33%
- isolate Official fraction-vs-percent at adapter boundary

PDF/fixed-page:
- preserve currentPage/totalPages, percentage projection, file/user projection
- preserve expected timestamp / force conflict semantics or equivalent optimistic conflict state

Manual status: newer manual status must not be overwritten by older progress.

Use canonical internal ProgressSnapshot with explicit units; no ambiguous naked percentage field.
Verify both directions: KOReader→Official→WebUI and WebUI→Official→KOReader.
Tests must include EPUB 55/16653, .33 fraction vs 33 percent regression, native location roundtrip, missing pages, PDF projection, stale/new timestamps, conflict/force.
Do not proceed to metadata until green. Update HANDOFF.
```
