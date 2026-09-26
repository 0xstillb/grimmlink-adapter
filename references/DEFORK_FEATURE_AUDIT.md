# Defork Feature Audit — Grimmory fork → Official Grimmory

**Scope:** READ-ONLY audit — ไม่แก้ code, config, DB, container หรือ repository ต้นทาง  
**อ่านก่อนเริ่ม:** `/home/begodev/OFFICIAL_GRIMMORY_API_PARITY_AUDIT.md`  
**Repositories:** `0xstillb/grimmory`, `grimmory-tools/grimmory`, `0xstillb/GrimmLink`, `0xstillb/grimmory-bridge`

## 1. Verdict

การเลิกใช้ fork ทำได้ แต่ต้องทำเป็น **defork migration** ไม่ใช่ลบ package `org.booklore.grimmlink` แล้วเปลี่ยน URL อย่างเดียว

Fork เพิ่ม behavior หลักไว้ 4 ชั้น:

1. **GrimmLink server contract** — `/api/grimmlink/v1`, MD5 auth, flat DTO, hash fallback, shelf facade, metadata batch, reading-session batch
2. **Cross-device behavior** — progress normalization, PDF conflict handling, web UI progress bridge, rating/bookmark/annotation bridge, metadata cursor/dedupe
3. **OPF ingestion** — fork-side adjacent OPF scan/metadata/cover augmentation
4. **Fork release/data surface** — V9001/V9002, fork-only tests/docs/CI/release workflows

Official Grimmory ยังเป็น source of truth สำหรับ library, shelf, progress, bookmark/annotation, rating, sessions และ sidecar import. Grimmory Bridge เป็นปลายทางของ OPF → embedded/sidecar workflow ตาม requirement ที่กำหนด [4].

```text
OPF → Grimmory Bridge → .metadata.json → Official Grimmory → Import All
```

### Classification legend

- **DROP** — fork-only surface; ลบทิ้งได้หลังเงื่อนไขก่อน drop ผ่าน
- **MOVE_GRIMMLINK** — behavior ต้องอยู่ใน GrimmLink plugin/adapter/local SQLite
- **MOVE_BRIDGE** — behavior ต้องอยู่ใน Grimmory Bridge
- **KEEP_EXTERNAL** — ไม่ใช่ fork runtime surface; คงไว้เป็น Official/external dependency
- **BLOCKER** — ห้ามเลิกใช้ fork จนกว่าจะ preserve/replace/verify รายการนี้

---

## 2. Audit snapshots

| Repo | Snapshot ที่ตรวจ |
|---|---|
| `0xstillb/grimmory` | `704f8c26794da923ea8ed96cfb1e6405e43f1241` — `fix(ci): block published flyway migration mutations` |
| `grimmory-tools/grimmory` | deployed comparison baseline `v3.5.0`, revision `402e89b4452f8e2b17ab95f16c1621c003516cd` |
| `0xstillb/GrimmLink` | `c6114075917a86d3a5d150f484adfa76a692bd75` |
| `0xstillb/grimmory-bridge` | `2073a9fb681eee55daf117a84b7d7229f1add243` |

จาก parity audit เดิม Official instance ทำงานจริง แต่ `/api/openapi.json` และ `/api/docs` ตอบ SPA HTML แทน OpenAPI JSON; จึงใช้ source ที่ pin ตาม revision และไม่ใช้ credential/API write ระหว่าง audit นี้

---

## 3. Feature inventory และปลายทาง

### 3.1 GrimmLink backend/API

