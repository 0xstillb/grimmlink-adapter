# GrimmLink → Official Grimmory Migration Prompt Pack

## Execution model — recommended

ใช้ **Gemini Flash 3.8 เป็น implementer หลัก** และใช้ **GPT-5.6 Sol High เป็น review gate ระหว่าง session** ไม่ใช่รอตรวจทีเดียวตอนท้าย

- Gemini: inspect → implement → test → update HANDOFF → STOP before merge
- Sol: review architecture/semantics/data safety → APPROVE or FIX_REQUIRED
- Session 02/03/05/06/07/08/09/10/11 เป็น deep-review gates
- ดู `WORKFLOW_GEMINI_SOL.md` และ `SOL_REVIEW_TEMPLATE.md`


เป้าหมาย: ย้าย GrimmLink ออกจาก Grimmory fork โดย **ไม่แก้ Official Grimmory**, ไม่เขียน DB โดยตรง และรักษา behavior สำคัญของ fork ให้ครบ

The Adapter uses a read-only MariaDB lookup to resolve a reader's file hash to
the Grimmory book/file ID on first sync. Stock Grimmory's native KOReader
progress response does not include those IDs, so this lookup is required for
unmapped books unless another verified mapping already exists. It is disabled
by default and requires a dedicated SELECT-only DB account on a restricted
network. It never writes to Grimmory's database. See `sessions/03_BOOK_IDENTITY.md`
and `docs/HANDOFF.md`.

For a legacy MD5-only client, link each Grimmory user locally once after the
Adapter is configured. Run this with the same `GRIMMORY_BASE_URL` and
`SQLITE_DB_PATH` used by the Adapter:

```text
uv run --no-sync python -m grimmlink_adapter.link_account <username>
```

The command prompts for the password, checks that KOReader and Official JWT
authentication identify the same user, and stores a digest of the MD5 key
plus Official tokens in Adapter SQLite. Protect that file from other users.

## Target architecture

```text
KOReader / existing GrimmLink plugin
              ↓
       GrimmLink Adapter
      (standalone new repo)
              ↓
       Official Grimmory
```

OPF ingestion (Session 03A):

```text
OPF → GrimmLink Adapter → Official Metadata API (PRIMARY)
                        ↳ .metadata.json/.cover.jpg sidecar (FALLBACK)
                        → Official Grimmory Import All
```

## New repo
แนะนำ: `0xstillb/grimmlink-adapter`

Default stack: Python 3.12 + FastAPI + httpx + SQLite + pydantic + pytest + Docker.
ถ้าจะเปลี่ยน stack ต้องมี ADR อธิบาย blocker ก่อน

## Model split
- **GPT Plus / GPT-5.6 Sol High**: architecture, auth/security, hash identity, progress, metadata mapping, idempotency, migration/cutover review
- **Antigravity / Gemini Flash 3.8**: scaffold, repetitive DTO/API implementation, fixtures, pagination plumbing, CI/Docker/docs หลัง scope ถูกล็อก
- งานเสี่ยง data corruption: Flash implement ได้ แต่ต้อง GPT review ก่อน merge

## Rules
1. Official Grimmory ต้อง stock
2. ห้าม direct DB write
3. ห้ามลบ fork ก่อน replacement ผ่าน canary
4. ห้าม force-push/history rewrite
5. small cherry-pick-friendly commits
6. preserve user work
7. ห้าม log password/token/MD5 key
8. Magic Shelf removal = read-only/rule-derived
9. multi-shelf ต้องไม่ลบ local file ผิด
10. EPUB native location/CFI/XPointer เป็นตำแหน่งหลัก
11. EPUB display % ต้องสัมพันธ์กับ page ratio เมื่อมี page data ที่เชื่อถือได้
12. PDF page/progress/conflict semantics ต้องคงเดิม
13. retry ต้อง idempotent
14. OPF ingestion: Primary = Official Metadata API, Fallback = Sidecar JSON (Session 03A); source ebook files are never modified

## Session order
00 → 12 ตามไฟล์ใน `sessions/`
ทุก session ต้องอ่านและอัปเดต `docs/HANDOFF.md`


## Additional session

หลัง `03_BOOK_IDENTITY.md` ให้รัน:

`03A_OPF_API_WITH_SIDECAR_FALLBACK.md`

Primary = Official Metadata API  
Fallback = Official-compatible Sidecar JSON

## V2 local metadata UI and LAN access

The Adapter serves its Setup, Diagnostics, and OPF/sidecar review UI at `/api/grimmlink/v2/local/metadata` (the root URL redirects there). The login form can load without a session; metadata, diagnostics, and setup API actions require signing in with the normal Grimmory website account. Readest and KOReader enter their KOSync credentials in the reader apps; the Adapter UI does not ask for those credentials. Configure `METADATA_IMPORT_ROOTS` as a JSON array of directories visible to the Adapter, for example `METADATA_IMPORT_ROOTS=["/imports"]`. The scanner only lists files under those roots; preview is read-only and apply is explicit.

