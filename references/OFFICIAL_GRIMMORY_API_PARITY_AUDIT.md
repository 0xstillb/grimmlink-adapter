# Official Grimmory API Parity Audit สำหรับ GrimmLink

**วันที่ audit:** 2026-09-25 (Asia/Bangkok)  
**ขอบเขต:** read-only source/API audit — ไม่เรียก API ที่เปลี่ยนข้อมูล และไม่แก้ source, config, DB หรือ container  
**เป้าหมาย:** ตรวจว่า GrimmLink ย้ายจาก Grimmory fork ไปใช้ Official Grimmory API ได้หรือไม่ โดยให้ Shelf Sync เป็น priority สูงสุด

## 1. Executive conclusion

**ผลสรุป: ย้ายได้ แต่ไม่ใช่ drop-in replacement และยังย้ายไม่ได้ด้วยการเปลี่ยน base URL อย่างเดียว**

Official Grimmory มี capability ฝั่ง server เกือบครบสำหรับ book access, regular shelves, progress, bookmarks, rating, reading sessions และ sidecar แต่ GrimmLink รุ่นที่ audit ยังผูกกับ **fork-specific `/api/grimmlink/v1` contract** หลายจุด ทั้ง path, authentication, response shape และ batch payload

สถานะ migration ที่เหมาะสม:

- **SUPPORTED:** Official มี endpoint/semantics ตรงกับงาน และ GrimmLink ใช้ได้หลังเปลี่ยน route/auth เล็กน้อย
- **PARTIAL:** Official มี capability แต่ path, DTO, pagination, units หรือ permission ต่างกัน
- **MISSING:** Official หรือ GrimmLink ฝั่งใดฝั่งหนึ่งไม่มี capability ที่ flow ปัจจุบันต้องใช้
- **NEEDS_BRIDGE_STATE:** ต้องมี adapter และ local SQLite state เพื่อรักษา token, hash mapping, dedupe, pagination หรือ offline queue

**ข้อสรุปหลัก:**

1. **Shelf Sync เป็น blocker ใหญ่สุด:** list/read regular shelf ทำได้, แต่ magic shelf แยก namespace และเป็น paginated app API; add/remove ใช้ bulk assignment endpoint คนละรูปแบบกับ fork
2. **Authentication เป็น blocker cross-cutting:** GrimmLink client ปัจจุบันใช้ `x-auth-user` + MD5 `x-auth-key` กับ fork API; Official JWT API ต้องใช้ Bearer token ส่วน MD5 ใช้กับ `/api/koreader/**` เป็นหลัก
3. **Book hash lookup ไม่มี Official endpoint ทั่วไปที่ตรงกับ fork:** Official ใช้ current hash ภายใน KOReader flow แต่ไม่มี `GET /api/v1/books/by-hash/{hash}` ใน source ที่ deploy อยู่
4. **Reading-session batch และ metadata batch แบบ GrimmLink ไม่มีใน Official contract ที่ตรวจพบ**
5. **ไม่พบความจำเป็นต้องแก้ Official Grimmory หากยอมทำ GrimmLink adapter/state** แต่หากต้องการให้ GrimmLink เดิมทำงานโดยไม่แก้ client เลย จะต้องมี compatibility bridge ฝั่ง server ซึ่งไม่ใช่แนวทางที่ audit นี้เสนอ

---

## 2. Audit evidence และวิธีตรวจ

### 2.1 Local instance

จาก container ที่รันอยู่บนเครื่องนี้:

- image: `ghcr.io/grimmory-tools/grimmory:v3.5.0`
- Official image revision: `402e89b4452f8e2b17ab95f16c1621c003516cd`
- bind port: `6060:6060`
- compose file: `/home/begodev/grimmory/docker-compose.yml`

Live read-only probes ที่ทำ:

- `GET /api/openapi.json` → `200`, `Content-Type: text/html`, body เป็น Grimmory SPA ขนาด 1404 bytes ไม่ใช่ OpenAPI JSON
- `GET /api/docs` → `200`, `Content-Type: text/html`, body เป็น SPA เช่นเดียวกัน
- `GET /api/v1/healthcheck` → `200`, JSON status `UP`
- `GET /api/v1/shelves` → `401 Unauthorized`
- `GET /api/v1/app/shelves` → `401 Unauthorized`
- `GET /api/v1/books` → `401 Unauthorized`
- `GET /api/koreader/users/auth` → `401 Unauthorized`

ดังนั้น instance ทำงานอยู่จริง แต่ **live OpenAPI artifact ใช้งานไม่ได้ใน runtime นี้**; audit endpoint contract จาก Official v3.5.0 source ที่ตรงกับ image revision และใช้ live probes เพื่อตรวจ behavior/permission เท่านั้น ระบบตั้ง `API_DOCS_ENABLED` ไม่ปรากฏใน compose environment ที่ตรวจพบ

### 2.2 Source snapshots

- Official Grimmory: commit/image revision `402e89b4452f8e2b17ab95f16c1621c003516cd` [1][4]
- GrimmLink: commit `c6114075917a86d3a5d150f484adfa76a692bd75` [2][14]
- ตรวจเอกสาร release checklist ของ GrimmLink เพิ่มเพื่อยืนยัน feature surface ที่ประกาศไว้ [3]
- ไม่ใช้ DB read, ไม่ใช้ container shell, ไม่ส่ง credential และไม่เรียก `POST`, `PUT`, `PATCH` หรือ `DELETE` ไปยัง instance

