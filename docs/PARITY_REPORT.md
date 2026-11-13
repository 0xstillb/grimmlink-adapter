# GrimmLink Adapter — E2E Parity Report

เอกสารนี้เป็นแบบฟอร์มบันทึกผลจาก `docs/E2E_CHECKLIST.md` ห้ามใส่ password,
token, refresh token หรือ MD5 key

## Current release and verification status — 2026-11-13

| Area | Result | Evidence / limits |
| --- | --- | --- |
| Adapter release | PASS | `v0.1.18`, commit `e14f7ee`; [GitHub Release](https://github.com/0xstillb/grimmlink-adapter/releases/tag/v0.1.18). |
| CI and ARM64 image | PASS | [Release workflow](https://github.com/0xstillb/grimmlink-adapter/actions/runs/43346752578): Ruff, Mypy, Python tests, Prose tests, and ARM64 image build/publish passed. GHCR publishes `0.1.18` and `latest`. |
| Changes covered by release | PASS — automated | Metadata pull converts valid EPUB CFI to KOReader XPointer when possible; deletion tombstones remain replayable; duplicate bookmark conflict recovery adopts only one exact match. CI proves automated checks, not device behavior. |
| Pi runtime | LAST DEPLOY CHECK: PASS; CURRENT STATE NOT RECHECKED | The 2026-11-13 Pi-local image `grimmlink-adapter:metadata-sync-20261113-epub-positions` was recorded as Docker `healthy`; `/healthcheck` reported version `0.1.18`. This was not the GHCR release image. SSH recheck while updating this report failed host-key verification, so present health and whether it was subsequently changed are unknown. |
| KOReader plugin | AUTOMATED PASS / DEVICE E2E NOT VERIFIED | Local plugin commit `0a9f6f5` passed Busted 252/252; it is not pushed/released, and EPUB bookmark appearance after pull still needs a device test. |
| Readest | NOT VERIFIED HERE | The Readest working tree has uncommitted changes; no build or client E2E result is included in this Adapter release report. |
| Reader-to-Grimmory metadata E2E | NOT RUN IN THIS RELEASE | No new writes against a live or disposable Grimmory library were performed as part of the 2026-11-13 release validation. |

The release run initially exposed one Mypy type error; a type guard was added,
then the final main and tag workflows passed. The failed attempt was not released.

## Historical live Pi deployment check — 2026-11-10

This table records an earlier Pi state observed on that date. A later Pi-local
deployment was recorded on 2026-11-13 in the current status table above. This
historical table predates publication of the `v0.1.18` GHCR image.

การตรวจนี้ยืนยันสถานะ runtime บน Pi เท่านั้น ไม่ใช่ผลทดสอบ parity/E2E

| Check | Result | Evidence/notes |
| --- | --- | --- |
| SSH host | PASS | `begodev` (Raspberry Pi OS, `aarch64`) |
| Adapter container | PASS | `grimmlink-adapter` running; Docker health `healthy`; restart count `0` |
| Image | PASS | `ghcr.io/0xstillb/grimmlink-adapter:0.1.18`, `linux/arm64`, image ID `sha256:8c90acf53031e8e0278ea9d49e93107de5d24c6fae8b04317e9c7e8d123edebb` (built locally on Pi; not published to GHCR) |
| Adapter HTTP health | PASS | `GET http://<Pi-LAN-address>:8085/healthcheck` returned HTTP `200` |
| NAS import mount | PASS | Container target `/imports` is read-only |
| Adapter data mount | OBSERVED | Container target `/app/data` is writable; persistence/backup restore was not checked |
| Source revision label | OBSERVED | No OCI labels; image built locally on Pi from the working-tree build archive and not published to GHCR |
| Adapter SQLite volume | PASS | `/app/data` uses external Docker volume `grimmlink_adapter_data` |
| Current Grimmory health | PASS | Read-only `GET http://<Pi-LAN-address>:6060/api/v1/healthcheck` from the Pi returned HTTP `200` |
| Metadata write canary (user-run) | PASS | Test-library book `535`: Adapter log at 2026-11-10 13:30:31 +07 shows Official metadata `PUT` and readback `GET` both HTTP `200`; persisted event says `seriesName=Blade Play`, `seriesNumber=5`, both verified |
| Metadata source smoke test | PASS | New ARM64 image read the actual Vol. 2/3 JSON sidecars as `ready`; absent optional cover files became `cover=False` rather than `FileNotFoundError` |
| Queued Library check recovery | PASS | Existing run completed 12/12: 9 `in_sync`, 2 `updated`, 1 `no_source`. Vol. 2/3 series values were written and verified; Grimmory API readback returned `Blade Play` 2/3 |
| Container after rollout | PASS | `0.1.18` running, Docker health `healthy`, restart count `0`; `/healthcheck` and `/readyz` both returned HTTP `200` |
| Metadata UI automatic session | PASS | Served Metadata page contains automatic UI connection; auto-session returned HTTP `200`; session accessed a local Metadata route with HTTP `200` and was denied on a Reader API route with HTTP `403`; test session was revoked |
| Fallback UI wording | PASS | Served Metadata page visibly labels `ใช้ OPF ก่อน; fallback เป็น JSON sidecar` and `ใช้ JSON sidecar ก่อน; fallback เป็น OPF` |
| Series comparison | EXPECTED_DIFFERENCE | Same library also has `Break Blade` Vol. 2/3 (books `27`/`30`); their OPFs name a different series and author. Vol. 5's source OPF is `Blade Play` by Aomaru Seta, so its series differs intentionally. |
| Grimmory API/database diagnostics | NOT_RUN | Adapter UI diagnostics were not run |
| Reader and metadata feature E2E | NOT_RUN | Automatic Metadata UI session and scope smoke test passed; full browser flow and Reader client sync were not run |

### Read-only Adapter-to-Grimmory functional smoke — 2026-11-10

These checks ran against the deployed Pi Adapter using the background account
already bound to Library 4. They used GET requests only; no Grimmory state was
changed.

| Check | Result | Evidence/notes |
| --- | --- | --- |
| Adapter auth bridge | PASS | `GET /api/grimmlink/v1/auth` returned HTTP `200`; returned user identity matched the configured Library 4 owner. This exercised the Bearer flow, not KOReader MD5-key login. |
| Capabilities and read-status catalog | PASS | v1 capabilities returned HTTP `200` with progress, PDF, reading-session, metadata, and shelf features advertised; read-status catalog returned HTTP `200` with 8 values. |
| Regular shelf reads | PASS | Shelf listing returned HTTP `200` with 2 shelves; item listing returned HTTP `200` for both (0 and 2 items, queried with a limit of 2). |
| Reading-session read | PASS | `GET /api/grimmlink/v1/reading-sessions?bookId=535&limit=1` returned HTTP `200` with 0 existing sessions. |
| Test-book detail | PASS | Official Grimmory book detail lookup for test-library book `535` returned HTTP `200` and the expected book ID. |
| v2 authorization boundary | PASS | Unauthenticated `GET /api/grimmlink/v2/shelves` returned HTTP `401`, as required for an Adapter-issued Reader session. |
| By-hash and progress read | NOT_RUN | Grimmory's book detail response omitted current/initial file hashes, and Adapter identity storage had no mapping for book `535`; no hash was guessed. Use a disposable fixture with a verified file hash before testing these routes. |
| Reader writes / client sync | NOT_RUN | No progress, status, shelf membership, rating, bookmark, annotation, or reading-session writes were sent. Readest/KOReader sync still needs a disposable fixture and device/plugin run. |

### KOReader `Proxy_unavailable` diagnosis and automatic account linking — 2026-11-10

- Pi logs showed requests from the test reader reaching `POST /api/grimmlink/v2/auth/session`; Grimmory's `/api/koreader/users/auth` returned HTTP `200`, but stock Grimmory's response omitted numeric `userId`. The Adapter had no linked account for the Library 4 Metadata owner, so it incorrectly surfaced the unresolved identity as HTTP `502`; the plugin labels 502 as `proxy_unavailable`. The URL and network route were valid.
- Version `0.1.18` is deployed with automatic first-use linking: after Grimmory accepts the KOReader key, the Adapter matches the exact username against the bound Metadata account, verifies its Official JWT profile, and stores the MD5-key digest plus the shared refresh-token chain. It never links a different username. When no exact match exists and Grimmory omits the ID, the Adapter now returns an actionable `501`, not a false proxy outage.
- Isolated ARM64 container smoke tests with temporary SQLite and mocked Grimmory passed both the same-user auto-link/same-user Bearer flow and the mismatched-username refusal. A real KOReader request then reached the Pi: at 2026-11-10 10:18:41–10:18:42 +07, Grimmory KOReader auth and profile calls returned `200`, the Adapter logged successful same-user auto-link (user ID `1`), and `POST /api/grimmlink/v2/auth/session` returned `200`. This confirms the live credential and username match. The Pi is running/healthy with 0 restarts; `/healthcheck`, `/readyz`, and the Metadata page returned `200`.
- No further request from the KOReader device to progress, book, shelf, or session routes appears after that successful login in the last 45 minutes of logs. The auth request itself may have exceeded the plugin's short client timeout while the Adapter continued and completed it; a later client-side step is also possible. This log window has no Adapter or Grimmory timeout/error to correlate. Reader sync behavior remains unverified.
- The displayed message `Check that Grimmory is running and this device is on the same network` is the plugin's local timeout suggestion. Source review found its connection-test auth timeout was only 1.5 seconds for local URLs and 0.8 seconds for remote URLs, shorter than the first-use linking path can take. The plugin now allows 10 seconds; the full KOReader Busted suite passed 227/227. ADB installed the updated controller on device `VUY9K18B27912892`; its SHA-256 matches the host file. The old controller is backed up at `/sdcard/koreader/grimmlink-backups/grimmlink_connection_controller.lua.before-timeout-fix-20261110`. KOReader was left running to avoid interrupting a possible reading session, so the new code takes effect after an app restart; on-device retest is pending restart.

### User-reported metadata run: two errors — 2026-11-10

Pi SQLite run snapshot for Library 4 showed 12/12 checked with 9 `in_sync`,
2 `failed`, and 1 `no_source`. The failed books were Blade Play Vol. 2 (536)
and Vol. 3 (537), both recorded as `FileNotFoundError`. Their PDFs exist at
the exact mapped `/imports/Books/MANGA_NC` paths, but each adjacent JSON
sidecar references a `.cover.jpg` that is absent. The third book without
metadata is book 28, “ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง เล่ม 1”. Library 4 is
already configured with `sourcePreference=opf`; this evidence points to the
missing optional cover reference as the two errors, not to an OPF series
mismatch. Version 0.1.16 addressed the missing optional cover, and the updated
image was deployed and the queued check completed successfully.

Readiness: **Pi runtime is ready for staging test preflight.** Start the E2E
matrix only after confirming a disposable Grimmory account/library, excluding
production data, and recording the plugin/server builds and test fixtures. The
live healthcheck and read-only parser smoke test do not satisfy feature-level
metadata write E2E coverage. The current image was built from local working-tree
files and has not been published to GHCR. Automatic Metadata UI session scoping
was smoke-tested; full browser setup/reconnect and Reader client E2E remain open.

### Restart recovery and Grimmory startup ordering review — 2026-11-10

This is a source/configuration review of the deployed revision, not a restart or
Grimmory-outage simulation.

| Area | Finding |
| --- | --- |
| Container restart | `deploy/pi-compose.yml` uses `restart: unless-stopped`; Docker will restart the container after a process/host restart unless an operator stopped it. |
| Local state | SQLite and encrypted account state are on external volume `grimmlink_adapter_data`; migration startup is rerun. A restore from backup was not tested. |
| Metadata automation | Startup resets interrupted `running` metadata runs to `queued` and starts the worker. The deployed image retries transient upstream transport/timeouts and 502/503/504 errors on the same durable run with bounded exponential backoff. |
| Reading sessions | Pending writes are durable and reconciled for the matching authenticated user on that user's next session request. Startup does not globally replay sessions; ambiguous POSTs are intentionally not blindly replayed. |
| Other outbox mutations | Outbox rows are durable, but there is no generic startup drain loop. Failed metadata actions are retried when the corresponding action is submitted again with its idempotency key. |
| Grimmory starts later | Adapter startup does not wait for Grimmory; `/healthcheck` is local-only and `/readyz` checks upstream availability. Metadata runs retain their durable position and retry transient upstream errors. |

At the live check, Grimmory was currently reachable (healthcheck HTTP 200). No
delayed-boot failure was induced, so recovery across an actual outage/restart
remains an E2E checklist item.

### Deployed resilience and OPF-first source handling — 2026-11-10

- Added `/readyz` as a read-only upstream readiness probe; Docker liveness remains local at `/healthcheck`.
- Metadata Auto/Manual runs now keep their durable position and retry transient transport errors, timeouts, and HTTP 502/503/504 with exponential backoff from 30 seconds up to 15 minutes. Metadata write intent is reconciled against a fresh read before retry.
- Pi log and persisted event for the user-run Vol. 5 canary confirm the series write and readback succeeded; the different series value is explained by the two comparison records being `Break Blade`, not `Blade Play`.
- Source preference defaults to one OPF; absent or malformed OPF falls back to one JSON sidecar. Missing optional cover references are ignored while path escapes remain rejected.
- The UI used to show only “ใช้ OPF”, which obscured the fallback behavior. Version `0.1.16` now shows the OPF-first/JSON-fallback behavior directly in the selector; the served page was checked after rollout.
- The run that showed two errors identified Blade Play Vol. 2/3, both `FileNotFoundError` because their JSON sidecars referenced missing `.cover.jpg` files. After rollout, the existing queued check retried and completed 12/12: 9 `in_sync`, 2 `updated`, 1 `no_source`. Vol. 2/3 were written from their JSON sidecars and verified; Grimmory API readback returned `Blade Play` series numbers 2 and 3. Book 28 still has no metadata source.
- New image smoke testing used the actual NAS files read-only and confirmed both books parse as `ready`, source JSON, cover absent. The user's already queued check then performed the two authorized metadata updates. Ruff and mypy pass. Local pytest collection remains blocked by missing `.venv` dependency `cryptography`; restart/outage E2E cases remain unchecked.

### Metadata UI access and Pi rollout — 2026-11-10

- Version `0.1.17` is running on the Pi (`linux/arm64`, image ID `sha256:34ab37827d921a2a3b6284493f0ccab45a6904b40091043b6b76a385e83dbcdb`); it was built locally and has not been published to GHCR. Compose backup: `/home/begodev/grimmlink-adapter-v2/compose.yml.codex-backup-0.1.16-20261110`.
- Metadata UI now opens a short-lived local UI session using the already-bound background metadata account. Initial setup, missing account, or reconnect still uses the existing enrollment form. UI session credentials are scoped to `/api/grimmlink/v2/local/**`; they cannot authenticate Reader APIs. Reader client authentication remains unchanged for home Wi-Fi and Tailscale access.
- Live smoke test: Metadata page served successfully, auto-session `200`, local Metadata route `200`, Reader API route `403`; the test session was revoked. Final Pi checks: container `running`/`healthy`, restart count `0`, `/healthcheck` `200`, `/readyz` `200`.
- Local pytest collection remains unavailable because `.venv` is missing `cryptography`; Ruff, mypy on changed auth/API modules, inline JavaScript syntax, and deployment smoke checks passed. First-time enrollment/reconnect and full Readest/KOReader sync remain feature E2E items.

## Historical parity run — 2026-09-29

The remaining run metadata, detailed results, data reconciliation, blockers,
and final decision below belong to this 2026-09-29 staging-gate snapshot. They
are retained as historical evidence and do not describe the 2026-11-13 release
or current Pi deployment. Use the current status table at the top for release
and validation state.

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
| Official XPointer → EPUB CFI conversion may leave Web Reader stale | OPEN — NOT RE-TESTED | Native KOReader sync may pass while Web Reader remains at an older CFI | Kept as an open check in `docs/E2E_CHECKLIST.md`; no Web Reader E2E in the 2026-11-13 release run | Re-run the Web Reader projection checks in `docs/E2E_CHECKLIST.md` before changing this status |

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

