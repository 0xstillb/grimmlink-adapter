# GrimmLink Progress Sync Status

สถานะนี้บันทึกปัญหา การแก้ไข หลักฐานการทดสอบ และ known issues ของ
`grimmlink-adapter` สำหรับ KOReader ↔ Grimmory Official

## Scope และข้อจำกัด

- แก้ที่ Adapter/protocol compatibility เป็นหลัก
- ไม่แก้ Grimmory Official UI หรือ mapper
- ไม่แก้ database ของ Grimmory โดยตรง
- native KOReader progress ใช้ค่า fraction ตาม contract เดิม
- ห้ามใช้ percentage แทนตำแหน่ง EPUB
- ห้ามบันทึกหรือเผยแพร่ token, password, refresh token หรือ credential

## ปัญหาที่พบและการแก้ไข

### 1. Stale reading session อ้างถึงหนังสือผิดเล่ม

**อาการ**

- session เก่าอ้าง `bookId` ที่ไม่มีอยู่แล้ว หรือ hash ไม่ตรงกับหนังสือปัจจุบัน
- ทำให้ Official ตอบ `404` และเกิด retry loop หรือ `422`

**การแก้**

- ใช้ hash ที่ตรวจสอบได้เป็น authoritative identity
- ไม่เดา mapping จาก stale `bookId`
- แยก invalid/stale state ออกจาก valid progress request

**Commit หลัก**

- `7d58faa` — resolve reading sessions by authoritative book hash
- `5b340f1` — resolve PDF progress by hash when book id is absent

### 2. เปิดหนังสือผ่าน native lookup แล้ว native client crash

**อาการ**

- response identity แบบเต็มจาก Adapter ทำให้ GrimmLink native path crash ในบาง flow
- crash signature เดิมเป็น `pthread_mutex_lock called on a destroyed mutex`

**การแก้**

- เพิ่ม minimal identity response สำหรับ native lookup
- คืนเฉพาะ identity ที่ GrimmLink ต้องใช้ เช่น `id`
- ไม่คืน field ส่วนเกินที่ทำให้ native lifecycle มีปัญหา

**Commit หลัก**

- `dfc809d` — proxy native GrimmLink book lookup
- `dceceec` — return minimal GrimmLink identity response

### 3. PDF Push/Pull ใช้คนละ progress channel

**อาการ**

- Push ผ่าน App/JWT แต่ Pull ผ่าน native KOReader endpoint
- ทำให้ Pull ไม่เห็นค่าที่ Push หรือเปิดหนังสือแล้วตำแหน่งไม่ต่อเนื่อง

**การแก้**

- ใช้ native KOReader progress endpoint เป็น source สำหรับ native Push/Pull
- preserve native progress response กลับไปยัง KOReader
- resolve หนังสือด้วย verified hash เมื่อ `bookId` ไม่มี

**Commit หลัก**

- `a3390b4` — round-trip PDF progress through native KOReader API
- `8efe088` — preserve native progress response for remote pull
- `5b340f1` — resolve PDF progress by hash when book id is absent

### 4. Grimmory App ไม่แสดง PDF progress ตามที่ KOReader ส่ง

**อาการ**

- native KOReader progress ถูกต้อง แต่ App projection ไม่อัปเดต
- Official overall UI อาจแสดง fraction เช่น `0.2%` แทน `20.2%` เนื่องจาก UI ใช้หน่วยไม่ตรงกับค่า `readProgress`

**การแก้**

- เขียน PDF projection เพิ่มทั้ง `page` และ `percentage`
- คง native percentage เป็น fraction เช่น `0.202` ห้ามแปลงเป็น `20.2` ใน native payload
- ไม่แก้ Official UI/mapper ตามขอบเขตงาน

**Commit หลัก**

- `ad0fa36` — mirror PDF progress to Grimmory app projection

### 5. Continue Read ไม่ขึ้นเมื่ออ่านเพียงหน้าแรก/ตำแหน่งแรก

**อาการ**

- percentage ยังปัดเป็น `0.0%` จึงไม่ผ่านเงื่อนไขเดิมของ Official
- PDF/EPUB ที่อ่านเพียงตำแหน่งแรกไม่ขึ้นสถานะ `READING`

**การแก้**

- mark `READING` เมื่อมีตำแหน่งจริงครั้งแรก
- PDF ใช้ current page/position
- EPUB ใช้ CFI/native location
- ไม่รอ display percentage และไม่บังคับค่า progress เป็น 10%

**Commit หลัก**

- `242b4b1` — mark first progress as reading
- `e2e4db0` — mark first EPUB position as reading

### 6. GrimmLink fork กับ Official ส่ง native EPUB response ไม่เหมือนกัน

**อาการ**

