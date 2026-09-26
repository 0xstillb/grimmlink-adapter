# Session 12 — Fork Retirement

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8 for mechanical cleanup/docs
> - **Reviewer:** SOL FINAL RETIREMENT AUDIT REQUIRED
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High

## Objective
ปิด fork หลังพิสูจน์ว่าไม่มี runtime dependency และข้อมูลครบ

## Prompt

```text
After agreed stable observation window verify:
- no client requests fork endpoints
- no required behavior depends on V9001/V9002
- adapter backup restore works
- Official upgrades remain stock
- OPF uses Grimmory Bridge + Sidecar Import All
- parity tests remain green

Then mark fork runtime deprecated/read-only, stop publishing fork runtime images, archive fork-only docs, retain exported data and rollback artifacts for defined retention.
Do not delete historical Git repo unless explicitly requested.
Create docs/FORK_RETIREMENT_REPORT.md.
No destructive cleanup without explicit user approval.
```
