# GrimmLink → Official Grimmory Migration Plan

- **Version:** 1.0.0
- **Execution Model:** Gemini Flash 3.8 Implementer + GPT-5.6 Sol High Review Gates
- **Upstream Target:** Stock Official Grimmory `v3.5.0+`

---

## 1. Executive Summary

This document specifies the complete 13-session roadmap to transition GrimmLink from a custom Grimmory fork (`0xstillb/grimmory`) to **unmodified Official Grimmory** via the standalone `grimmlink-adapter`.

### Core Non-Negotiables
1. **Stock Official Grimmory:** Zero server modifications, zero custom Flyway migrations in Grimmory.
2. **Zero Direct DB Access:** All interactions occur strictly over Official HTTP REST APIs.
3. **No Premature Deletion:** Fork repositories, containers, and databases remain active until the end-to-end canary in Session 10 passes.
4. **Data Safety First:** Never lose reading progress, custom shelves, bookmarks, or reading sessions.
5. **Idempotent Operations:** All mutations must be safely retryable across restarts and timeouts.

---

## 2. Session Roadmap & Review Gates

| Session | Title | Focus Area | Implementer | Required Review Gate |
|---|---|---|---|---|
| **00** | **Repo Bootstrap & Architecture Freeze** | Scaffold repo, architecture invariants, ADR-0001, CI, Docker | Gemini Flash 3.8 | **Sol Architecture Review** |
| **01** | **Contract Freeze** | Legacy wire contract specification, executable contract tests, fixtures | Gemini Flash 3.8 | Sol Contract Completeness Review |
| **02** | **Auth & Official Client** | MD5-to-Bearer token bridge, KOReader auth, TokenCache | Gemini Flash 3.8 | **Sol Deep Auth Review** |
| **03** | **Book Identity** | Hash-to-bookId resolution, accessible library check, BookHashCache | Gemini Flash 3.8 | **Sol Deep Hash Review** |
| **04** | **Shelf Read Sync** | Regular shelves + Magic shelves pagination and aggregation | Gemini Flash 3.8 | Sol Shelf Review |
| **05** | **Shelf Mutation & Cleanup** | Bulk assignment, Magic shelf read-only rule, multi-shelf file safety | Gemini Flash 3.8 | **Sol Safety Review** |
| **06** | **Progress Normalization** | EPUB CFI/XPointer authoritative, page ratio calculation (55/16653 ≈ 0.33%), PDF projection | Gemini Flash 3.8 | **Sol Deep Progress Review** |
| **07** | **Metadata Sync** | Ratings (1-10 to 1-5), bookmarks CRUD, annotations, deduplication | Gemini Flash 3.8 | **Sol Mapping Review** |
| **08** | **Reading Sessions** | Canonical idempotency keys, single/batch session recording | Gemini Flash 3.8 | **Sol Idempotency Review** |
| **09** | **Fork State Preservation** | Export V9001/V9002 fork tables, backfill into Official or adapter cache | Gemini Flash 3.8 | **Sol Data-Loss Review** |
| **10** | **E2E Parity & Canary** | Matrix testing across EPUB and PDF, multi-device sync verification | Gemini Flash 3.8 | **Sol Parity Review** |
| **11** | **Cutover & Release** | Switch KOReader plugin URL to adapter, production canary | Sol Leads | **Sol Final Cutover Gate** |
| **12** | **Post-Cutover Cleanup** | Archive fork repositories, decommission fork containers | Gemini Flash 3.8 | **Sol Final Retirement Audit** |

---

## 3. Risk Matrix & Mitigations

| Risk | Severity | Mitigation Strategy |
|---|---|---|
| **Secret Leakage in Logs** | High | Application-wide `SecretMaskingFilter` installed at startup; automated test assertions ensuring tokens are redacted. |
| **Incorrect Display Percentage** | High | Strict mathematical formula enforced: `(current_page / total_pages) * 100`. Verified by unit tests. |
| **Accidental Local File Deletion on Multi-Shelf** | Critical | Composite tracking `(book_id, shelf_id, shelf_type)` in `shelf_ownership_cache`. Removal from one shelf retains file if other shelves track it. |
| **Duplicate Reading Sessions on Retry** | High | Deterministic SHA-256 idempotency key generated per session. Existing keys return recorded success without duplicating records. |
| **Premature Fork Decommissioning** | Critical | Strict rule: Fork server and DB remain untouched until Session 11 cutover and verification pass. |