Use **Setup** to generate the `GRIMMORY_DB_*` environment snippet for a dedicated SELECT-only MariaDB account. The browser copies the snippet locally; it does not submit or store the database password. Paste it into the Adapter's private `.env`, restart the Adapter, then use **Diagnostics → Run diagnostics** to verify the Grimmory API, Adapter SQLite, hash lookup query permissions, metadata roots, and mDNS. The downloaded JSON report contains no passwords or tokens. To produce the same system report from a terminal/container, run `uv run python -m grimmlink_adapter.diagnostics` from the Adapter project directory.

The service advertises `grimmlink.local` through mDNS when `MDNS_ENABLED=true`. In Docker bridge mode, set `LAN_BIND_IP` to the Pi's reserved/static LAN IPv4; the example binds ports 8085 and 6060 to that interface and advertises the same address through mDNS. Ensure multicast DNS packets can pass between the container and LAN (an mDNS reflector or host networking may be needed). Host networking has different Docker DNS behavior, so configure `GRIMMORY_BASE_URL` to an address reachable from that network mode. Docker Desktop may not pass multicast; use the host's LAN IP and port `8085` as the fallback. **Do not forward either published port from the Internet, and keep host firewall rules limited to LAN clients.** Direct connections to the Adapter port bypass reverse-proxy path ACLs. Use the reverse proxy as the public entry point for Reader routes and to enforce LAN-only access for `/api/grimmlink/v2/local/**`.

The example Compose file bind-mounts the Pi4's existing NAS directory read-only at `/imports` in the Adapter and at `/books` in Grimmory. Set `NAS_BOOKS_ROOT` in Compose's `.env` to the actual host mount, for example `/mnt/nas/books`. The example is a reference: integrate the Adapter into the existing Grimmory/DB network rather than replacing a running server with it. mDNS advertises the configured Adapter port; it does not configure firewall rules or expose the port by itself.

## Auto / Manual metadata on the Pi4

1. Mount the NAS books into the Adapter read-only and set `METADATA_IMPORT_ROOTS` to the paths inside its container. Use the same files that Grimmory scans. Enable the SELECT-only identity lookup described above.
2. Create a small marker file such as `.grimmlink-storage` on the NAS inside each mapped root. This is a one-time operator action; the Adapter never creates files on the NAS in the automatic workflow.
3. Sign in to Setup with the Grimmory website account, then open **Metadata → Load Library → Connect background account**. This stores the Grimmory refresh credential encrypted for background work; the browser session can close afterward.
4. Select a Library and map each Grimmory library path to an Adapter import root and the NAS marker file. Choose OPF/JSON preference and allowed fields, then save. Each Library has its own settings and owner.
5. **Check now** works with Auto either on or off. Select one/multiple configured Libraries, choose check, preview, backfill-empty, backfill-source, or retry, then start. Backfill-source explicitly replaces nonempty selected fields; locked fields remain protected. Existing books from before enrollment require backfill; new books are processed automatically when Auto is enabled.

The durable worker waits for stable files, verifies exact book/file identity, plans per-field changes, protects newer Grimmory edits, persists write intent, and reads values back after writing. It yields between Libraries every twenty books. Missing NAS storage pauses the run with delayed retry. Sources waiting for Grimmory to import their book remain queued; the UI does not silently trigger a Grimmory rescan. Stop a run separately from disabling its Auto schedule. Manual requests during a running check request one follow-up pass without changing the Auto schedule.

New Library policies default to a 300-second Auto interval. The worker stages catalog pages in SQLite and loads twenty books at a time for processing. It fetches full book details only when it needs to verify a metadata source. The Activity page polls job summaries and loads a book's field differences when opened.

Auto metadata uses the supported metadata API, with no output-sidecar writes. Existing manual file ingestion retains its sidecar fallback. Auto ignores sidecars marked as Adapter-generated to avoid loops. Metadata and cover outcomes are separate: covers remain pending when no safe upload path is available. Multiple conflicting sources require resolving the preference/configuration; per-field Grimmory/source conflicts can be resolved from activity details.

Background credentials and newly stored linked-account access/refresh tokens are encrypted. Startup upgrades legacy plaintext linked tokens. Persist and back up the encryption key **with the SQLite database**, and keep both private. By default the key is next to SQLite with suffix `.metadata.key`; `METADATA_KEY_FILE` overrides it. Losing the key requires relinking accounts. Use one worker/replica. Activity exposes current per-book details and recent history; Diagnostics exports queue counts and safe reason codes without metadata values or credentials.

Implementation targets stock Grimmory 3.5.0 Library/BrowsePage/Book metadata contracts. Actual Pi4/NAS/Grimmory integration remains to be verified on the deployed build; source-file persistence/move settings must be disabled and readable by the bound account before any API metadata write.
