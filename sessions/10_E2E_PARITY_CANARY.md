# Session 10 — E2E Parity + Canary

> ## Execution rule — Hermes E2E run, Sol gate
>
> - **E2E runner:** Hermes runs the parity matrix and records evidence; do not merge or cut over as part of the run.
> - **Reviewer:** SOL PARITY REVIEW REQUIRED; no cutover without approval.
> - Hermes must use a disposable/test Official Grimmory library, update `docs/PARITY_REPORT.md` and `docs/HANDOFF.md`, then stop for review.
> - If a check fails, report the failing request/behavior and a focused remediation proposal; do not silently redesign the adapter or plugin.
> - GPT-5.6 Sol High reviews the report, tests, HANDOFF, and data-safety implications, then issues either `APPROVE` or a focused fix prompt.
> - Merge/cut over only after the required Sol gate passes.
>


**Recommended model:** Hermes for the E2E run; GPT-5.6 Sol High for design/review.

## Objective
พิสูจน์ผลลัพธ์เทียบ fork ก่อน cutover

## Prompt

```text
Run E2E against a disposable/test Official library first. Never use production.

Use `docs/E2E_CHECKLIST.md` as the executable master checklist and record every
result in `docs/PARITY_REPORT.md`. A matrix line is not complete until its
observable result and evidence are recorded as PASS, FAIL, ACCEPTED_DIFFERENCE,
BLOCKER, or NOT_RUN.

Client under test: the existing GrimmLink KOReader plugin from
https://github.com/0xstillb/GrimmLink, unchanged, pointed at this Adapter's
`/api/grimmlink/v1` base URL. The Main branch currently identifies as v2.0.0;
record the exact plugin commit/release used. The compatibility review found the
same v1 route namespace and MD5-style `x-auth-user` / `x-auth-key` client
contract, so do not patch the plugin preemptively.

New-install setup is not Session 09 migration: create/use a disposable Official
user and test library, configure the Adapter against that Official test server,
and run `python -m grimmlink_adapter.link_account <username>` locally with the
same test account before connecting the plugin. This provisions the local
MD5-to-Official identity link; do not paste credentials or tokens into this
repository, report, or chat.

Preflight required before any writes: confirm the Adapter is deployed at the
commit under test; confirm the Official staging URL, test account, KOReader
device/profile, and disposable books/shelves are available. If any are missing,
stop and report the exact blocker rather than using production data.

Matrix:
- EPUB native location push/pull
- EPUB 55/16653 ≈ 0.33%
- PDF page/progress/conflict
- manual read-status precedence
- regular shelf download
- magic shelf multipage
- same book in multiple shelves
- regular removal
- magic removal stays read-only
- rating push/pull/reset
- bookmark CRUD/pull
- annotation roundtrip
- reading session single/batch/retry
- adapter restart with pending ops
- auth refresh
- offline→online

Verify OPF separately:
OPF → Grimmory Bridge → .metadata.json/.cover.jpg → Official → Import All
Check series/ISBN/cover/locks/hash safety.

Produce docs/PARITY_REPORT.md with PASS / FAIL / ACCEPTED_DIFFERENCE / BLOCKER.
Do not cut over with data-loss/sync-safety blockers. Update HANDOFF.
```