| Fork feature/behavior | Evidence | Classification | ต้องย้าย/ทำก่อน drop อย่างไร |
|---|---|---|---|
| Fork route namespace `/api/grimmlink/v1` | `GrimmlinkRoutes.API_PREFIX` | **DROP** | GrimmLink ต้องเปลี่ยนไป Official endpoints; ห้ามรักษา compatibility route ใน Official เป็นเงื่อนไขถาวร [5][15] |
| `x-auth-user` + MD5 `x-auth-key` filter, sync-enabled/link checks | `GrimmlinkAuthFilter`, `GrimmLinkSecurityConfig` | **MOVE_GRIMMLINK / BLOCKER** | แยก Official JWT transport สำหรับ `/api/v1/**` กับ Official KOReader MD5 transport สำหรับ `/api/koreader/**`; ห้าม drop จน auth canary ผ่าน [1][2][3] |
| `GET /auth` wrapper ที่คืน `status`, username, userId, sync flags | `GrimmlinkV1AuthController`, `GrimmlinkAuthService` | **DROP** | ใช้ Official auth/KOReader authorize โดยตรง; response wrapper ถ้าจำเป็นให้สร้างใน GrimmLink client ไม่ใช่ Official fork server |
| `GET /books/by-hash/{hash}` | `GrimmlinkV1BookController` | **MOVE_GRIMMLINK / BLOCKER** | ย้าย hash resolution ไป local `book_cache`/plugin adapter; Official ไม่มี general by-hash endpoint ที่เป็น drop-in [6][8] |
| `GET /books/read-statuses`, `PUT /books/{id}/status` | fork `GrimmlinkBookService` | **MOVE_GRIMMLINK** | map ไป Official read-status API หรือใช้ Official book/progress model; คง status normalization และ permission handling ใน adapter |
| Fork download wrapper | `GET /books/{bookId}/download` | **MOVE_GRIMMLINK** | Official download route ใช้ Bearer; คง temp file, signature/size verification และ safe rename ใน GrimmLink |
| Fork DTO `GrimmlinkBookSummary` | flat `bookId`, `bookFileId`, filename, format, size, hash, series | **MOVE_GRIMMLINK** | map Official nested `Book`/`BookFile` เป็น internal adapter DTO; ไม่ย้าย DTO เข้า Official |
| Fork capabilities endpoint | `GET /capabilities` flags webUiProgress/progressSync/pdfBridge/sessions/metadata/shelves | **DROP** | capability detection ย้ายเป็น static adapter capability matrix หรือ runtime probe ใน GrimmLink |
| Fork controller/service/facade package ทั้งชุด | `org.booklore.grimmlink.*` | **DROP** | drop หลังทุก row ใน inventory ผ่านและ migration data ถูก preserve แล้ว |
| Fork API docs/release docs | `GRIMMLINK-V1-API.md`, release/readiness docs | **MOVE_GRIMMLINK / DROP** | route behavior ที่ยังจำเป็นให้เขียนเป็น GrimmLink adapter contract; fork server API reference drop หลัง cutover [15] |

### 3.2 Shelf Sync / Magic Shelf

| Fork feature/behavior | Evidence | Classification | ต้องย้าย/ทำก่อน drop อย่างไร |
|---|---|---|---|
| List regular shelves with unified `type` field | fork `GET /api/grimmlink/v1/shelves?type=regular` | **MOVE_GRIMMLINK** | Official `GET /api/v1/shelves`; adapter เติม `shelf_type=regular` |
| List magic shelves with unified shelf DTO | fork merges user/public magic shelves and adds `type`, `bookCount`, description | **MOVE_GRIMMLINK** | Official `GET /api/v1/app/shelves/magic`; adapter map DTO และเก็บ type แยกจาก id |
| Flat shelf book summary | fork emits filename, format, size, hash, series | **MOVE_GRIMMLINK** | map `Book.primaryFile`/metadata จาก Official; ใช้ internal normalized item ใน plugin |
| Optional limit/offset/cursor shelf pagination | fork applies local pagination after loading all books | **MOVE_GRIMMLINK** | regular Official list ไม่ต้อง page; magic Official ต้อง page/size loop; persist checkpoint ใน local SQLite |
| Regular shelf remove endpoint | fork `POST /shelves/{id}/books/{book}/remove` | **MOVE_GRIMMLINK** | Official bulk assignment POST with `shelvesToUnassign`; keep pending-removal outbox |
| Magic shelf remove returns unsupported/rule-based | fork explicitly refuses manual removal from magic shelf | **BLOCKER** | ต้องตัดสิน behavior อย่างเป็นทางการก่อน cutover: magic shelf เป็น read-only rule result; ห้าม map local deletion เป็น remote unassign อัตโนมัติ |
| Shelf add direction | fork has no add endpoint/flow | **BLOCKER** | ถ้า requirement ต้องมี KOReader → server add ต้องสร้าง GrimmLink adapter ต่อ Official bulk assignment; ถ้าไม่ใช้ ให้ประกาศ DROP อย่างชัดเจนก่อน cutover |
| Multiple-shelf ownership | plugin `shelf_sync_map` uses `(book_id,shelf_id,shelf_type)` and preserves local file when tracked elsewhere | **MOVE_GRIMMLINK / BLOCKER** | คง composite mapping, tombstone, local-path ownership และ invariant “remove shelf เดียวห้ามลบ file ที่ shelf อื่นยังใช้” |
| Magic shelf file download | fork resolves rule-derived book IDs then downloads like regular | **MOVE_GRIMMLINK** | ใช้ Official magic shelf list endpoint + pagination; preserve local dedupe and download policy |
| Remote removal → local cleanup | fork checks `downloaded_by_grimmlink`, deletes managed file/`.sdr`, keeps shared file | **MOVE_GRIMMLINK / BLOCKER** | ทำ canary บน regular shelf และ verify magic shelf policy ก่อนลบ fork endpoint |
| Shelf error/debug ID behavior | fork controller adds debug IDs around shelf failures | **MOVE_GRIMMLINK** | preserve safe user-facing error and local diagnostic ID; no need for Official server code |

