# Inter-Session Handoff & Governance

- **Current Session:** `Session 00 — New Repo Bootstrap + Architecture Freeze`
- **Implementer:** Gemini Flash 3.8
- **Reviewer:** GPT-5.6 Sol High
- **Current Lifecycle State:** `STOPPED — Ready for Sol Re-Review Gate (Post-Fixes)`
- **Timestamp:** 2026-09-26T12:48:00+07:00

---

## 1. Response to FIX_REQUIRED Review

In response to the Sol High review gate, all 4 critical issues and the OPF architecture alignment have been fully addressed and verified by automated regression tests:

### Fix 1: Strict Auth Verification & Rejection
- **Issue:** `auth_service.py` caught upstream exceptions and silently returned `status="ok", userId=1`, and accepted Bearer tokens without upstream verification.
- **Fix:**
  - Upstream KOReader authentication failures (401, 403) now immediately raise `HTTPException(401, "Invalid username or MD5 authentication key.")`.
  - Unreachable upstream (e.g. connection refused, network timeout) now raises `HTTPException(502, "Upstream Official Grimmory service is unreachable.")`.
  - Bearer tokens are validated against Official Grimmory (`GET /api/v1/users/me`) via `OfficialGrimmoryClient.get_current_user`; invalid tokens raise 401.
  - Zero silent success fallbacks.
- **Tests:** Added `test_auth_endpoint_rejects_invalid_credentials`, `test_auth_endpoint_fails_when_upstream_unreachable`, and `test_auth_endpoint_success_with_valid_upstream`.

### Fix 2: Unimplemented Mutations Return 501 & Do Not Mutate State
- **Issue:** Progress, metadata, session, and shelf endpoints falsely reported success without executing mutations, recorded premature idempotency keys, and modified SQLite ownership cache without upstream unassignment. Capabilities falsely claimed support.
- **Fix:**
  - `capabilities.py`: Explicitly advertises `progressSync: false`, `readingSessions: false`, `metadataSync: false`, `pdfBridge: false`. Only read operations (`shelves: true`) are claimed.
  - `progress_service.py`: `update_progress` raises `HTTPException(501)` with note that progress mutation is activated in Session 06.
  - `metadata_service.py`: `sync_metadata` and `sync_metadata_batch` raise `HTTPException(501)` with note that metadata mutation is activated in Session 07.
  - `session_service.py`: `record_session` and `record_sessions_batch` raise `HTTPException(501)` and **strictly do not record idempotency keys** for unexecuted mutations.
  - `shelf_service.py`: Regular shelf removal raises `HTTPException(501)` and **strictly does not delete records from `ShelfOwnershipCache`** before upstream unassign is executed. Magic shelf removal continues to return 400 Bad Request (rule-derived).
- **Tests:** Added `test_regular_shelf_removal_returns_501_in_scaffold`, `test_progress_sync_contract` (PUT returns 501), `test_metadata_sync_mutations_return_501_in_scaffold`, `test_reading_sessions_batch_returns_501_in_scaffold`, `test_session_idempotency_not_created_in_scaffold`, and `test_shelf_ownership_not_altered_when_unimplemented`.

### Fix 3: Secret Masking Filter Prevents Leaks via Format Arguments
- **Issue:** `SecretMaskingFilter` masked `record.msg` separately from `record.args`. When strings contained placeholders like `%s`, `mask_secret` altered the format string, causing format errors or logging raw unredacted arguments to stderr.
- **Fix:**
  - In `SecretMaskingFilter.filter(record)`: evaluates `record.getMessage()` first to produce the fully formatted string, passes the complete string through `mask_secret()`, updates `record.msg`, and **empties `record.args = ()`**.
  - This eliminates placeholder mismatch crashes and guarantees that stderr or downstream handlers never receive raw argument tuples or dictionaries.
- **Tests:** Added `test_logging_filter_scrubs_records_with_positional_args` (verifying `Bearer %s` and `x-auth-key: %s`) and `test_logging_filter_scrubs_records_with_dict_args`.

### Fix 4: Containerized Migration Resolution & Fail-Fast Startup
- **Issue:** `migrations.py` used a relative path based on `__file__` that resolved to `/usr/local/lib` inside containerized wheel installations instead of `/app/migrations`. When not found, it logged a warning and returned 0, allowing unmigrated containers to pass healthcheck.
- **Fix:**
  - Added `MIGRATIONS_DIR` setting to `Settings` with multi-path candidate resolution (`settings.migrations_dir_resolved`), checking `/app/migrations`, local working directories, and repo paths.
  - `apply_migrations()` now **raises `FileNotFoundError`** if the migrations directory or `.sql` files are missing, refusing to start with an unmigrated database.
  - In `Dockerfile`: added `ENV MIGRATIONS_DIR=/app/migrations` and `COPY --chown=adapter:adapter migrations /app/migrations`.
  - In `docker-compose.example.yml`: added `MIGRATIONS_DIR=/app/migrations`.
  - In `main.py`: lifespan re-raises migration exceptions and logs `CRITICAL` before aborting startup.
- **Tests:** Added `test_migrations_raise_on_missing_or_empty_dir` and `test_startup_fails_when_migrations_fail`.

