# Session 11 — Controlled Cutover + First Release

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** GPT-5.6 Sol High leads
> - **Reviewer:** SOL FINAL CUTOVER GATE
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High

## Objective
สลับ production แบบ rollback ได้

## Prompt

```text
Preconditions: parity green, fork export verified, adapter image pinned, rollback tested, Official stock.

Cutover:
1. backup adapter SQLite/config + plugin settings
2. keep fork available/read-only where possible
3. point ONE canary device to adapter
4. run full sync cycle
5. confirm no unintended write/delete
6. expand devices
7. verify no runtime requests hit fork `/api/grimmlink/v1`
8. keep rollback window

Release: semver, immutable Docker tag, release notes, config migration, healthcheck, backup/restore docs, changelog.
No force-push. Do not delete fork repo/DB. Update HANDOFF with exact rollback steps.
```