---

## 3. A. Authentication

### A1. Login / refresh — **NEEDS_BRIDGE_STATE**

- **Official endpoint + method**
  - `POST /api/v1/auth/login` — รับ username/password และคืน JWT access/refresh token
  - `POST /api/v1/auth/refresh` — รับ refresh token และคืน token ชุดใหม่ [4]
- **GrimmLink เดิมใช้**
  - `APIClient.api_prefix = "/api/grimmlink/v1"`
  - `GET /api/grimmlink/v1/auth`
  - ส่ง `x-auth-user` และ `x-auth-key = md5(password)` ในทุก request ของ client ปัจจุบัน [14]
  - เอกสาร GrimmLink ระบุ Bearer login/refresh สำหรับ extended API แต่ใน client implementation ที่ audit ไม่พบ login/refresh/token transport ที่ทำให้ request หลักเปลี่ยนไปใช้ Bearer จริง [17]
- **Incompatibility**
  - Official JWT routes เป็น `/api/v1/**` และ protected ด้วย JWT
  - MD5 headers ของ Official ใช้กับ KOReader security chain (`/api/koreader/**`) ไม่ใช่ general `/api/v1/**` [8][9]
  - เปลี่ยน URL อย่างเดียวจึงทำให้ shelf/book/download/metadata API ได้ `401`
- **GrimmLink SQLite state?** **ต้องมี** หากใช้ Official JWT: access token, refresh token, expiry/refresh result และ server/user binding ควรอยู่ใน local state; ห้ามพึ่ง memory อย่างเดียว เพราะ KOReader offline/restart
- **ต้องแก้ Official Grimmory หรือไม่?** **ไม่ต้อง** หากทำ adapter ใน GrimmLink; ถ้าต้องการคง fork wire contract โดยไม่แก้ GrimmLink จะต้องมี server compatibility layer ซึ่งอยู่นอก scope และไม่ได้เสนอให้ทำ

### A2. Auth ที่ GrimmLink ใช้กับ Official ได้ — **PARTIAL**

- Official KOReader flow ใช้ `x-auth-user` + MD5 password ผ่าน `/api/koreader/users/auth`, `/api/koreader/syncs/progress/{bookHash}` และ `/api/koreader/syncs/progress` [8][9]
- GrimmLink ใช้ header pair เดียวกัน แต่ติด prefix fork และส่งไปยัง endpoint อื่น [14]
- **ผล:** progress สามารถใช้ Official KOReader auth ได้ค่อนข้างตรง; shelf/book/download/metadata ยังต้อง Bearer adapter
- **SQLite:** ต้องเก็บ credential reference/metadata และ offline queue; ไม่ควรเก็บ access token เฉพาะ memory
- **Official change:** ไม่ต้อง

---

## 4. B. Book matching

### B1. List / search / get book — **PARTIAL**

- **Official endpoint + method**
  - `GET /api/v1/books` — list แบบ legacy
  - `GET /api/v1/books/page` — page/cursor/query/sort/facet
  - `GET /api/v1/books/{bookId}` — full book DTO
  - `GET /api/v1/app/books` — paginated app list
  - `GET /api/v1/app/books/search?q={q}&page={p}&size={n}` — app search
  - `GET /api/v1/app/books/{bookId}` — app detail [7][13]
- **GrimmLink เดิมใช้**
  - primary: `GET /api/grimmlink/v1/books/by-hash/{hash}`
  - docs ยังระบุ fallback `GET /api/v1/books/search?title=...` และ `?isbn=...` [14][17][19]
  - runtime matching หลักใช้ hash แล้ว cache `book.id` ลง SQLite
- **Incompatibility**
  - Official source ไม่พบ `GET /api/v1/books/by-hash/{hash}` และไม่พบ `/api/v1/books/search` แบบ title/isbn ที่ GrimmLink docs อ้าง
  - Official app search ใช้ `q`, `page`, `size` และคืน `AppPageResponse`; ต้อง map response/iterate page
  - Official core list/page และ app detail มี DTO คนละ shape
- **SQLite:** `book_cache` ยังจำเป็นสำหรับ file path/hash → numeric bookId และ offline/open-session continuity
- **Official change:** ไม่ต้อง; adapter เลือก core/app endpoint ตาม use case

### B2. bookId — **PARTIAL**

- Official ใช้ `Long id`; GrimmLink ใช้ numeric `book.id` อยู่แล้ว จึง compatible ในชนิดข้อมูล
- จุดขาดคือการหา id จาก hash ซึ่งเป็น entry point ของ GrimmLink ไม่ใช่ชนิดข้อมูล id เอง
- **SQLite:** ต้องมี `book_cache` และ invalidation เมื่อ file/hash เปลี่ยน
- **Official change:** ไม่ต้อง

### B3. hash / path / filename / metadata mapping — **PARTIAL**