Fork shelf service explicitly treats Magic Shelf as rule-derived and refuses manual removal; this is a semantic constraint, not merely a route mismatch [6].

### 3.3 Progress / normalization / PDF behavior

| Fork feature/behavior | Evidence | Classification | ต้องย้าย/ทำก่อน drop อย่างไร |
|---|---|---|---|
| Extended progress DTO: `bookHash`, `bookId`, `bookFileId`, format, pages, `updatedAt`, conflict fields | fork `KoreaderProgress` | **MOVE_GRIMMLINK** | keep as internal plugin snapshot; translate to Official KOReader progress payload |
| currentHash → initialHash → accessible candidate fallback | `GrimmLinkBookMatchService`, `GrimmlinkHashMatcher` | **MOVE_GRIMMLINK / BLOCKER** | preserve exact priority and access filtering in local adapter; no general Official hash API replacement exists |
| Reflowable location normalization | fork rejects numeric-only native location and requires native location for EPUB-like formats | **MOVE_GRIMMLINK / BLOCKER** | preserve CFI/XPointer/native location policy and test EPUB open/push/pull |
| EPUB display percentage derived from page ratio when page data exists | fork progress service | **MOVE_GRIMMLINK** | preserve conversion and unit contract; Official fraction/percent differences must not leak to UI |
| PDF fixed-page projection | fork maps page/progress into PDF fields and file-level progress | **MOVE_GRIMMLINK** | map to Official progress fields; preserve page number and percentage projection |
| Manual read-status precedence over older progress | fork preserves newer manual status | **MOVE_GRIMMLINK / BLOCKER** | preserve timestamp comparison; verify against Official progress/status writes |
| PDF conflict detection with expected timestamp and `force` | fork PDF bridge behavior/docs | **MOVE_GRIMMLINK / BLOCKER** | keep optimistic-conflict behavior in adapter or explicitly retire it; do not silently lose conflict protection |
| Web UI progress capability | fork capability flag + progress service writes web-reader projections | **MOVE_GRIMMLINK / BLOCKER** | use Official web/app progress as destination; preserve web↔KOReader direction and conversion tests |
| Fork `GET/PUT /syncs/progress` wrapper | `GrimmlinkV1SyncController` | **DROP** | GrimmLink calls Official `/api/koreader/syncs/progress*` or documented app API directly [5][8] |

The fork progress service writes both user-level and file-level projections and normalizes reflowable/PDF behavior; these behaviors must be preserved even though the fork endpoint itself is dropped [8].

### 3.4 Bookmarks / annotations / rating

| Fork feature/behavior | Evidence | Classification | ต้องย้าย/ทำก่อน drop อย่างไร |
|---|---|---|---|
| Generic metadata batch carrying rating + annotations + bookmarks | `POST /syncs/metadata`, `/metadata/batch` [7] | **MOVE_GRIMMLINK / BLOCKER** | fan out to Official rating, annotation and bookmark APIs; keep local dedupe/outbox |
| `grimmlink_metadata_items` content hash/dedupe/device/deviceId storage | fork entity + V9001 | **BLOCKER** | export/preserve every item before dropping table; map to Official records or GrimmLink SQLite applied-history |
| Rating normalization 1–10 and 1–5 input | `normalizePersonalRating` | **MOVE_GRIMMLINK** | preserve source-scale conversion and removal semantics; Official personal rating is different scale/endpoint |
| Web rating pull as synthetic `grimmory-personal-rating` item | metadata pull service | **MOVE_GRIMMLINK** | read Official personal rating and expose it to KOReader adapter without fork synthetic server item |
| Bookmark create/update/delete bridge | metadata service writes `BookMarkEntity` and metadata item | **MOVE_GRIMMLINK / BLOCKER** | map page/CFI/title/notes to Official bookmark CRUD; preserve remote-ID/dedupe mapping |
| Explicit bookmark deletion flag | `deleted=true` handling | **MOVE_GRIMMLINK** | translate to Official DELETE and delete local applied state only after confirmed success |
| Annotation payload with `pos0/pos1`, color, drawer, style, chapter | fork DTO/service | **MOVE_GRIMMLINK / BLOCKER** | map to Official annotation CRUD; preserve downgrade/unsupported-location behavior |
| Same-device skip using `deviceId` | metadata pull item | **MOVE_GRIMMLINK** | keep as client-side dedupe rule; Official pull response may not carry the same fork fields |
| Fork metadata pull `since/cursor/limit/type` | `GrimmlinkMetadataPullResponse.nextCursor` | **MOVE_GRIMMLINK / BLOCKER** | persist cursor per server/user/book/file/type in GrimmLink SQLite; read Official records incrementally where possible |

