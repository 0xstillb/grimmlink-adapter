# SOL_REVIEW_TEMPLATE.md

Use this after Gemini finishes each session.

```text
Review the completed session as a gatekeeper. Do not implement unrelated features.

Read:
- current session prompt
- docs/HANDOFF.md
- git diff / commits from this session
- relevant tests
- OFFICIAL_GRIMMORY_API_PARITY_AUDIT.md
- DEFORK_FEATURE_AUDIT.md when applicable

Check:
1. scope compliance — no unrelated changes
2. architecture — Official Grimmory remains stock; no direct DB write
3. backward behavior — GrimmLink semantics preserved where required
4. state/data safety — no silent deletion, corruption, duplicate sync, or lossy mapping
5. auth/security — no secret leakage
6. tests — regression cases are meaningful, not superficial
7. handoff — exact status, blockers, next step
8. upstream independence — no hidden dependency on fork runtime unless explicitly still in migration phase

For progress sessions specifically verify:
- EPUB native location round-trip
- page/percent consistency
- 55/16653 ≈ 0.33%, not 33%
- percent-vs-fraction boundaries
- PDF page projection/conflict semantics

For shelf sessions specifically verify:
- complete snapshot requirement
- multi-shelf ownership
- magic shelf read-only removal
- managed-copy-only cleanup

For metadata/session sessions verify:
- dedupe/idempotency across timeout/retry/restart
- explicit delete/reset semantics
- device/cursor isolation

Output only:
VERDICT: APPROVE | FIX_REQUIRED | BLOCKED

Then:
- Critical issues
- Required fixes
- Tests to rerun
- Whether merge is allowed
```