- Official `Book` มี `primaryFile`, `alternativeFormats`, metadata และ `BookFile` fields เช่น `id`, `fileName`, `filePath`, `bookType`, `fileSizeKb`; DTO นี้ไม่ได้เปิด current file hash เป็น public matching field [7]
- Official KOReader service resolve หนังสือจาก current hash ภายใน `/api/koreader/syncs/progress/{hash}` แต่ response เป็น progress ไม่ใช่ general book identity [8]
- GrimmLink เก็บ hash/path/filename/author/title ใน `book_cache` และ `shelf_sync_map`; shelf flow reuse file จาก `book_id`/local path เป็นหลัก [14][16]
- **Incompatibility:** cannot replace fork `by-hash` with a single Official general endpoint; filename/path matching เป็น heuristic และไม่ควรแทน hash exact match
- **SQLite:** จำเป็นมาก — local hash cache, path cache, shelf mapping และ unmatched rows
- **Official change:** ไม่ต้อง หาก adapter ใช้ local cache + Official get/list/search; ห้ามเขียน DB Official โดยตรง

---

## 5. C. Shelf Sync — priority สูงสุด

### C1. List regular shelves — **PARTIAL**

- **Official:** `GET /api/v1/shelves` → `List<Shelf>`; ไม่มี `type` query ใน controller และไม่ประกาศ pagination [5]
- **GrimmLink เดิม:** `GET /api/grimmlink/v1/shelves?type=regular`, แล้ว normalize list/`content`/`items` [14]
- **Incompatibility:** prefix และ query contract ต่างกัน; Official regular shelf DTO ใช้ `id`, `name`, `bookCount`, `publicShelf` แต่ไม่มี `type`
- **SQLite:** cache shelf list ได้ แต่ไม่ใช่ source of truth
- **Official change:** ไม่ต้อง

### C2. List magic shelves — **PARTIAL**

- **Official:** `GET /api/v1/app/shelves/magic` → `List<AppMagicShelfSummary>` [6]
- **GrimmLink เดิม:** ใช้ fork `/api/grimmlink/v1/shelves?type=magic` และคาด `type`, `bookCount`/aliases [14]
- **Incompatibility:** Official magic shelf อยู่ app namespace แยกจาก regular และ summary ไม่มี `bookCount`; ต้อง map `type = magic` ใน adapter
- **SQLite:** cache selection ได้; ไม่ควรถือว่า id เดียวกันข้าม regular/magic โดยไม่เก็บ type
- **Official change:** ไม่ต้อง

### C3. List books in regular shelf — **PARTIAL**

- **Official:** `GET /api/v1/shelves/{shelfId}/books` → `List<Book>` [5]
- **GrimmLink เดิม:** `GET /api/grimmlink/v1/shelves/regular/{shelfId}/books` พร้อม fallback legacy path `/shelves/{id}/books` [14]
- **Incompatibility:** path และ response field ต่างกัน; Official คืน full `Book`, GrimmLink normalizer ต้องดึง `bookId`, filename, format, size จาก `Book.primaryFile` แทน flat shelf item
- **Pagination:** Official controller ระบุ “all books” และไม่รับ page/size
- **SQLite:** `shelf_sync_map` จำเป็นสำหรับ local file ownership/removal
- **Official change:** ไม่ต้อง

### C4. List books in magic shelf — **NEEDS_BRIDGE_STATE**

- **Official:** `GET /api/v1/app/shelves/magic/{magicShelfId}/books?page={page}&size={size}` → `AppPageResponse<AppBookSummary>` [6]
- **GrimmLink เดิม:** `GET /api/grimmlink/v1/shelves/magic/{id}/books` ครั้งเดียว และ normalizes array/`content`/`items` [14][15]
- **Incompatibility:** Official paginated (`page`, `size`, `totalPages`, `hasNext`); current sync plan assumes a complete in-memory list andไม่มี page loop
- **SQLite:** ต้องเก็บ/ส่งต่อ page cursor หรือ sync checkpoint หาก shelf ใหญ่และมี offline/cancel/resume; `shelf_sync_map` ยังต้องเก็บทุก mapping
- **Official change:** ไม่ต้อง; adapter ต้อง paginate จน `hasNext=false`

### C5. Add book to shelf — **MISSING** (ใน GrimmLink flow ปัจจุบัน)

- **Official:** `POST /api/v1/books/shelves` body `{bookIds, shelvesToAssign, shelvesToUnassign}` [7]
- **GrimmLink เดิม:** ไม่มี `addBookToShelf` ใน API client และ Shelf Sync เป็น server → local download; ไม่ได้ observe local KOReader shelf แล้ว assign กลับ server [14][15]
- **Incompatibility:** ไม่มี client call สำหรับ add; download หนังสือจาก shelf ไม่เท่ากับ assign หนังสือใหม่เข้า shelf
- **SQLite:** ถ้าจะเพิ่ม ต้องมี intent/outbox เพื่อ retry และ dedupe assignment
- **Official change:** ไม่ต้อง; feature เป็น GrimmLink adapter/state work

### C6. Remove book from shelf — **PARTIAL**

