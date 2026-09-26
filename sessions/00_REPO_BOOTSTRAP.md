# Session 00 — New Repo Bootstrap + Architecture Freeze

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8
> - **Reviewer:** SOL REVIEW REQUIRED before Session 01
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High; Gemini Flash 3.8 สำหรับ scaffold หลัง approve

## Objective
สร้าง repo ใหม่ แยกจาก fork และล็อก architecture ก่อนเขียน feature จริง

## Prompt

```text
Start a NEW repository `grimmlink-adapter`.

References:
- 0xstillb/grimmory
- grimmory-tools/grimmory
- 0xstillb/GrimmLink
- 0xstillb/grimmory-bridge
- references/OFFICIAL_GRIMMORY_API_PARITY_AUDIT.md
- references/DEFORK_FEATURE_AUDIT.md

Target:
KOReader/current GrimmLink → standalone adapter → Official Grimmory HTTP API only.
Official Grimmory stays stock. No direct DB access/write. OPF is out of scope.

Default stack: Python 3.12, FastAPI, httpx, SQLite, pydantic, pytest, Docker.

Create:
README.md, pyproject.toml, src/grimmlink_adapter/{api,official,services,state,models,security}, tests/{contract,unit,integration}, migrations/, docs/{ARCHITECTURE.md,ADR/,HANDOFF.md,MIGRATION_PLAN.md}, Dockerfile, docker-compose.example.yml, .env.example, CI lint/typecheck/test.

Rules:
- preserve current GrimmLink wire contract initially where practical
- compatibility routes live ONLY in adapter, never Official
- secrets never logged
- SQLite = cache/outbox/idempotency, not Official source of truth
- small commits, no force push
- no real Official mutations yet

Write ADR-0001 explaining external adapter choice.
Run lint/tests. Commit scaffold only. Update HANDOFF.
```
