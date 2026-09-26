# Session 08 — Reading Sessions + Idempotency

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
fan-out legacy batch เป็น Official single POST โดยไม่สร้าง session ซ้ำ

## Prompt

```text
Implement reading-session adapter.
Legacy batch → deterministic Official single-session POSTs + aggregate results.

Create canonical idempotency key from preserved legacy identity: server/user/book/hash/start/end/device (change only with evidence).
Persist pending/committed state in SQLite.
Timeout after POST must not blindly create duplicate on retry.
Preserve unsupported legacy fields locally only if needed for reconciliation: book_hash, device, device_id, current_page, total_pages.

Tests: single, batch, duplicate retry, timeout after possible commit, restart pending, partial success, same timestamps different devices, invalid duration/order.
Do not touch fork DB yet. Update HANDOFF.
```