- **Official:** ใช้ `POST /api/v1/books/shelves` เดิม โดยใส่ `shelvesToUnassign`; ไม่มี granular `/{shelf}/books/{book}/remove` ใน Official Shelf/Book controllers [5][7]
- **GrimmLink เดิม:** `POST /api/grimmlink/v1/shelves/{type}/{shelfId}/books/{bookId}/remove` และ fallback regular legacy path [14]
- **Incompatibility:** path/method/payload ต่างกัน; Official operation เป็น bulk assignment API และต้องใช้ Bearer
- **SQLite:** `pending_shelf_removals` จำเป็นสำหรับ retry, `shelf_sync_map` ใช้เช็ก ownership
- **Official change:** ไม่ต้อง

### C7. Create / delete / rename shelf — **MISSING** (GrimmLink ไม่ได้ใช้)

- **Official:**
  - `POST /api/v1/shelves` create
  - `PUT /api/v1/shelves/{shelfId}` rename/update
  - `DELETE /api/v1/shelves/{shelfId}` delete [5]
- **GrimmLink เดิม:** UI เลือก/validate shelf ที่มีอยู่; ไม่พบ API client สำหรับ create/update/delete
- **Incompatibility:** ถ้าความหมาย “ถ้า GrimmLink ใช้” คือ feature ปัจจุบัน — ไม่มี flow ให้ย้าย
- **SQLite:** ไม่จำเป็นสำหรับ basic CRUD แต่ควร cache selected shelf id/type/name
- **Official change:** ไม่ต้อง

### C8. Pagination — **PARTIAL**

- regular shelf books Official เป็น unpaginated list
- magic shelf books Official เป็น page/size app response
- GrimmLink client มี normalizer สำหรับ `content/items` แต่ไม่มี loop เรียกทุกหน้า [14][15]
- **SQLite:** page/checkpoint จำเป็นเฉพาะ resumable large magic sync; local map จำเป็นทุกกรณี
- **Official change:** ไม่ต้อง

### C9. หนังสืออยู่หลาย shelf — **NEEDS_BRIDGE_STATE**

- Official `Book` มี `Set<Shelf> shelves`; assignment API รับ set ของ shelf IDs [7]
- GrimmLink ใช้ composite uniqueness `(book_id, shelf_id, shelf_type)` ใน `shelf_sync_map` และ logic `isBookTrackedInOtherShelf` เพื่อเก็บ local file เมื่อยังถูก track โดย shelf อื่น [15][16][18]
- Flow ปัจจุบันสามารถ reuse local file ข้าม shelf และลบเฉพาะ mapping ที่ stale; นี่เป็น behavior ที่ต้องคงไว้เมื่อ map ไป Official
- **Incompatibility:** Official regular/magic list คนละ API และ current GrimmLink type เป็น client-side field; remove one shelf ต้องไม่ลบ file ถ้ายังมี mapping อื่น
- **SQLite:** ต้องมี — mapping, tombstone, pending removal, selected shelves และ local path ownership
- **Official change:** ไม่ต้อง

---

## 6. Shelf Sync flow trace แบบละเอียด

### 6.1 Official Grimmory → GrimmLink → KOReader

1. **เลือก shelf**
   - GrimmLink อ่าน selected shelf id/type จาก plugin settings
   - Official regular ใช้ `GET /api/v1/shelves`; magic ใช้ `GET /api/v1/app/shelves/magic`
   - adapter ต้องเติม/normalize `shelf_type` เอง เพราะ Official regular/magic DTO แยก endpoint
2. **ดึง membership**
   - regular: `GET /api/v1/shelves/{id}/books`
   - magic: วน `GET /api/v1/app/shelves/magic/{id}/books?page=N&size=...`
   - map Official `Book.primaryFile` เป็น GrimmLink shelf item: `bookId`, `fileName`, `fileSizeKb`, `bookType`, `title`, `author`, series
3. **วางแผน local sync**
   - GrimmLink สร้าง snapshot token จาก book id/name/size/format
   - query `shelf_sync_map` หา mapping เดิม; ถ้าไฟล์มีอยู่แล้ว reuse
   - ถ้า book เดิมอยู่ shelf อื่น ใช้ local path เดิมและเพิ่ม mapping ใหม่
4. **ดาวน์โหลด**
   - Official: `GET /api/v1/books/{bookId}/download` หรือ `/download-all` [7]
   - GrimmLink เดิม: `GET /api/grimmlink/v1/books/{bookId}/download` พร้อม MD5 headers [14]
   - adapter ต้องเปลี่ยน auth เป็น Bearer สำหรับ Official general API; local side ยังตรวจ content type, signature, size และเขียน `.tmp` ก่อน rename
5. **บันทึก local mapping**
   - upsert `shelf_sync_map(book_id, shelf_id, shelf_type, local_path, remote metadata, timestamps)`
   - update `book_cache` ให้ local path → book id
6. **ทำให้ KOReader เห็นหนังสือ**
   - GrimmLink ไม่ได้เรียก server-side KOReader shelf API; มันบันทึกไฟล์ลง download directory แล้ว refresh BookInfo/FileManager/SimpleUI cache
7. **cleanup**
   - หาก remote shelf เอา book ออกและ mapping ไม่อยู่ shelf อื่น: ลบเฉพาะไฟล์ที่ `downloaded_by_grimmlink=1` และอาจลบ `.sdr`
   - หากยัง track ใน shelf อื่น: ลบ mapping เฉพาะ shelf เดิมและเก็บไฟล์ไว้

