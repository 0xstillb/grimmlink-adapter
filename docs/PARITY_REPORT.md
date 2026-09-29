# GrimmLink Adapter — E2E Parity Report

เอกสารนี้เป็นแบบฟอร์มบันทึกผลจาก `docs/E2E_CHECKLIST.md` ห้ามใส่ password,
token, refresh token หรือ MD5 key

## Run metadata

| Field | Value |
| --- | --- |
| Date/time | 2026-09-29 10:32:19 +07:00 |
| Operator | Codex automated gate; live E2E operator NOT_RUN |
| Adapter commit | `479a9eb` |
| Adapter image tag/ID | NOT_RUN |
| GrimmLink plugin commit/release | NOT_RUN |
| Official Grimmory version/image | NOT_RUN |
| KOReader version/device A | NOT_RUN |
| KOReader version/device B | NOT_RUN |
| Test environment | Disposable/staging only |
| Test account/library | Record non-secret identifier only |

## Safety preflight

| Check | Result | Evidence/notes |
| --- | --- | --- |
| Disposable library and account confirmed | NOT_RUN | |
| Production data excluded | NOT_RUN | |
| Adapter commit/image verified | NOT_RUN | |
| Fixtures and checksums recorded | NOT_RUN | |
| Logs enabled without secrets | NOT_RUN | |

## Summary

| Area | Result | Evidence/notes |
| --- | --- | --- |
| Automated quality gate | PASS | 298 pytest tests, Ruff, mypy, and `git diff --check` passed |
| Authentication/account isolation | NOT_RUN | |
| Legacy contract/capabilities | NOT_RUN | |
| Book identity/hash safety | NOT_RUN | |
| EPUB native progress | NOT_RUN | |
| EPUB Web Reader projection | NOT_RUN | Known XPointer → CFI issue |
| PDF progress | NOT_RUN | |
| Shelves/download | NOT_RUN | |
| Shelf mutation/cleanup | NOT_RUN | |
| Ratings/bookmarks/annotations | NOT_RUN | |
| Reading sessions | NOT_RUN | |
| OPF ingestion | NOT_RUN | |
| Restart/offline/recovery | NOT_RUN | |
| Security/observability | NOT_RUN | |
| Production smoke | NOT_RUN | Run only after staging gate |

## Detailed results

เพิ่มหนึ่งแถวต่อ checklist item ที่รันจริง

| ID/check | Result | Request/action | Expected | Observed | Evidence |
| --- | --- | --- | --- | --- | --- |
| Example: EPUB-A-to-B | NOT_RUN | | | | timestamp/log/screenshot/checksum |
| AUTO-PYTEST | PASS | `.venv/Scripts/python -m pytest -q --basetemp .codex-tmp/pytest-adapter-full-479a9eb` | Full suite passes | 298 passed, 2 warnings, 61.96s | Command output from this run |
| AUTO-RUFF | PASS | `.venv/Scripts/python -m ruff check .` | No lint errors | All checks passed | Command output from this run |
| AUTO-MYPY | PASS | `.venv/Scripts/python -m mypy src` | Strict typecheck passes | No issues in 49 source files | Command output from this run |
| AUTO-DIFF | PASS | `git diff --check` | No whitespace errors | Passed | Command output from this run |

Automated warnings: one Starlette deprecation warning for
`HTTP_422_UNPROCESSABLE_ENTITY`, plus a pytest cache write warning caused by
local Windows permissions. Neither warning failed a test.

## Known issues และ accepted differences

| Issue | Classification | Impact | Evidence | Follow-up |
| --- | --- | --- | --- | --- |
| Official XPointer → EPUB CFI conversion may leave Web Reader stale | FAIL — KNOWN ISSUE | Native KOReader sync may pass while Web Reader remains at an older CFI | `docs/PROGRESS_SYNC_STATUS.md` | Not fixed in this test run |

## Data integrity reconciliation

| Entity | Before | Expected delta | After | Result/evidence |
| --- | ---: | ---: | ---: | --- |
| Books/files | NOT_RUN | | NOT_RUN | |
| Shelf memberships | NOT_RUN | | NOT_RUN | |
| Ratings | NOT_RUN | | NOT_RUN | |
| Bookmarks/annotations | NOT_RUN | | NOT_RUN | |
| Reading sessions | NOT_RUN | | NOT_RUN | |
| Pending outbox rows | NOT_RUN | | NOT_RUN | |

## Blockers

- None recorded yet; replace this line if a blocker is found.

## Final decision

- Overall: `IN_PROGRESS — AUTOMATED PASS, LIVE E2E NOT_RUN`
- Data-loss/sync-safety blockers: `UNKNOWN`
- Reviewer: `NOT_RUN`
- Decision: `NOT_RUN`

