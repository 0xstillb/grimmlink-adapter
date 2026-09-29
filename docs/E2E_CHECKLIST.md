# GrimmLink Adapter — Full E2E Checklist

Checklist นี้ใช้พิสูจน์ GrimmLink plugin → Adapter → stock Official Grimmory
บนระบบจริงก่อนสรุป parity หรือ cutover โดยต้องเริ่มจาก disposable/test library
และ test account เท่านั้น

ผลและหลักฐานของแต่ละรอบให้บันทึกใน `docs/PARITY_REPORT.md`

## Result labels

- `PASS` — พฤติกรรมตรงตาม acceptance criteria พร้อมหลักฐาน
- `FAIL` — พฤติกรรมไม่ตรงตาม acceptance criteria
- `ACCEPTED_DIFFERENCE` — ต่างจาก fork โดยตั้งใจและไม่มี data-loss/safety risk
- `BLOCKER` — ขาด environment, credential, fixture หรือพบความเสี่ยงที่ห้ามทดสอบต่อ
- `NOT_RUN` — ยังไม่ได้รัน ห้ามนับเป็นผ่าน

HTTP `200` เพียงอย่างเดียวไม่ใช่หลักฐานว่า sync สำเร็จ ต้องตรวจ state ปลายทางและ
พฤติกรรมที่ผู้ใช้เห็นจริงด้วย

## 1. Run identity และ preflight

- [ ] บันทึกวันเวลาและผู้รันทดสอบ
- [ ] บันทึก Adapter commit SHA และ container image ID/tag
- [ ] บันทึก GrimmLink plugin commit/release
- [ ] บันทึก Official Grimmory version/image digest
- [ ] ยืนยันว่า Adapter health เป็น `healthy`
- [ ] ยืนยันว่าใช้ disposable Official library และ test account
- [ ] ยืนยันว่าไม่มี production book/shelf/user อยู่ใน test scope
- [ ] ยืนยันว่า Adapter SQLite ใช้ persistent test volume
- [ ] ยืนยันว่า linked account ใช้ test user เดียวกับ MD5 credentials
- [ ] ยืนยันว่าไม่บันทึก password, token, refresh token หรือ MD5 key ใน report/log
- [ ] เตรียม EPUB, PDF และ OPF fixtures พร้อม checksum ก่อนทดสอบ
- [ ] เปิด Adapter/Official logs พร้อม timestamp correlation

## 2. Automated quality gate

- [x] `pytest` ผ่านทั้งหมด
- [x] Ruff ผ่าน
- [x] mypy ผ่าน
- [x] `git diff --check` ผ่าน
- [x] healthcheck integration test ผ่าน
- [x] contract/OpenAPI freeze tests ผ่าน
- [x] บันทึก warnings แยกจาก failures

## 3. Authentication และ account isolation

- [ ] MD5 authentication สำเร็จด้วย test user
- [ ] MD5 key ผิดตอบ `401` และไม่ใช้ linked bearer เดิม
- [ ] Bearer authentication สำเร็จ
- [ ] access token หมดอายุแล้ว refresh ได้หนึ่งครั้ง
- [ ] invalid/expired refresh token fail closed
- [ ] MD5 และ Bearer คนละ user ถูกปฏิเสธ
- [ ] switching account ไม่ reuse token/account state ของ user ก่อนหน้า
- [ ] user ที่ไม่มีสิทธิ์เข้าถึงหนังสือได้ `403`
- [ ] upstream transport failure แยกจาก auth failure ชัดเจน

## 4. Capabilities และ legacy contract

- [x] `GET /api/grimmlink/v1/capabilities` ใช้งานได้โดยไม่ต้อง auth
- [ ] protected routes ทั้งหมดปฏิเสธ request ที่ไม่มี credential
- [x] request/response keys ตรง legacy GrimmLink contract
- [x] error shape ไม่ทำให้ plugin crash
- [x] array/object response variants ที่ Official ใช้จริงถูก normalize ถูกต้อง