**จุดตัดสินใจ:** ขั้น 1–2 ต้องมี Official adapter; ขั้น 4 ต้องมี Bearer transport; ขั้น 3/5/6/7 ใช้ local SQLite state ต่อไป

### 6.2 KOReader → GrimmLink → Official Grimmory

1. **Local file/book event**
   - GrimmLink รู้จักไฟล์จาก `book_cache`, `shelf_sync_map` และ current file path/hash
   - plugin ไม่ได้มี generic listener สำหรับ “ผู้ใช้เพิ่มหนังสือเข้า KOReader shelf” แล้วส่ง assign กลับ server
2. **Local deletion / server removal**
   - เมื่อเปิด two-way shelf delete sync และพบ mapping stale, GrimmLink queue `pending_shelf_removals`
   - fork flow เดิมเรียก granular remove endpoint
   - Official adapter ต้องรวม/ส่ง `POST /api/v1/books/shelves` ด้วย `bookIds=[id]`, `shelvesToUnassign=[shelfId]`
3. **Retry และ local cleanup**
   - ถ้า remote unassign สำเร็จ จึงลบ/mark local mapping และ file ตาม policy
   - ถ้า network/auth fail ให้คง pending row และ retry; ห้ามถือ local delete ว่า server sync สำเร็จแล้ว
4. **Add direction**
   - ปัจจุบันไม่มี flow ที่รับ local KOReader shelf add แล้วเรียก `shelvesToAssign`
   - จึงยังเป็น **MISSING** แม้ Official จะมี endpoint รองรับ
5. **Multiple shelves**
   - ทุก unassign ต้องระบุ shelf เดียวและตรวจ mapping อื่นก่อนลบ local file
   - ห้ามใช้เพียง `book_id` เป็น key เพราะจะทำให้ remove shelf หนึ่งกระทบ shelf อื่น

---

## 7. D. Reading data

### D1. Progress get/update — **PARTIAL**

- **Official:**
  - `GET /api/koreader/syncs/progress/{bookHash}`
  - `PUT /api/koreader/syncs/progress`
  - auth: `x-auth-user` + MD5 `x-auth-key` [8][9]
  - app alternative: `GET /api/v1/app/books/{bookId}/progress`, `PUT /api/v1/app/books/{bookId}/progress` [13]
- **GrimmLink เดิม:** `GET/PUT /api/grimmlink/v1/syncs/progress...` และส่ง native payload ที่มี `bookHash`, `bookId`, `bookFileId`, `progress`, `location`, `percentage`, page fields [14]
- **Incompatibility:** prefix ต่างกัน; Official KOReader `percentage` เป็น progress fraction ตาม service behavior ขณะที่ app/core DTO ใช้ percent/format อื่น; ต้องรักษา unit conversion
- **SQLite:** ต้องมี `progress_state`, `pending_progress`, conflict snapshots และ retry queue; นี่เป็น bridge state ที่มีอยู่แล้ว
- **Official change:** ไม่ต้อง หากใช้ Official KOReader route และ map payload

### D2. Bookmarks CRUD — **NEEDS_BRIDGE_STATE**

- **Official:**
  - `GET /api/v1/bookmarks/book/{bookId}`
  - `GET /api/v1/bookmarks/{bookmarkId}`
  - `POST /api/v1/bookmarks`
  - `PUT /api/v1/bookmarks/{bookmarkId}`
  - `DELETE /api/v1/bookmarks/{bookmarkId}` [10]
- **GrimmLink เดิม:** ไม่ได้ใช้ individual bookmark CRUD; ดึง/ส่ง bookmark เป็น item ใน fork `GET /api/grimmlink/v1/syncs/metadata` และ `POST /api/grimmlink/v1/syncs/metadata/batch` [14]
- **Incompatibility:** Official bookmark model ใช้ `bookId`, `cfi`, `pageNumber`, `positionMs`, `trackIndex`, title/color/notes/priority; GrimmLink payload ใช้ `dedupeKey`, `location`, `pos0/pos1`, page และ device metadata
- **SQLite:** ต้องมี remote bookmark id ↔ local annotation/dedupe mapping, applied history และ pending outbox เพื่อทำ idempotent CRUD
- **Official change:** ไม่ต้องสำหรับ basic CRUD; adapter ต้องแปลง model และแยก bookmark จาก annotation

### D3. Rating get/update/reset — **PARTIAL**

- **Official:**
  - get: `GET /api/v1/books/{bookId}` หรือ `GET /api/v1/app/books/{bookId}` — response มี `personalRating`
  - update: `PUT /api/v1/books/personal-rating` body `{ids, rating}`
  - reset: `POST /api/v1/books/reset-personal-rating` body `[bookId,...]` [7][13]
- **GrimmLink เดิม:** rating อยู่ใน metadata batch; docs ระบุ scale 1–10 และ current metadata logic normalizes rating/scale [14][17]
- **Incompatibility:** Official app/core personal rating เป็น scale 1–5; Official update/reset ไม่ใช่ metadata batch และใช้ Bearer
- **SQLite:** ต้องมี dedupe key/synced metadata history และอาจเก็บ source scale เพื่อป้องกัน 5↔10 conversion ซ้ำ
- **Official change:** ไม่ต้อง; adapter ต้อง map 1–10 KOReader ↔ 1–5 Official และรองรับ reset

