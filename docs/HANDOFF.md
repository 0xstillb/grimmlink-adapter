# Inter-Session Handoff & Governance

- **Current Session:** `Session 02 — Official Auth + HTTP Client`
- **Implementer:** Antigravity (Gemini)
- **Reviewer:** Codex (Session 02 review and repair)
- **Current Lifecycle State:** `Session 02 repaired and verified — STOP before merge`
- **Timestamp:** 2026-09-26

---

## 1. Summary of Session 02 Deliverables

In accordance with `sessions/02_AUTH_AND_OFFICIAL_CLIENT.md` and user directives:
- **Official Grimmory Transport Only:** Implemented stock Official Grimmory HTTP transport with zero direct DB access.
- **Strictly Separated Auth Modes:** Dedicated routing and credential handling separating general `/api/v1/**` JWT Bearer from `/api/koreader/**` MD5 authentication.
- **No Shelf/Progress/Metadata Behavior Yet:** Preserved scaffold stubs; no business logic or mutations for shelves, progress, or metadata were introduced.
- **Security Non-Negotiables:** Secrets masking, TLS verification default with explicit opt-in, exponential backoff retry, header redaction, and infinite refresh loop prevention.

### Deliverable A: Auth Models & Strict Separation (`src/grimmlink_adapter/official/auth.py`)
- `AuthMode`: Enumeration (`JWT`, `KOREADER`, `NONE`).
- `JWTAuth`: Pydantic model managing `username`, `password`, `access_token`, `refresh_token`, and `expires_at`.
  - `parse_jwt_expiry`: Extracts standard `exp` claim from unverified JWT base64url payload for proactive expiry detection.
  - `is_expired(buffer_seconds=30.0)`: Checks token expiration with safety buffer.
  - Masked `__repr__` and `__str__` redacting password, access_token, and refresh_token.
- `KOReaderAuth`: Pydantic model managing `username` and `md5_key` with header generator `get_headers()` (`x-auth-user`, `x-auth-key`) and masked `__repr__`.
- `determine_auth_mode_for_path`: Automatically classifies paths (`/api/koreader/**` -> `KOREADER`, `/api/v1/**` -> `JWT`, others -> `NONE`).
- `validate_auth_separation`: Rejects cross-domain auth mixing (e.g. JWT with KOReader endpoint or KOReader MD5 with general API) with `ValueError`.

### Deliverable B: Typed Exception Hierarchy (`src/grimmlink_adapter/official/exceptions.py`)
- `OfficialClientError`: Base exception scrubbing `message`, `url`, and `response_body` using `mask_secret`. Never reveals raw credentials or tokens.
- `OfficialAuthError`: Raised on 401 Unauthorized, invalid login, token refresh failure, or persistent 401 after retry.
- `OfficialPermissionError`: Raised on 403 Forbidden.
- `OfficialTimeoutError`: Raised when connect, read, or pool timeouts occur and retries are exhausted.
- `OfficialTransportError`: Raised on network failures, DNS resolution errors, connection refused, or TLS handshake failures.
- `OfficialBadResponseError`: Raised on malformed JSON responses, unexpected non-dict payloads, or unhandled 5xx server errors.

### Deliverable C: Official Grimmory Client Transport (`src/grimmlink_adapter/official/client.py`)
- **Strict Authentication Separation:**
  - Requests to `/api/v1/**` automatically inject `Authorization: Bearer <token>` and strip any `x-auth-user` / `x-auth-key`.
  - Requests to `/api/koreader/**` automatically inject `x-auth-user` + `x-auth-key` and strip any `Authorization` header.
- **Token Management & 401 Safe Refresh:**
  - `login()`: POST `/api/v1/auth/login` updates JWT tokens and extracts expiry.
  - `refresh_token()`: POST `/api/v1/auth/refresh` exchanges refresh token for new access token.
  - `_ensure_valid_jwt()`: Proactively refreshes expired tokens before dispatching requests.
  - **One Safe Refresh on 401:** When a protected `/api/v1/**` request receives 401, acquires `_refresh_lock`, executes `refresh_token()` (or `login()` fallback), and retries the request exactly once with `_is_401_retry=True`.
  - **Loop Prevention:** If retried request receives 401 again, immediately raises `OfficialAuthError`. Never enters infinite retry loops.
  - **KOReader 401 Invariant:** A 401 on `/api/koreader/**` immediately raises `OfficialAuthError` and never triggers JWT refresh.
- **TLS Verification & Self-Signed Opt-In:**
  - `ssl_verify = True` by default (`OFFICIAL_CLIENT_VERIFY_SSL`).
  - Supports custom CA bundle path (`OFFICIAL_CLIENT_CA_BUNDLE`).
  - Explicit self-signed opt-out (`verify_ssl=False`) logs a prominent warning.