### 3.5 Reading sessions

| Fork feature/behavior | Evidence | Classification | ต้องย้าย/ทำก่อน drop อย่างไร |
|---|---|---|---|
| Fork GET sessions with `limit` | `GrimmlinkV1ReadingSessionController` [9] | **MOVE_GRIMMLINK** | call Official paginated reading-session GET; normalize response |
| Single session write with hash/device/page fields | fork service | **MOVE_GRIMMLINK** | map supported fields to Official single-session POST; retain unsupported fields locally if needed |
| Batch session write | `/reading-sessions/batch` + per-item results | **MOVE_GRIMMLINK / BLOCKER** | fan out to Official single-session POST with retry and result aggregation; do not assume Official batch exists |
| Server-side idempotency by user/book/hash/start/end/device | V9002 index + `findDuplicate` | **BLOCKER** | preserve idempotency in client outbox/canonical key or verify Official uniqueness; otherwise duplicate sessions can be created during retry |
| Extra session columns `book_hash`, `device`, `device_id`, `current_page`, `total_pages` | V9001 | **BLOCKER** | export/backfill/archive before removing fork schema; decide exact mapping for each field |
| Fork reading-session tests | service/controller tests | **MOVE_GRIMMLINK / DROP** | behavior assertions move to GrimmLink adapter contract tests; fork Spring tests drop with server package |

### 3.6 Metadata pull / cursor / hash-book matching

| Fork feature/behavior | Classification | Destination |
|---|---|---|
| Current/initial hash matching with accessible-library check | **MOVE_GRIMMLINK / BLOCKER** | GrimmLink local adapter + `book_cache`; preserve priority and ambiguity handling |
| Flat book summary including hash and file size | **MOVE_GRIMMLINK** | internal normalized response in GrimmLink |
| Metadata push content hash | **MOVE_GRIMMLINK** | local dedupe/outbox; Official records are destination |
| Metadata pull cursor and limit | **MOVE_GRIMMLINK / BLOCKER** | SQLite cursor keyed by server/user/book/file/type |
| Server `grimmlink_metadata_items` table | **BLOCKER → DROP** | data export first; no direct DB rewrite permitted |
| Unsupported/malformed metadata result handling | **MOVE_GRIMMLINK** | adapter must preserve skip/fail/duplicate distinctions |
| Official book ID and metadata APIs | **KEEP_EXTERNAL** | use Official API; do not copy fork repository queries into Official |

---

## 4. WebUI bridge

### What the fork added

The fork backend exposes a GrimmLink capability contract and projects KOReader progress into Web UI progress. The fork also bridges web ratings/bookmarks through the metadata batch service. The current frontend search did **not** show a distinct GrimmLink-specific WebUI route or component; the bridge is primarily backend/API behavior, while the normal Official WebUI remains an external surface.

### Classification

- **Fork WebUI progress projection:** **MOVE_GRIMMLINK / BLOCKER**
- **Fork web rating/bookmark pull/push:** **MOVE_GRIMMLINK / BLOCKER**
- **Official WebUI reader/progress/session UI:** **KEEP_EXTERNAL**
- **Fork capabilities endpoint:** **DROP** after GrimmLink adapter capability detection exists
- **New standalone WebUI fork component:** no distinct component found in the inspected frontend tree; no separate component needs to be moved

### Cutover condition

Run a bidirectional canary for EPUB and PDF:

```text
KOReader → GrimmLink adapter → Official → Official WebUI
Official WebUI → Official → GrimmLink adapter → KOReader
```

The canary must verify native location, percent units, manual read status precedence, PDF page projection, rating scale, bookmark/annotation dedupe and retry behavior.

---

## 5. OPF and metadata workflow

### 5.1 Fork-side OPF behavior

Fork added adjacent OPF discovery, secure/tolerant XML parsing, metadata field augmentation, metadata-lock respect, adjacent cover discovery, cover priority and cache-only cover application during scan/import/refresh [10][11][14].

### 5.2 Required replacement

The requested replacement is accepted as the target architecture:

```text
OPF → Grimmory Bridge → .metadata.json / .cover.jpg → Official Grimmory → Import All
```

### 5.3 Classification