### D4. Reading sessions — **PARTIAL**

- **Official:**
  - `POST /api/v1/reading-sessions` รับ single session
  - `GET /api/v1/reading-sessions/book/{bookId}?page={p}&size={n}` รับ paginated history [11]
- **GrimmLink เดิม:** `POST /api/grimmlink/v1/reading-sessions` และ primary `POST /api/grimmlink/v1/reading-sessions/batch`, fallback single เมื่อ batch fail [14][17]
- **Incompatibility:** Official controller ที่ตรวจพบไม่มี `/batch`; official request มี bookId/bookType/times/duration/progress/location แต่ไม่มี fork fields `bookHash`, `device`, `deviceId`, `currentPage`, `totalPages` ใน request contract
- **SQLite:** `pending_sessions` จำเป็นสำหรับ offline queue, dedupe และ single-session fallback; ถ้าต้องรักษา device/hash metadata ต้องเก็บ local correlation แยก
- **Official change:** ไม่ต้องสำหรับ single POST; batch behavior ต้องทำใน GrimmLink adapter โดยแตกเป็น single requests หรือยอมลด batch optimization

---

## 8. E. Book access

### E1. Download — **PARTIAL**

- **Official:** `GET /api/v1/books/{bookId}/download`; `GET /api/v1/books/{bookId}/download-all` [7]
- **GrimmLink เดิม:** `GET /api/grimmlink/v1/books/{bookId}/download`, sync หรือ async curl/wget, ใช้ MD5 headers และตรวจ binary signature/size ก่อน rename [14]
- **Incompatibility:** route และ auth chain ต่างกัน; Official download route อยู่ JWT/query-parameter protected chain ไม่ใช่ KOReader MD5 chain
- **SQLite:** shelf map/book cache จำเป็น; download temp/control files เป็น filesystem state
- **Official change:** ไม่ต้อง; Bearer-aware downloader adapter

### E2. Metadata get — **PARTIAL**

- **Official:**
  - `GET /api/v1/books/{bookId}` — metadata + progress/shelves
  - `GET /api/v1/books/{bookId}/file-metadata`
  - `GET /api/v1/books/{bookId}/cbx/metadata/comicinfo`
  - app detail/search endpoints [7][13]
- **GrimmLink เดิม:** shelf item normalization ใช้ flat remote metadata; หลังดาวน์โหลด refresh metadata โดยอ่านไฟล์ใน KOReader (`BookInfoManager`/`FileManagerBookInfo`) และเขียน local metadata index [14][15]
- **Incompatibility:** Official DTO nested `primaryFile`/metadata; ไม่ใช่ fork flat item และไม่มี general hash lookup field
- **SQLite:** book cache + metadata index/sync history จำเป็น local; Official metadata itself ไม่ควรถูก mirror แบบไม่มี identity/version
- **Official change:** ไม่ต้องสำหรับ read; adapter mapping required

### E3. Sidecar import-all — **MISSING** (ใน GrimmLink flow)

- **Official:** `POST /api/v1/libraries/{libraryId}/sidecar/import-all`; ยังมี per-book `POST /api/v1/books/{bookId}/sidecar/import` และ read/status/export routes [12]
- **GrimmLink เดิม:** ไม่พบ client call สำหรับ Official sidecar import-all; metadata sync ของ GrimmLink เป็น custom annotation/rating/bookmark batch ไม่ใช่ library sidecar import [14][17]
- **Incompatibility:** Official operation เป็น library-level mutation ต้องใช้ `libraryId` และ metadata-edit/admin permission; ไม่มี equivalent ใน current GrimmLink UI/API client
- **SQLite:** ไม่ควรเก็บเป็น substitute ของ server sidecar; อาจเก็บ job/result marker หากภายหลังมี explicit feature
- **Official change:** ไม่ต้อง; feature ต้องเพิ่ม adapter/UI flow ก่อน และ endpoint นี้ไม่ได้ถูกเรียกระหว่าง audit เพราะเป็น write operation

---

## 9. Capability matrix แบบย่อ

