# Session 02 — Official Auth + HTTP Client

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8
> - **Reviewer:** SOL DEEP REVIEW REQUIRED before merge
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High for design/review; Flash for implementation after review

## Objective
ทำ transport layer ที่แยก JWT กับ KOReader MD5 อย่างปลอดภัย

## Prompt

```text
Implement Official Grimmory transport only.

General `/api/v1/**`: JWT login, access/refresh token, expiry, one safe refresh on 401.
`/api/koreader/**`: x-auth-user + MD5 x-auth-key only where Official requires it.
Strictly separate auth modes.

Security: never log password/token/refresh/MD5 key; redact headers; TLS verify ON; explicit self-signed opt-in only; timeout/retry/backoff; no infinite refresh loop.

Implement typed auth/permission/timeout/transport/bad-response errors and read-only health canaries.
Tests: login, refresh, 401 retry-once, KOReader routing, redaction, timeout/retry, malformed response.
No shelf/progress/metadata behavior yet. Update HANDOFF.
```