| OPF feature/behavior | Classification | Destination/condition |
|---|---|---|
| Adjacent OPF locator and safe path containment | **MOVE_BRIDGE** | Bridge OPF parser/scan plan; preserve ambiguity/path-escape rejection |
| OPF metadata extraction: title/authors/publisher/date/description/language/categories/ISBN/series | **MOVE_BRIDGE** | Bridge `opf.py`; output canonical sidecar payload |
| OPF metadata lock behavior | **MOVE_BRIDGE** | Bridge preview must show source/target; Official remains final authority during import |
| `allMetadataLocked`/field-lock safety | **MOVE_BRIDGE / BLOCKER** | preserve in preview/import policy; do not overwrite locked Official fields |
| Adjacent cover manifest/property/fallback priority | **MOVE_BRIDGE** | Bridge cover resolver and `.cover.jpg` output |
| Cache-only cover application that avoids changing book hash | **DROP** in fork backend | Bridge writes sidecar cover; Official Import All updates Official metadata without fork scan hook |
| Fork OPF backend augmentation during library scan | **DROP** after Bridge canary | do not keep duplicate OPF source of truth in fork |
| Bridge dry-run, field diff, compatibility report | **KEEP_EXTERNAL** | Bridge is the replacement UI/runtime |
| Bridge backup-before-write, halt, manifest and rollback | **KEEP_EXTERNAL** | preserve before enabling write mode [16][17] |
| `.metadata.json` and `.cover.jpg` format | **KEEP_EXTERNAL / BLOCKER** | output is the accepted handoff artifact; validate Official Import All on representative EPUB/PDF/cover cases [18][19] |
| CBZ/AZW3/MOBI sidecar-only behavior | **KEEP_EXTERNAL** | Bridge may generate sidecar-only output; no embedded write required |

**OPF verdict:** replacement path is architecturally complete, but operationally it remains a cutover gate until import-all verification confirms field locks, cover handling, ISBN/series and no unintended book-file hash changes.

---

## 6. Migrations V9001+

| Migration/state | Classification | Defork action |
|---|---|---|
| `V9001` `grimmlink_metadata_items` table | **BLOCKER** | preserve/export all rows keyed by user/book/file/type/dedupe/device/content hash before fork DB retirement |
| `V9001` reading-session extra columns | **BLOCKER** | preserve `book_hash`, `device`, `device_id`, `current_page`, `total_pages`; map to Official/client state or archive before schema removal |
| `V9001` indexes on metadata/session fields | **DROP** after data preservation | indexes have no destination once fork table/columns are retired [12] |
| `V9002` reading-session idempotency index | **DROP** only after idempotency is implemented/verified in GrimmLink + Official path [13] |
| V9001/V9002 naming/isolation convention | **DROP** | fork migration isolation is not needed in Official; do not copy V9001+ into Official |
| Existing Official reading-session/bookmark/progress tables | **KEEP_EXTERNAL** | use documented Official API; no direct DB writes |

### Data-loss warning

The `grimmlink_metadata_items` table is not equivalent to ordinary Official bookmarks. It stores arbitrary annotation payload JSON, content hashes, device IDs and client timestamps. It must not be dropped merely because Official has bookmark/annotation endpoints. **No direct DB write is proposed or permitted.** If no read-only export path is available, this remains a true blocker.

---

## 7. Frontend

### Findings

- No distinct `GrimmLink`/`/api/grimmlink` frontend route or component was found in the inspected fork frontend tree.
- The fork frontend contains normal Official WebUI surfaces such as reading sessions, sidecar settings/viewer and metadata UI; these are not automatically fork additions.
- The fork-specific WebUI bridge is primarily backend behavior exposed through capabilities/progress/metadata services.

### Classification

| Frontend surface | Classification |
|---|---|
| Ordinary Official WebUI reader/progress/rating/session components | **KEEP_EXTERNAL** |
| Sidecar settings/viewer that correspond to Official sidecar support | **KEEP_EXTERNAL** |
| Any UI text or API client code explicitly bound to `/api/grimmlink/v1` | **MOVE_GRIMMLINK**, if found in downstream deployment; otherwise **DROP** |
| Fork-only API capability display | **DROP** after adapter capability detection |
| General fork frontend translations/build output | **DROP** only as part of normal fork retirement; do not migrate wholesale |

No frontend blocker was found separate from the backend/WebUI bridge semantics listed above.

---

## 8. Tests