| Capability | Status | Official route ที่ใช้ได้ | GrimmLink เดิม | SQLite state | แก้ Official ไหม |
|---|---|---|---|---|---|
| Login/refresh | NEEDS_BRIDGE_STATE | `POST /api/v1/auth/login`, `/refresh` | fork MD5 auth | token/expiry | ไม่ต้อง ถ้ามี adapter |
| List/search/get book | PARTIAL | `/api/v1/books*`, `/api/v1/app/books*` | fork by-hash + documented old search | book cache | ไม่ต้อง |
| Hash/path mapping | PARTIAL | ไม่มี general by-hash; KOReader hash route เฉพาะ progress | `by-hash` | จำเป็น | ไม่ต้อง |
| Regular shelf list | PARTIAL | `GET /api/v1/shelves` | fork typed list | cache | ไม่ต้อง |
| Magic shelf list | PARTIAL | `GET /api/v1/app/shelves/magic` | fork `?type=magic` | cache + type | ไม่ต้อง |
| Regular shelf books | PARTIAL | `GET /api/v1/shelves/{id}/books` | fork typed books | shelf map | ไม่ต้อง |
| Magic shelf books | NEEDS_BRIDGE_STATE | paginated app endpoint | one-shot fork endpoint | page/checkpoint + map | ไม่ต้อง |
| Add book to shelf | MISSING | bulk assign POST | ไม่มี flow | outbox ถ้าเพิ่ม | ไม่ต้อง |
| Remove book from shelf | PARTIAL | bulk assign POST/unassign | granular fork POST | pending removals | ไม่ต้อง |
| Create/rename/delete shelf | MISSING | regular shelf CRUD | ไม่มี flow | selected shelf cache | ไม่ต้อง |
| Multi-shelf behavior | NEEDS_BRIDGE_STATE | Book.shelves + bulk IDs | composite local map | จำเป็น | ไม่ต้อง |
| Progress get/update | PARTIAL | `/api/koreader/syncs/progress*` | fork progress path | pending/conflict | ไม่ต้อง |
| Bookmark CRUD | NEEDS_BRIDGE_STATE | `/api/v1/bookmarks*` | metadata batch | remote id/dedupe | ไม่ต้อง |
| Rating get/update/reset | PARTIAL | Book GET + personal-rating PUT/reset POST | metadata batch 1–10 | dedupe/scale | ไม่ต้อง |
| Reading sessions | PARTIAL | single POST + paginated GET | single + fork batch | pending sessions | ไม่ต้อง |
| Download | PARTIAL | `/api/v1/books/{id}/download*` | fork download | shelf map | ไม่ต้อง |
| Metadata get | PARTIAL | book/file-metadata/app detail | flat fork + local refresh | cache/index | ไม่ต้อง |
| Sidecar import-all | MISSING | `POST /api/v1/libraries/{id}/sidecar/import-all` | ไม่มี call | ไม่ใช่ substitute | ไม่ต้อง |

---

## 10. Blockers ที่ทำให้ย้ายจาก fork ไม่ได้ทันที

1. **Auth mismatch:** current GrimmLink MD5 client ไม่สามารถเรียก Official general `/api/v1` endpoints ที่ต้อง Bearer ได้
2. **Fork-only API namespace:** shelf, `by-hash`, progress, reading session, metadata batch และ download calls อยู่ใต้ `/api/grimmlink/v1`
3. **Shelf contract split:** Official regular shelves อยู่ `/api/v1/shelves`; magic shelves/หนังสืออยู่ app namespace และ magic books ต้อง paginate
4. **No general Official by-hash API:** hash เป็น key สำคัญของ GrimmLink; Official exposes hash resolution inside KOReader progress service ไม่ใช่ book lookup API
5. **Shelf mutation mismatch:** Officialใช้ bulk `POST /api/v1/books/shelves`; fork flow ใช้ granular remove และ GrimmLink ยังไม่มี add flow
6. **Batch mismatch:** Official reading sessions ที่พบเป็น single POST; Official ไม่มี fork metadata batch contract ใน controller ที่ audit
7. **Payload/scale mismatch:** Official bookmark model และ rating 1–5 ไม่ตรงกับ GrimmLink metadata item/scale 1–10
8. **Live OpenAPI unavailable:** `/api/openapi.json` และ `/api/docs` ตอบ SPA HTML ใน instance นี้ ทำให้ client generation/contract verification จาก runtime ทำไม่ได้ ต้อง pin source/image revision

---

## 11. Features ที่ย้ายได้ทันที / ใกล้ทันที

คำว่า “ทันที” ในที่นี้หมายถึง **ไม่ต้องแก้ Official Grimmory และไม่ต้องเขียน DB Official โดยตรง** แต่ยังอาจต้องแก้ GrimmLink adapter:

- Official JWT login/refresh เป็นฐาน auth ที่ชัดเจน
- Get book by numeric id และ full/app metadata หลัง map DTO
- Regular shelf list และ regular shelf books หลังเปลี่ยน route/normalizer
- Download หลังเปลี่ยน downloader ให้ส่ง Bearer และคง binary validation ฝั่ง KOReader
- Progress get/update ผ่าน Official `/api/koreader/**` โดยคง MD5 auth และเปลี่ยน prefix
- Local reuse, file integrity checks, `shelf_sync_map`, multi-shelf local ownership และ offline queues — เป็น logic ฝั่ง GrimmLink ที่ไม่ผูกกับ Official DB

---

## 12. Features ที่ต้องมี GrimmLink adapter/state

- Bearer access/refresh token lifecycle และ request routing แยก KOReader MD5 กับ Official JWT
- Hash → bookId resolution โดยใช้ local cache เป็นหลัก และ fallback เป็น Official list/page/search แบบ exact/defensive matching
- Regular/magic shelf endpoint adapter, shelf type normalization และ magic pagination/checkpoint
- Official `Book`/`BookFile` → GrimmLink shelf item mapping
- Bulk shelf assign/unassign adapter พร้อม retry/outbox
- `shelf_sync_map` composite key และ “keep local file while another shelf still tracks it” invariant
- Bookmark CRUD mapping, remote ID, dedupe key และ applied-history
- Rating conversion 1–10 ↔ 1–5 และ reset/get mapping
- Reading-session batch fan-out เป็น single POST พร้อม pending-session retry
- Metadata/sidecar feature separation: local KOReader metadata refresh ไม่ใช่ Official sidecar import-all

---

## 13. ข้อกำหนดด้าน data/state และสิ่งที่ audit นี้ไม่เสนอ