- **Timeout & Retry with Exponential Backoff:**
  - Configurable timeouts (`OFFICIAL_CLIENT_TIMEOUT`, `OFFICIAL_CLIENT_CONNECT_TIMEOUT`).
  - Retries transient errors (`httpx.ConnectError`, `httpx.TimeoutException`, HTTP 502, 503, 504) for GET, HEAD, and OPTIONS with exponential backoff: `delay = backoff_factor * (2 ** (attempt - 1))`. Mutation methods are sent once.
  - Non-transient errors (400, 403, 422) fail immediately on attempt 1 without retry.
- **Read-Only Health Canaries:**
  - `check_health()`: GET `/api/v1/healthcheck` (verifies server UP).
  - `canary_jwt_auth()`: GET `/api/v1/users/me` with Bearer token (verifies JWT session).
  - `canary_koreader_auth()`: GET `/api/koreader/users/auth` with MD5 headers (verifies KOReader credentials).
  - All canaries strictly read-only GET requests with zero mutations.
- **Backward Compatibility:**
  - Helper methods (`get_koreader_auth`, `login_jwt`, `get_current_user`, `get_koreader_progress`, `get_book_by_id`, `get_regular_shelves`, `get_magic_shelves`, `download_book_stream`) preserved for existing services and contract tests.

### Deliverable D: Security Enhancements (`src/grimmlink_adapter/security/masking.py`)
- Added `redact_headers(headers)` masking `Authorization`, `x-auth-key`, `cookie`, `set-cookie`, `proxy-authorization`, and headers containing token/secret/password.
- Expanded `SENSITIVE_PATTERNS` to cover camelCase tokens (`refreshToken`, `accessToken`) and MD5 keys across JSON and key-value formats.

---

## 2. Verification Evidence

After review repairs on 2026-09-26:

- `uv run --no-sync pytest -q` → **121 passed**.
- `uv run --no-sync ruff check .` → **All checks passed**.
- `uv run --no-sync mypy src` → **No issues in 37 source files**.
- `git diff --check` → **No whitespace errors** (Git reports line-ending notices only).

The regression tests in `tests/unit/test_session02_review_regressions.py` cover case-insensitive auth headers, account switching, no retry for POST, malformed auth identities, 403 mapping, safe streaming errors, and isolation of caller-supplied bearer tokens.

---

## 3. Session 02 Scope & Invariant Checklist

| Requirement / Invariant | Status | Evidence |
|---|---|---|
| General `/api/v1/**` JWT login | Passed | `test_login_success`, `test_login_invalid_credentials_401` |
| Access / refresh token & expiry | Passed | `test_refresh_success`, `test_jwt_proactive_refresh_when_expired`, `parse_jwt_expiry` |
| One safe refresh on 401 | Passed | `test_jwt_401_triggers_one_refresh_and_succeeds` |
| No infinite refresh loop | Passed | `test_jwt_401_no_infinite_loop_on_persistent_401`, `test_jwt_401_refresh_fails_aborts_immediately` |
| `/api/koreader/**` x-auth-user + MD5 | Passed | `test_koreader_routing_uses_md5_headers_only`, `test_koreader_missing_credentials_raises_auth_error` |
| Strictly separate auth modes | Passed | `test_strict_auth_mode_separation_violations`, `test_koreader_401_never_attempts_jwt_refresh` |
| Never log secrets / redact headers | Passed | `test_security_redact_headers`, `test_security_exception_and_model_repr_masking`, `test_security_no_secret_leak_in_logs` |
| TLS verify ON / explicit opt-in only | Passed | `test_tls_verify_defaults_and_opt_in` |
| Timeout / retry with backoff | Passed | `test_transient_error_503_retries_and_succeeds`, `test_timeout_and_retry_exhaustion_raises_timeout_error` |
| Typed error hierarchy | Passed | `OfficialAuthError`, `OfficialPermissionError`, `OfficialTimeoutError`, `OfficialTransportError`, `OfficialBadResponseError` |
| Read-only health canaries | Passed | `test_read_only_health_canary`, `test_read_only_jwt_auth_canary`, `test_read_only_koreader_auth_canary` |
| Malformed response handling | Passed | `test_malformed_json_response`, `test_unexpected_json_shape` |
| No shelf/progress/metadata logic | Preserved | Stubs remain unchanged; zero DB writes or entity mutations |

---

## 4. Current Lifecycle State & Next Steps

Codex repaired the Session 02 review findings in the working tree:

- Header filtering now handles HTTP header names case-insensitively, including the 401 refresh path. Public endpoints discard caller-supplied bearer headers.
- Switching JWT accounts clears previous credentials and tokens; explicit login updates the account identity.
- Transient retries are limited to safe request methods. Streaming errors consume and close responses before typed error handling; the download helper uses that transport.
- Auth responses without a valid user ID or bearer username fail closed with 502. Upstream 403 maps to the legacy auth endpoint's 401 response.
- Protected paths reject public `AuthMode.NONE` calls. Internal helpers with caller-supplied bearer tokens do not refresh another account's stored JWT.

**STOP before merge.** Session 03 starts only after the Session 02 review gate is accepted.