| Test family | Classification | Migration target |
|---|---|---|
| `GrimmlinkV1ControllersTest` | **DROP / MOVE_GRIMMLINK** | drop Spring controller tests; move route/response assertions to GrimmLink API adapter contract tests |
| `GrimmlinkAuthFilterTest` and security dispatch tests | **DROP / MOVE_GRIMMLINK** | drop fork filter tests; add client tests for JWT/KOReader auth selection and 401/403 handling |
| `GrimmlinkHashMatcherTest` | **MOVE_GRIMMLINK / BLOCKER** | preserve currentHash/initialHash/access/ambiguity cases in plugin adapter tests |
| `GrimmlinkServicesBehaviorTest` | **MOVE_GRIMMLINK** | split behavior assertions by progress, metadata, sessions, shelf and test against mocked Official API |
| `GrimmlinkReadingSessionServiceTest` | **MOVE_GRIMMLINK / BLOCKER** | preserve batch fan-out, duplicate key and retry semantics |
| `AdjacentOpf*Test`, `OpfMetadataExtractorTest` | **MOVE_BRIDGE** | bridge already has Python OPF/plan/run tests; require equivalent coverage before deleting fork OPF code |
| Official core tests unrelated to fork | **KEEP_EXTERNAL** | remain with Official project/runtime |
| GrimmLink Lua tests for local DB/shelf/progress/metadata | **KEEP_EXTERNAL / MOVE_GRIMMLINK** | extend to Official adapter contract; preserve local SQLite invariants |
| Bridge Python/RPC/plan/run tests | **KEEP_EXTERNAL / MOVE_BRIDGE** | retain as replacement acceptance tests |

Tests are not migration evidence by themselves: a green fork Spring test does not prove Official API parity.

---

## 9. CI, release and documentation

| Fork surface | Classification | Action |
|---|---|---|
| Fork Grimmory image/release workflow | **DROP** | stop publishing fork runtime after cutover and retention window |
| Fork `v*-grimmory` migration immutability checks | **DROP** with fork migrations | do not transplant V9001+ into Official |
| Fork GrimmLink API docs | **MOVE_GRIMMLINK / DROP** | retain only adapter behavior and migration notes in GrimmLink docs |
| Fork OPF docs | **MOVE_BRIDGE** | make Bridge docs the canonical OPF workflow |
| GrimmLink plugin CI/release | **KEEP_EXTERNAL** | continue independent plugin releases after adapter cutover |
| Grimmory Bridge release/sidecar CI | **KEEP_EXTERNAL** | continue signed desktop/sidecar release pipeline |
| Official Grimmory CI/release | **KEEP_EXTERNAL** | remain upstream source of Official runtime |

---

## 10. MUST PRESERVE

These are behaviors, not necessarily fork files:

1. **Book identity:** current hash priority, initial hash fallback, access filtering and deterministic ambiguity handling.
2. **Shelf ownership:** composite `(book_id, shelf_id, shelf_type)` mapping, multi-shelf reuse and safe local deletion.
3. **Magic Shelf semantics:** rule-derived/read-only behavior; do not pretend a local delete is a remote magic-shelf unassign.
4. **Progress normalization:** EPUB native location, fraction/percent conversion, PDF page projection, file-level projection and manual status precedence.
5. **Conflict safety:** expected timestamp/force behavior for PDF or progress conflicts.
6. **Metadata idempotency:** dedupe key, content hash, device/deviceId, cursor and applied-history semantics.
7. **Rating semantics:** 1–10/1–5 conversion, web rating pull and explicit removal/reset.
8. **Bookmark/annotation semantics:** page/CFI/location mapping, explicit delete, unsupported-location handling and same-device dedupe.
9. **Reading-session semantics:** batch result visibility, duplicate suppression and retry-safe identity.
10. **OPF safety:** secure parsing, path containment, malformed-file skip, metadata locks and cover priority.
11. **Bridge safety:** dry-run first, field-level diff, backup-before-write, halt-on-error, manifest and rollback.
12. **Sidecar handoff:** deterministic `.metadata.json`/`.cover.jpg` output and Official Import All verification.
13. **No direct DB write:** migration must use documented APIs, controlled export/read-only preservation and client-owned SQLite state only.

---

## 11. SAFE TO DROP

Only after the migration gates below pass:

- `/api/grimmlink/v1` controllers, DTOs, facade/services and fork security chain
- fork auth wrapper and capabilities endpoint
- fork shelf wrapper endpoints, including granular remove route
- fork progress wrapper endpoint after Official KOReader adapter canary
- fork metadata batch endpoints after Official bookmark/annotation/rating adapter canary
- fork reading-session batch endpoint after single-POST fan-out/idempotency canary
- V9002 index and all V9001+ migration machinery after data preservation
- fork OPF scan/augment/cover hooks after Bridge sidecar/import-all acceptance
- fork-only API docs/release docs and fork image publishing workflows
- fork Spring controller/security tests after replacement contract tests are green

