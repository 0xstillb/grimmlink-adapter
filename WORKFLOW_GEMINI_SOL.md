# WORKFLOW — Gemini First, GPT-5.6 Sol Review Gates

## Core workflow

ทุก session ใช้ flow นี้:

```text
Gemini Flash 3.8
  ↓ inspect existing code/tests/docs
  ↓ implement ONLY current session
  ↓ run focused tests
  ↓ run broader tests if feasible
  ↓ update docs/HANDOFF.md
  ↓ STOP — no merge

GPT-5.6 Sol High
  ↓ review diff + tests + HANDOFF
  ↓ check architecture / regressions / data safety
  ↓ APPROVE
     or
  ↓ issue focused FIX prompt

Gemini Flash 3.8
  ↓ apply only reviewed fixes
  ↓ rerun tests
  ↓ update HANDOFF

GPT-5.6 Sol High
  ↓ final check when session requires deep gate
  ↓ merge allowed
```

## Why not review only at the end?

ถ้า auth, book identity, shelf ownership หรือ progress semantics ผิดตั้งแต่ต้น
session หลัง ๆ จะสร้างบนฐานที่ผิดและแก้ย้อนกลับแพงกว่า

## Review gates

| Session | Main implementer | Required gate |
|---|---|---|
| 00 Repo/bootstrap | Gemini Flash 3.8 | Sol architecture review |
| 01 Contract freeze | Gemini Flash 3.8 | Sol contract completeness review |
| 02 Auth/JWT/MD5 | Gemini Flash 3.8 | **Sol deep review** |
| 03 Hash → bookId | Gemini Flash 3.8 | **Sol deep review** |
| 03A OPF ingestion | Gemini Flash 3.8 | **Sol deep review** |
| 04 Shelf read | Gemini Flash 3.8 | Sol review |
| 05 Shelf mutation/cleanup | Gemini Flash 3.8 | **Sol safety review** |
| 06 EPUB/PDF progress | Gemini implements locked spec | **Sol deep progress review** |
| 07 Metadata sync | Gemini Flash 3.8 | **Sol mapping/dedupe review** |
| 08 Reading sessions | Gemini Flash 3.8 | **Sol idempotency review** |
| 09 Fork data preservation | Gemini Flash 3.8 | **Sol data-loss review** |
| 10 E2E parity | Gemini runs matrix | **Sol parity review** |
| 11 Cutover | Sol leads | **Sol final cutover gate** |
| 12 Retirement | Gemini mechanical work | **Sol final retirement audit** |

## Critical non-negotiables

- Official Grimmory remains stock.
- No direct DB write.
- No force-push/history rewrite.
- Preserve user work.
- Small cherry-pick-friendly commits.
- Magic Shelf removal remains rule-derived/read-only.
- Multi-shelf ownership safety must remain intact.
- EPUB native location/CFI/XPointer remains authoritative.
- EPUB display percent from reliable page data:
  `currentPage / totalPages * 100`
  Example: `55 / 16653 ≈ 0.33%`, not `33%`.
- PDF page/progress/conflict semantics must be preserved.
- Metadata/session retries must be idempotent.
- Fork state must be exported/verified before retirement.


## OPF ingestion architecture

หลัง Session 03 Book Identity ให้ทำ Session 03A:

```text
OPF
 ↓
Adapter parser/normalizer
 ↓
Exact bookId
 ↓
Official Metadata API (PRIMARY)
 ↓ failure under approved fallback conditions only
Official-compatible .metadata.json/.cover.jpg (FALLBACK)
```

Sidecar fallback ต้องไม่ถูกใช้เพื่อกลบ identity/auth/lock errors.

