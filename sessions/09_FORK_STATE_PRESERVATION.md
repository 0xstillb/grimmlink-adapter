# Session 09 — Preserve Fork-only Data

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8
> - **Reviewer:** SOL DATA-LOSS REVIEW REQUIRED before any cutover
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High

## Objective
ส่งออก V9001/V9002 แบบ read-only ก่อน retirement

## Prompt

```text
READ-ONLY preservation first. Do not mutate fork schema.

Inventory/export:
- grimmlink_metadata_items
- V9001 reading-session extra fields
- V9002 idempotency identity
- counts/checksums by user/book/type
- timestamps/device/dedupe/content hashes
- rows not representable in Official

Build controlled exporter to versioned JSONL/SQLite archive with checksums, resumable behavior, no secrets, no write-back.
Then classify every field/row: PRESERVED_IN_OFFICIAL / PRESERVED_IN_ADAPTER_STATE / ARCHIVE_ONLY / UNMAPPED_BLOCKER.
Do not import automatically. No direct Official DB writes.
Acceptance: source counts/checksums match export; every fork-only field has destination or blocker.
Update HANDOFF + docs/FORK_DATA_PRESERVATION.md.
```