“Safe to drop” does **not** mean safe to delete now; it means safe only after the corresponding blocker is closed.

---

## 12. MUST MOVE

### MOVE_GRIMMLINK

- Official auth selector: JWT for general API, KOReader MD5 for `/api/koreader/**`
- hash/path → bookId cache and current/initial hash behavior
- regular/magic shelf adapter, page loop, normalization and multiple-shelf state
- Official download adapter with binary integrity checks
- progress normalization, PDF projection/conflict behavior and web progress bridge
- bookmark/annotation/rating mapping, cursor and dedupe state
- reading-session batch fan-out, retry and idempotency key
- local SQLite outbox/cache/tombstone state
- behavior-focused tests and adapter contract tests

### MOVE_BRIDGE

- adjacent OPF discovery/parser
- OPF metadata extraction and normalization
- metadata-lock-aware preview/write policy
- cover discovery/conversion/priority
- dry-run, diff, backup, manifest, halt and rollback workflow
- `.metadata.json` and `.cover.jpg` generation

### KEEP_EXTERNAL

- Official Grimmory REST/API/DB implementation
- Official WebUI and native reader/session/rating/bookmark surfaces
- Grimmory Bridge application/release pipeline
- GrimmLink plugin release/CI and local SQLite runtime
- Calibre/KOReader file formats and sidecar conventions

---

## 13. TRUE BLOCKERS

1. **Fork metadata data preservation** — no deletion of `grimmlink_metadata_items` until all rows are exported and mapped.
2. **Fork reading-session field preservation** — V9001 extra fields and V9002 duplicate identity must have a destination.
3. **Exact hash matching** — initialHash fallback and access-filter semantics need a GrimmLink-side replacement; Official general API is not a drop-in.
4. **Authentication cutover** — JWT/KOReader split must work without storing unsafe or unbounded secrets in logs.
5. **Magic Shelf deletion semantics** — rule-derived membership cannot be treated as ordinary shelf membership.
6. **Progress behavior parity** — units, native locations, PDF page state, manual status and conflict handling must be verified bidirectionally.
7. **Metadata mapping parity** — rating scale, bookmark/annotation fields, device dedupe and cursor behavior must be verified.
8. **Reading-session idempotency** — batch-to-single fan-out must not duplicate sessions on timeout/retry.
9. **OPF handoff acceptance** — Bridge output must import correctly through Official Import All while preserving locks, covers and identifiers.
10. **No direct DB migration path** — if the fork metadata cannot be preserved through a safe read-only/controlled export, the fork cannot be retired without data-loss risk.

---

## 14. Migration order

### Phase 0 — Freeze and inventory

1. Freeze new fork-only feature development.
2. Record fork server revision, Official revision, GrimmLink revision and Bridge revision.
3. Snapshot counts/checksums of fork metadata/session state and GrimmLink SQLite; do not delete or rewrite.
4. Identify active regular/magic shelf selections and files tracked by multiple shelves.

### Phase 1 — OPF replacement first

1. Run Bridge dry-run against representative EPUB/PDF/CBZ/AZW3/MOBI cases.
2. Verify field-level diff, locks, series/ISBN, cover priority and malformed OPF handling.
3. Write `.metadata.json`/`.cover.jpg` only through Bridge write mode with backup/manifest.
4. Import via Official Import All and verify resulting metadata, cover and file hash behavior.
5. Once this passes, disable fork OPF scan hooks in the retirement plan; do not delete them yet.

### Phase 2 — GrimmLink transport and identity

1. Add route/auth adapter in GrimmLink: Official JWT vs KOReader MD5.
2. Implement local hash/bookId mapping with currentHash/initialHash priority.
3. Add DTO normalization from Official `Book`/`BookFile` to GrimmLink internal shape.
4. Run read-only book/shelf canaries; no shelf mutation yet.

### Phase 3 — Shelf Sync

1. Regular shelf list/books.
2. Magic shelf list/books with pagination/checkpoint.
3. Download and local mapping.
4. Multi-shelf reuse and cleanup.
5. Explicitly enforce magic shelf read-only removal policy.
6. Only after read path is stable, enable regular-shelf unassign through Official bulk assignment.
7. Decide whether add-to-shelf is implemented or explicitly retired.

### Phase 4 — Progress and WebUI bridge

1. EPUB/KOReader native location push/pull.
2. PDF page/progress push/pull.
3. Manual read-status precedence.
4. Conflict/force behavior.
5. WebUI → Official → GrimmLink → KOReader and reverse canary.

### Phase 5 — Metadata and sessions