## 5. Book identity และ hash safety

- [ ] exact `currentHash` resolve book/file ถูกต้อง
- [ ] `initialHash` fallback ทำงานเมื่อ current hash ไม่พบ
- [ ] unknown hash fail closed โดยไม่เดา book ID
- [ ] ambiguous hash ตอบ conflict และไม่เลือก candidate เอง
- [ ] stale book ID ถูกแทนด้วย authoritative verified hash mapping
- [ ] rename หนังสือแล้วยัง resolve ด้วย hash ได้
- [ ] file replacement/hash change invalidate mapping เก่า
- [ ] mapping แยกตาม Official server และ user
- [ ] restart Adapter แล้วยังรักษา verified mapping ที่ถูกต้อง
- [ ] by-hash response ส่งเฉพาะ identity fields ที่ plugin ใช้และไม่ทำให้ client crash

## 6. EPUB native progress — KOReader parity

- [ ] Device A push XPointer แล้ว native server state ตรง
- [ ] Device B pull แล้วกระโดดไป XPointer เดียวกับ Device A
- [ ] Device B push กลับแล้ว Device A pull ตรง
- [ ] progress ต่ำกว่า 1% เช่น `55/16653 ≈ 0.33%` ไม่ถูก scale ซ้ำ
- [ ] ตำแหน่งแรกทำให้ status เปลี่ยนจาก `UNREAD` เป็น `READING`
- [ ] อ่านเดินหน้าแล้ว sync ถูกต้อง
- [ ] อ่านย้อนกลับบทเก่าแล้ว native Pull ย้อนตาม
- [ ] stale timestamp ถูกปฏิเสธ
- [ ] force override ทำงานเฉพาะ progress conflict ที่อนุญาต
- [ ] newer manual status ไม่ถูก progress replay เก่าทับ
- [ ] native Pull preserve remote XPointer และไม่ใช้ cache แทน location
- [ ] offline progress queue replay แล้วไม่สูญหายหรือ duplicate

## 7. EPUB Web Reader projection — known issue

- [ ] บันทึก baseline เมื่อ XPointer → CFI conversion สำเร็จ
- [ ] บันทึก fixture/log เมื่อ conversion ล้มเหลว
- [ ] ยืนยันว่า native XPointer เปลี่ยนแม้ Web Reader CFI ค้าง
- [ ] ทดสอบ Web Reader หลัง KOReader อ่านเดินหน้า
- [ ] ทดสอบ Web Reader หลัง KOReader ย้อนกลับบทเก่า
- [ ] จัดผลเป็น `FAIL — KNOWN ISSUE` จนกว่า Web Reader E2E จะผ่านจริง
- [ ] ห้ามจัด HTTP `200` หรือ percentage เปลี่ยนเป็น Web Reader PASS

ปัญหานี้บันทึกไว้ใน `docs/PROGRESS_SYNC_STATUS.md` และยังไม่อยู่ใน scope
การแก้ของ test run นี้

## 8. PDF/fixed-page progress

- [ ] Push current page และ percentage ผ่าน native route
- [ ] Pull กลับได้ page/percentage เดิม
- [ ] Device A → Device B ไปหน้าถูกต้อง
- [ ] App/Web projection แสดง page และ percentage ถูกต้อง
- [ ] อ่านย้อนหน้ากลับแล้วทุก projection ย้อนตาม
- [ ] หน้าแรกทำให้ status เป็น `READING`
- [ ] missing book ID resolve ด้วย verified hash ได้
- [ ] stale timestamp/conflict ไม่เขียน state บางส่วน
- [ ] App projection failure ไม่ทำให้ native state อยู่ในสภาพครึ่งสำเร็จที่รายงานผิด

## 9. Regular และ magic shelves

