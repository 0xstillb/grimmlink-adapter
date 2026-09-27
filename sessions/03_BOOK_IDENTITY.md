# Session 03 — Book Identity

> ## Execution rule — Gemini first, Sol gate
>
> - **Implementer:** Gemini Flash 3.8
> - **Reviewer:** SOL DEEP REVIEW REQUIRED before merge
> - Gemini must inspect, implement, test, and update `docs/HANDOFF.md`, then **STOP before merge**.
> - Gemini must not silently redesign architecture when blocked.
> - GPT-5.6 Sol High reviews the diff, tests, HANDOFF, data-safety implications, and issues either `APPROVE` or a focused fix prompt.
> - Merge only after required Sol gate passes.
>


**Recommended model:** GPT-5.6 Sol High

## Objective
ย้าย currentHash → initialHash → bookId โดยไม่จับหนังสือผิดเล่ม

### Approved read-only identity source exception (2026-09-26)

The user approved resolving hash misses from Grimmory MariaDB after the Pi
read-only audit confirmed the schema. This is a narrowly scoped exception to
the original “without direct DB access” prompt:

- The Adapter may issue exact parameterized `SELECT` queries only against
  `book`, `book_file`, `library`, and `library_path`, using a dedicated
  SELECT-only account on a restricted network.
- Keep Official Grimmory stock. Never write to its DB, files, or configuration.
- Search `current_hash` first; search `initial_hash` only when current has no
  match. Filter deleted books and `is_book != 1`; fail on multiple matches.
- A DB row is only an identity candidate. Verify book/file identity and access
  with an Official Bearer token for that same user before returning book details.
- When configured, Grimmory DB lookup takes precedence over Adapter cache:
  current hash, then initial hash. An unavailable DB fails closed with 502.
- `book_file_id` may refer to `primaryFile` or `alternativeFormats`.
- MD5-only clients use a locally linked same-user JWT. Run
  `python -m grimmlink_adapter.link_account <username>` after deployment;
  the command verifies both authentication modes before saving tokens.
- Do not use the broad Grimmory application DB account. DB lookup stays
  disabled until explicit read-only credentials/network settings are supplied.

Operational canary against the Pi remains required before marking this session
fully deployed: the audit found no read-only account or Adapter deployment.
Protect the Adapter SQLite file because it contains Official tokens.
For the pass canary, use an active book whose stored `current_hash` matches
the partial MD5 of its current file bytes. Book 25's stored `d654…` does not
match its current bytes (`95b8…`), so a 200 response for that hash alone is
insufficient to prove file-byte identity. See `docs/HANDOFF.md` section 5.

## Prompt

```text
Implement the user-approved, optional read-only MariaDB mapping source.
Query exact current_hash first, then initial_hash only if current has no match.
Use only a dedicated SELECT-only DB account on a restricted network.
Verify every candidate's book/file ID and access with a same-user Official Bearer token.
Never write to Official DB or silently choose an ambiguous match.

Cache verified mappings in adapter SQLite scoped by server/user.
Do not match by filename, path, or metadata.
Keep DB lookup disabled until explicit read-only credentials are configured.
Expose legacy-compatible `/books/by-hash/{hash}` only in the adapter.

Tests: exact currentHash, initialHash fallback, stale id, inaccessible candidate, ambiguity, rename, server/user isolation, restart persistence.
Document algorithm/failures. Update HANDOFF.
```