### Fix 5: Unified OPF Architecture Across All Documentation
- **Issue:** Contradiction between `sessions/00_REPO_BOOTSTRAP.md` ("OPF is out of scope"), `README.md` ("OPF อยู่ใน Bridge ไม่ย้ายเข้า adapter"), and `ADR-0001` / `03A_OPF_API_WITH_SIDECAR_FALLBACK.md`.
- **Fix:**
  - All documents now declare the single, unified architecture:
    - **OPF Ingestion (Session 03A):** Handled inside `grimmlink-adapter`.
    - **PRIMARY:** Official Grimmory Metadata & Cover HTTP APIs (`PUT /api/v1/books/{id}/metadata`, `POST /api/v1/books/{id}/metadata/cover/upload`).
    - **FALLBACK:** Official-compatible Sidecar JSON (`.metadata.json` + `.cover.jpg`) for Import All (used strictly under approved fallback conditions, never to mask auth/identity/lock errors).
    - Source ebook files (EPUB, PDF, CBZ) are **never** modified.
    - Grimmory Bridge remains available as an external preview/standalone tool.
  - Aligned files: `sessions/00_REPO_BOOTSTRAP.md`, `README.md`, `docs/ADR/ADR-0001-external-adapter-choice.md`, `docs/ARCHITECTURE.md`, `docs/MIGRATION_PLAN.md`, and `docs/HANDOFF.md`.

---

## 2. Verification Evidence

### Pytest Execution (`uv run pytest -v`)
```text
============================= test session starts =============================
platform win32 -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
collected 33 items

tests/contract/test_legacy_routes.py::test_auth_endpoint_requires_credentials PASSED [  3%]
tests/contract/test_legacy_routes.py::test_auth_endpoint_rejects_invalid_credentials PASSED [  6%]
tests/contract/test_legacy_routes.py::test_auth_endpoint_fails_when_upstream_unreachable PASSED [  9%]
tests/contract/test_legacy_routes.py::test_auth_endpoint_success_with_valid_upstream PASSED [ 12%]
tests/contract/test_legacy_routes.py::test_capabilities_endpoint_contract PASSED [ 15%]
tests/contract/test_legacy_routes.py::test_shelves_list_contract PASSED  [ 18%]
tests/contract/test_legacy_routes.py::test_magic_shelf_removal_rejection_invariant PASSED [ 21%]
tests/contract/test_legacy_routes.py::test_regular_shelf_removal_returns_501_in_scaffold PASSED [ 24%]
tests/contract/test_legacy_routes.py::test_progress_sync_contract PASSED [ 27%]
tests/contract/test_legacy_routes.py::test_metadata_sync_mutations_return_501_in_scaffold PASSED [ 30%]
tests/contract/test_legacy_routes.py::test_reading_sessions_batch_returns_501_in_scaffold PASSED [ 33%]
tests/integration/test_app_lifecycle.py::test_healthcheck_endpoint PASSED [ 36%]
tests/integration/test_app_lifecycle.py::test_openapi_schema_generated PASSED [ 39%]
tests/integration/test_app_lifecycle.py::test_startup_fails_when_migrations_fail PASSED [ 42%]
tests/unit/test_cache_and_state.py::test_book_hash_cache_roundtrip PASSED [ 45%]
tests/unit/test_cache_and_state.py::test_token_cache_roundtrip PASSED    [ 48%]
tests/unit/test_cache_and_state.py::test_multi_shelf_ownership_invariant PASSED [ 51%]
tests/unit/test_cache_and_state.py::test_idempotency_manager PASSED      [ 54%]
tests/unit/test_cache_and_state.py::test_outbox_queue_lifecycle PASSED   [ 57%]
tests/unit/test_cache_and_state.py::test_migrations_raise_on_missing_or_empty_dir PASSED [ 60%]
tests/unit/test_cache_and_state.py::test_session_idempotency_not_created_in_scaffold PASSED [ 63%]
tests/unit/test_cache_and_state.py::test_shelf_ownership_not_altered_when_unimplemented PASSED [ 66%]
tests/unit/test_progress_calculation.py::test_progress_display_percentage_ratio PASSED [ 69%]
tests/unit/test_progress_calculation.py::test_progress_display_percentage_boundaries PASSED [ 72%]
tests/unit/test_progress_calculation.py::test_rating_normalization PASSED [ 75%]
tests/unit/test_progress_calculation.py::test_session_idempotency_key_deterministic PASSED [ 78%]
tests/unit/test_security_masking.py::test_mask_secret_bearer_token PASSED [ 81%]
tests/unit/test_security_masking.py::test_mask_secret_x_auth_key PASSED  [ 84%]
tests/unit/test_security_masking.py::test_mask_secret_json_body PASSED   [ 87%]
tests/unit/test_security_masking.py::test_client_credentials_repr_redaction PASSED [ 90%]
tests/unit/test_security_masking.py::test_logging_filter_scrubs_records_without_args PASSED [ 93%]
tests/unit/test_security_masking.py::test_logging_filter_scrubs_records_with_positional_args PASSED [ 96%]
tests/unit/test_security_masking.py::test_logging_filter_scrubs_records_with_dict_args PASSED [100%]

============================= 33 passed in 0.82s ==============================
```

### Static Analysis
- `uv run ruff check .` → **All checks passed!**
- `uv run mypy src` → **Success: no issues found in 35 source files**

---

## 3. Current Lifecycle State

- **`STOPPED — Awaiting Sol Review Gate (No Merge Yet)`**
- Implementer has completed all requested fixes, executed test matrix, committed changes, and paused.