- [x] list regular shelves ทั้ง empty และ non-empty
- [x] list books ใน regular shelf ครบ
- [x] list magic shelves ครบ
- [x] magic shelf หลายหน้าถูกอ่านครบโดยไม่มี duplicate
- [x] snapshot drift ระหว่าง pagination fail closed
- [x] regular และ magic shelf ที่มี numeric ID เดียวกันไม่ชนกัน
- [x] same book อยู่หลาย shelf แล้วยังรักษา ownership ทุก shelf
- [x] book ที่ไม่มี primary file ยังอ่านรายการได้โดยไม่สร้าง download ปลอม
- [x] series name/number และ file format/extension ถูกต้อง

## 10. Download และ local file safety

- [x] regular shelf download ได้ binary ที่ถูกต้อง
- [x] ตรวจ content type, signature, size และ checksum
- [x] timeout/HTML error response ไม่ถูกบันทึกเป็น ebook
- [x] first-read stream failure ปิด stream/client ถูกต้อง
- [x] existing user-owned file ไม่ถูกแทนหรือลบ
- [x] Adapter-managed file ถูก register หลังเขียนสำเร็จเท่านั้น
- [x] path traversal และ path นอก managed root ถูกปฏิเสธ

## 11. Shelf mutation และ cleanup

- [x] regular shelf removal เรียก Official bulk unassign ถูก payload
- [x] Official mutation สำเร็จก่อนเปลี่ยน local ownership
- [x] timeout เก็บ pending outbox และ retry operation เดิม
- [x] `401`/`403` ไม่ถูกแปลงเป็น success
- [x] magic shelf manual removal ถูกปฏิเสธและไม่มี remote mutation
- [x] cleanup ลบเฉพาะ Adapter-managed file หลัง complete snapshot
- [x] ไม่ลบเมื่อหนังสือยังอยู่ shelf อื่น
- [x] ไม่ลบเมื่อ magic shelf หรือ provider อื่นยังอ้างถึง
- [x] ไม่ลบ user-owned, replaced หรือ size-changed file
- [x] incomplete snapshot ไม่ทำ cleanup
- [x] owner scope ของ user หนึ่งไม่แก้ ownership ของอีก user

## 12. Ratings, bookmarks และ annotations

- [ ] rating push 1–10 → Official 1–5 ถูกต้อง
- [ ] rating pull คืนค่าเดิมโดยไม่ conversion drift
- [ ] rating reset สำเร็จ
- [ ] bookmark create/update/delete/pull สำเร็จ
- [ ] duplicate bookmark ไม่ถูกสร้างซ้ำ
- [ ] bookmark timeout-after-commit ถูก probe ก่อน retry
- [ ] delete mapping หลัง upstream ยืนยัน success/404 เท่านั้น
- [ ] unmapped delete ไม่รายงาน success
- [ ] annotation round-trip สำหรับ fields ที่ Official รองรับ
- [ ] unsupported annotation fields ถูก preserve ใน Adapter SQLite
- [ ] raw XPointer ไม่ถูกยัดลง CFI field
- [ ] remote deletion ถูกส่งกลับเป็น tombstone
- [ ] metadata cursor แยก server/user/book/file/type
- [ ] paginated pull ไม่ข้ามหรือทำรายการซ้ำ

## 13. Reading sessions

- [ ] single session ถูกสร้างด้วยเวลา/duration/progress ที่ถูกต้อง
- [ ] batch fanout และ aggregate counts ถูกต้อง
- [ ] invalid item ใน batch ไม่ยกเลิกรายการ valid อื่น
- [ ] duplicate retry ไม่ POST upstream ซ้ำ
- [ ] same timestamps คนละ device สร้างคนละ session
- [ ] stale book ID ถูก resolve ด้วย authoritative hash
- [ ] unresolvable hash ถูกปฏิเสธและไม่ retry แบบเดา
- [ ] timeout-after-POST เปลี่ยนเป็น `PENDING`
- [ ] retry probe ทุกหน้าและ adopt เฉพาะ unique unclaimed match
- [ ] Adapter restart แล้ว pending reconciliation ทำงาน
- [ ] recovery ใช้ credential ของ owner เดียวกันเท่านั้น
- [ ] unresolved/ambiguous outcome ไม่ blind replay