- fork คืน `fileFormat`, `bookId`, `bookFileId`, `progress` และ `location`
- Official native DTO มักคืน `progress`, `document`, `percentage`, `timestamp` แต่ไม่คืน identity/format metadata ครบ
- GrimmLink จึงเลือก EPUB jump flow ไม่เหมือน fork

**การแก้**

- เติมเฉพาะ verified identity/format metadata ที่ขาดหาย
- คง remote XPointer ที่ Official ส่งกลับ
- ไม่แทนที่ remote location ด้วยค่าจาก cache

**Commit หลัก**

- `9d9a2f3` — preserve EPUB identity on native pull

## Known issue ที่ยังไม่ได้แก้

### Official XPointer → EPUB CFI conversion ล้มเหลวบางตำแหน่ง

**หลักฐานที่พบ**

Official log พบกรณีลักษณะนี้:

```text
XPointer: /body/DocFragment[75]/body/h3/text().0
Error: Cannot find child h3[1]
```

**ผลกระทบ**

- KOReader native progress และ KOReader Pull อาจถูกต้อง
- percentage/native progress เปลี่ยนได้
- แต่ `epubProgress` แบบ CFI ของ Official อาจยังเป็นตำแหน่งเดิม
- Web Reader จึงไม่ย้อนตามตำแหน่งที่ KOReader กลับไปอ่าน

**สถานะ**

- ยังไม่ได้แก้ที่ Official `CfiConverter`/`EpubCfiService`
- ยังไม่ได้ทำ Adapter-side EPUB parser/CFI shim
- chapter-level fallback เป็นเพียงแนวทางที่เสนอ ยังไม่ถือว่า implemented
- ห้ามรายงานว่า Web Reader sync ตรงจาก HTTP `200` เพียงอย่างเดียว

**แนวทางแก้ที่ยังต้องทำ**

1. เพิ่ม regression fixture EPUB ที่ทำให้ XPointer conversion ล้มเหลว
2. เปรียบเทียบ XPointer, CFI ที่ Official สร้าง, native progress และ Web Reader CFI
3. เลือกหนึ่งแนวทาง:
   - แก้ Official converter ให้รองรับ DOM/XPointer ที่ KOReader ส่งจริง
   - ทำ Adapter-side XPointer → CFI converter โดยอ่าน EPUB ที่ verified แล้ว
   - ใช้ chapter-level fallback โดยประกาศชัดว่าไม่ละเอียดถึง paragraph
4. ทดสอบ Web Reader หลังแก้จริง ไม่สรุปจาก native Pull อย่างเดียว

## Test และ verification checklist

### Automated tests

- [x] Full pytest: `298 passed`
- [x] Progress normalization targeted tests: `19 passed`
- [x] Ruff: passed
- [x] mypy: `Success: no issues found in 49 source files`
- [x] `git diff --check`: passed

### Manual/E2E verification

- [x] PDF เปิดหนังสือได้
- [x] PDF Push ผ่าน native endpoint
- [x] PDF Pull ผ่าน
- [x] PDF Jump/Continue Read ผ่าน
- [x] PDF อ่านหน้าแรกแล้วขึ้น `READING`
- [x] EPUB เปิดหนังสือได้
- [x] EPUB อ่านตำแหน่งแรกแล้วขึ้น `READING`
- [x] EPUB native Pull รับ XPointer กลับได้
- [x] เปิด Tracking ไว้ระหว่างทดสอบ
- [x] Adapter production health เป็น `healthy`
- [x] ตรวจ log ให้สัมพันธ์กับเวลาทดสอบ
- [x] ไม่แก้ Official UI/mapper

### Git/release verification

- [x] Commit code changes รวมอยู่ใน branch release
- [x] Branch ถูก push ไป GitHub
- [x] `main` ถูก fast-forward จาก `28607af` ไป `9d9a2f3`
- [x] Remote `main` และ release branch ชี้ที่ `9d9a2f3`
- [x] Production image ที่ใช้งานอยู่คือ `grimmlink-adapter:9d9a2f3`
- [x] Production image revision ตรงกับ `9d9a2f3`
- [ ] แก้ Official XPointer → CFI conversion failure
- [ ] ยืนยัน Web Reader ย้อนตาม KOReader หลังแก้ CFI แล้ว

## Current release baseline

- Code baseline: `9d9a2f3443d44c42e7e8f4a22007cb6cd42ab9b3`
- Production tag: `grimmlink-adapter:9d9a2f3`
- Production image ID: `sha256:489efa1c511e05849cdfc88ac945d1cfe56164536780f7dbcf4e7a6396c96666`
- Production state at last verification: `running`, `healthy`, restart `0`
- Known unresolved issue: Official Web Reader CFI may remain stale when native XPointer conversion fails

เอกสารนี้เป็นสถานะตามหลักฐานที่ทดสอบแล้ว ไม่ถือว่า known issue ถูกแก้จนกว่าจะมี Web Reader E2E test ผ่านจริง