1. Export/preserve fork metadata items before any fork schema retirement.
2. Map ratings and removals.
3. Map bookmarks/annotations and remote IDs.
4. Persist cursor/dedupe in GrimmLink SQLite.
5. Fan out reading-session batches to Official single-session API.
6. Verify retry/idempotency under timeout and restart.

### Phase 6 — Cutover and retirement

1. Switch GrimmLink to Official endpoints and disable fork endpoint calls.
2. Observe one full sync cycle across regular shelf, magic shelf, EPUB, PDF, rating, bookmark, annotation and session cases.
3. Verify no requests target `/api/grimmlink/v1`.
4. Keep fork read-only/rollback window until data verification is complete.
5. Export/archive required fork data through controlled read-only process.
6. Only then retire fork image, fork controllers/services, V9001/V9002 migration path, fork docs and fork CI publishing.

---

## 15. Final defork decision

**Fork can be abandoned only after:**

- all TRUE BLOCKERS are closed;
- OPF replacement has passed Official Import All acceptance;
- GrimmLink adapter/state owns the required sync semantics;
- fork metadata/session data has a verified preservation destination;
- no runtime request depends on `/api/grimmlink/v1`;
- no required behavior is being supplied only by V9001/V9002;
- no direct DB write is used as a migration shortcut.

Until those conditions pass, the correct status is **BLOCKER — keep fork runtime available but read-only where possible**. No code was changed by this audit.

## Sources

[1] https://github.com/0xstillb/grimmory
[2] https://github.com/grimmory-tools/grimmory
[3] https://github.com/0xstillb/GrimmLink
[4] https://github.com/0xstillb/grimmory-bridge
[5] https://github.com/0xstillb/grimmory/blob/704f8c26794da923ea8ed96cfb1e6405e43f1241/backend/src/main/java/org/booklore/grimmlink/controller/GrimmlinkV1SyncController.java
[6] https://github.com/0xstillb/grimmory/blob/704f8c26794da923ea8ed96cfb1e6405e43f1241/backend/src/main/java/org/booklore/grimmlink/controller/GrimmlinkV1ShelfController.java
[7] https://github.com/0xstillb/grimmory/blob/704f8c26794da923ea8ed96cfb1e6405e43f1241/backend/src/main/java/org/booklore/grimmlink/service/GrimmlinkMetadataService.java
[8] https://github.com/0xstillb/grimmory/blob/704f8c26794da923ea8ed96cfb1e6405e43f1241/backend/src/main/java/org/booklore/grimmlink/service/GrimmlinkProgressService.java
[9] https://github.com/0xstillb/grimmory/blob/704f8c26794da923ea8ed96cfb1e6405e43f1241/backend/src/main/java/org/booklore/grimmlink/service/GrimmlinkReadingSessionService.java
[10] https://github.com/0xstillb/grimmory/blob/704f8c26794da923ea8ed96cfb1e6405e43f1241/backend/src/main/java/org/booklore/opf/AdjacentOpfMetadataAugmenter.java
[11] https://github.com/0xstillb/grimmory/blob/704f8c26794da923ea8ed96cfb1e6405e43f1241/backend/src/main/java/org/booklore/opf/AdjacentOpfCoverApplier.java
[12] https://github.com/0xstillb/grimmory/blob/704f8c26794da923ea8ed96cfb1e6405e43f1241/backend/src/main/resources/db/migration/V9001__Grimmory_fork_rename_grimmlink_metadata_and_session_fields.sql
[13] https://github.com/0xstillb/grimmory/blob/704f8c26794da923ea8ed96cfb1e6405e43f1241/backend/src/main/resources/db/migration/V9002__GrimmLink_reading_session_idempotency_index.sql
[14] https://github.com/0xstillb/grimmory/blob/704f8c26794da923ea8ed96cfb1e6405e43f1241/docs/OPF-ADJACENT-METADATA.md
[15] https://github.com/0xstillb/grimmory/blob/704f8c26794da923ea8ed96cfb1e6405e43f1241/docs/GRIMMLINK-V1-API.md
[16] https://github.com/0xstillb/grimmory-bridge/blob/2073a9fb681eee55daf117a84b7d7229f1add243/python/grimmory_bridge/plan.py
[17] https://github.com/0xstillb/grimmory-bridge/blob/2073a9fb681eee55daf117a84b7d7229f1add243/python/grimmory_bridge/run.py
[18] https://github.com/0xstillb/grimmory-bridge/blob/2073a9fb681eee55daf117a84b7d7229f1add243/python/grimmory_bridge/opf.py
[19] https://github.com/0xstillb/grimmory-bridge/blob/2073a9fb681eee55daf117a84b7d7229f1add243/python/grimmory_bridge/sidecar.py
