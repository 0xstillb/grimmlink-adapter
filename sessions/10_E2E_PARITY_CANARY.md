# Session 10 — E2E Parity + Canary

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8 runs/fixes scoped issues
> - **Reviewer:** SOL PARITY REVIEW REQUIRED; no cutover without approval
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High for design/review; Flash can run mechanical matrix

## Objective
พิสูจน์ผลลัพธ์เทียบ fork ก่อน cutover

## Prompt

```text
Run E2E against disposable/test Official library first.

Matrix:
- EPUB native location push/pull
- EPUB 55/16653 ≈ 0.33%
- PDF page/progress/conflict
- manual read-status precedence
- regular shelf download
- magic shelf multipage
- same book in multiple shelves
- regular removal
- magic removal stays read-only
- rating push/pull/reset
- bookmark CRUD/pull
- annotation roundtrip
- reading session single/batch/retry
- adapter restart with pending ops
- auth refresh
- offline→online

Verify OPF separately:
OPF → Grimmory Bridge → .metadata.json/.cover.jpg → Official → Import All
Check series/ISBN/cover/locks/hash safety.

Produce docs/PARITY_REPORT.md with PASS / FAIL / ACCEPTED_DIFFERENCE / BLOCKER.
Do not cut over with data-loss/sync-safety blockers. Update HANDOFF.
```
