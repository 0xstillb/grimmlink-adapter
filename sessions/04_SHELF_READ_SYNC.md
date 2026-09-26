# Session 04 — Shelf Sync Read Path

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8
> - **Reviewer:** SOL REVIEW REQUIRED before Session 05
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High for semantics; Flash for pagination/DTO plumbing

## Objective
ทำ regular + magic shelf read/download โดยยังไม่เปิด mutation

## Prompt

```text
Implement Official shelf READ path.

Regular: list shelves + all books; normalize to frozen GrimmLink DTO.
Magic: list magic shelves; page-loop books until COMPLETE successful snapshot; add shelf_type=magic in adapter; preserve rule-derived semantics.

Normalize Official Book/BookFile → internal bookId, bookFileId, title, author, series, filename, format, size, required legacy fields.
Download via Official Bearer API; preserve integrity checks expected by client.

Safety: incomplete pagination is NOT complete snapshot. No cleanup/removal/mutation/local delete.
Tests: regular empty/nonempty, magic multipage, page failure, duplicate across shelves, missing primaryFile, download auth/timeout/bad content.
Update HANDOFF.
```