- **ห้าม direct DB write:** migration นี้ต้องใช้ Official HTTP API เท่านั้น ไม่แตะ MariaDB ของ Grimmory และไม่พยายามเลียนแบบ schema ภายใน Official
- GrimmLink SQLite เป็น client-owned state ที่ยังจำเป็นสำหรับ cache, offline queue, dedupe, mapping, retry และ resumable sync
- ไม่ควรถือ local SQLite เป็น source of truth แทน Official; ใช้เป็น outbox/cache พร้อม reconciliation
- ห้ามถือว่า HTTP request สำเร็จ = local file/server state สำเร็จโดยไม่ตรวจ response และ update mapping ตามผลจริง
- audit นี้ **ไม่ implement fix**, ไม่เปลี่ยน config, ไม่เปิด API docs, ไม่สร้าง user/token และไม่เรียก endpoint ที่เปลี่ยนข้อมูล

---

## 14. Final verdict

**Official Grimmory API parity สำหรับ GrimmLink: PARTIAL / MIGRATABLE WITH ADAPTER + STATE**

- ถ้า requirement คือ “เปลี่ยนจาก fork เป็น Official โดยไม่แก้ GrimmLink”: **ไม่ผ่าน**
- ถ้า requirement คือ “ย้ายโดยเพิ่ม Official API adapter ใน GrimmLink และคง SQLite client state”: **ผ่านในเชิงสถาปัตยกรรม**
- Shelf Sync สามารถคง behavior สำคัญได้ โดยเฉพาะ local mapping, multiple-shelf ownership และ safe cleanup แต่ต้องทำ regular/magic route split, magic pagination, Bearer auth และ bulk assignment mapping ให้ครบก่อน
- ไม่พบ blocker ที่บังคับให้แก้ Official Grimmory สำหรับ feature ที่ระบุ; blocker อยู่ที่ fork contract และ GrimmLink adapter/state

## Sources

[1] https://github.com/grimmory-tools/grimmory.git  
[2] https://github.com/0xstillb/GrimmLink  
[3] https://github.com/0xstillb/GrimmLink/blob/Main/RELEASE_CHECKLIST.md  
[4] https://github.com/grimmory-tools/grimmory/blob/402e89b4452f8e2b17ab95f16c1621c003516cd/backend/src/main/java/org/booklore/controller/AuthenticationController.java  
[5] https://github.com/grimmory-tools/grimmory/blob/402e89b4452f8e2b17ab95f16c1621c003516cd/backend/src/main/java/org/booklore/controller/ShelfController.java  
[6] https://github.com/grimmory-tools/grimmory/blob/402e89b4452f8e2b17ab95f16c1621c003516cd/backend/src/main/java/org/booklore/app/controller/AppShelfController.java  
[7] https://github.com/grimmory-tools/grimmory/blob/402e89b4452f8e2b17ab95f16c1621c003516cd/backend/src/main/java/org/booklore/controller/BookController.java  
[8] https://github.com/grimmory-tools/grimmory/blob/402e89b4452f8e2b17ab95f16c1621c003516cd/backend/src/main/java/org/booklore/controller/KoreaderController.java  
[9] https://github.com/grimmory-tools/grimmory/blob/402e89b4452f8e2b17ab95f16c1621c003516cd/backend/src/main/java/org/booklore/config/security/filter/KoreaderAuthFilter.java  
[10] https://github.com/grimmory-tools/grimmory/blob/402e89b4452f8e2b17ab95f16c1621c003516cd/backend/src/main/java/org/booklore/controller/BookMarkController.java  
[11] https://github.com/grimmory-tools/grimmory/blob/402e89b4452f8e2b17ab95f16c1621c003516cd/backend/src/main/java/org/booklore/controller/ReadingSessionController.java  
[12] https://github.com/grimmory-tools/grimmory/blob/402e89b4452f8e2b17ab95f16c1621c003516cd/backend/src/main/java/org/booklore/controller/SidecarController.java  
[13] https://github.com/grimmory-tools/grimmory/blob/402e89b4452f8e2b17ab95f16c1621c003516cd/backend/src/main/java/org/booklore/app/controller/AppBookController.java  
[14] https://github.com/0xstillb/GrimmLink/blob/c6114075917a86d3a5d150f484adfa76a692bd75/grimmlink.koplugin/grimmlink_api_client.lua  
[15] https://github.com/0xstillb/GrimmLink/blob/c6114075917a86d3a5d150f484adfa76a692bd75/grimmlink.koplugin/grimmlink_shelf_sync.lua  
[16] https://github.com/0xstillb/GrimmLink/blob/c6114075917a86d3a5d150f484adfa76a692bd75/grimmlink.koplugin/grimmlink_database.lua  
[17] https://github.com/0xstillb/GrimmLink/blob/c6114075917a86d3a5d150f484adfa76a692bd75/docs/content/reference/api-endpoints.md  
[18] https://github.com/0xstillb/GrimmLink/blob/c6114075917a86d3a5d150f484adfa76a692bd75/docs/content/features/shelf-sync.md  
[19] https://github.com/0xstillb/GrimmLink/blob/c6114075917a86d3a5d150f484adfa76a692bd75/docs/content/features/book-id-resolution.md