## 14. OPF ingestion และ source-file safety

- [ ] dry-run ไม่มี API write, SQLite write หรือ sidecar write
- [ ] exact hash/identity proof ผ่านก่อน mutation
- [ ] metadata API success ไม่สร้าง sidecar โดยไม่จำเป็น
- [ ] timeout/eligible failure ใช้ sidecar fallback ตาม policy
- [ ] auth/permission failure ไม่ fallback เป็น mutation ทางอื่น
- [ ] global/per-field locks ถูกเคารพ
- [ ] unchanged metadata ถูก deduplicate
- [ ] title, author, series, ISBN, description และ date ถูก normalize ถูกต้อง
- [ ] cover sidecar เป็น object form และ JPEG ตาม contract
- [ ] source ebook/PDF/CBX checksum ไม่เปลี่ยน
- [ ] path ambiguity/path escape fail closed
- [ ] Official Import All อ่าน sidecar ที่สร้างได้

## 15. Restart, offline และ failure recovery

- [ ] restart Adapter ระหว่างไม่มี pending operation
- [ ] restart Adapter ขณะมี pending shelf mutation
- [ ] restart Adapter ขณะมี pending metadata mutation
- [ ] restart Adapter ขณะมี pending reading session
- [ ] Official unavailable แล้ว Adapter ตอบ typed `502/504` ตามกรณี
- [ ] Official กลับมาแล้ว queue/reconciliation ทำงาน
- [ ] network timeout หลัง possible commit ไม่สร้าง duplicate
- [ ] offline KOReader queue replay หลัง reconnect
- [ ] failure ใน queue หนึ่งไม่หยุด queue อื่นทั้งหมด
- [ ] SQLite migration จาก existing test DB ผ่านและข้อมูลเดิมยังอยู่

## 16. Security และ observability

- [ ] Bearer, refresh token, password และ MD5 key ไม่ปรากฏใน logs/errors/repr
- [ ] mixed-case headers ไม่ข้าม auth mode
- [ ] JWT route ไม่ส่ง MD5 headers และ KOReader route ไม่ส่ง Bearer
- [ ] SQL lookup เป็น parameterized SELECT-only
- [ ] DB account grants จำกัดเฉพาะตาราง/เครือข่ายที่อนุญาต
- [ ] logs มี operation type, result และ timestamp โดยไม่มี secret
- [ ] healthcheck ไม่เปิดเผย credential หรือ internal state ที่อ่อนไหว

## 17. Production smoke test หลัง staging ผ่าน

- [ ] image/commit บน production ตรงกับ release candidate
- [ ] container `running` และ `healthy`, restart count ปกติ
- [ ] read-only auth/book/shelf/progress GET ผ่านด้วย test fixture ที่อนุญาต
- [ ] ทำ bounded progress write ด้วย test book/user เท่านั้น
- [ ] ยืนยัน native Pull หลัง write
- [ ] ตรวจ logs ช่วงทดสอบว่าไม่มี error/secret
- [ ] ยืนยัน rollback image และ persistent SQLite backup พร้อมใช้

## 18. Exit criteria

- [ ] ทุกหัวข้อมีผลใน `docs/PARITY_REPORT.md`
- [ ] ไม่มี `NOT_RUN` ใน critical sync/data-safety paths
- [ ] ไม่มี `BLOCKER` หรือ data-loss/sync-safety `FAIL`
- [ ] `ACCEPTED_DIFFERENCE` มีเหตุผลและผลกระทบชัดเจน
- [ ] Known Web Reader CFI failure ถูกบันทึกตรงตามหลักฐานและไม่ถูกนับเป็น PASS
- [ ] checksum/counts ก่อนและหลังตรงตาม expected mutations
- [ ] reviewer อนุมัติผลก่อน cutover/release decision

