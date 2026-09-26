# GrimmLink → Official Grimmory Migration Prompt Pack

## Execution model — recommended

ใช้ **Gemini Flash 3.8 เป็น implementer หลัก** และใช้ **GPT-5.6 Sol High เป็น review gate ระหว่าง session** ไม่ใช่รอตรวจทีเดียวตอนท้าย

- Gemini: inspect → implement → test → update HANDOFF → STOP before merge
- Sol: review architecture/semantics/data safety → APPROVE or FIX_REQUIRED
- Session 02/03/05/06/07/08/09/10/11 เป็น deep-review gates
- ดู `WORKFLOW_GEMINI_SOL.md` และ `SOL_REVIEW_TEMPLATE.md`


เป้าหมาย: ย้าย GrimmLink ออกจาก Grimmory fork โดย **ไม่แก้ Official Grimmory**, ไม่เขียน DB โดยตรง และรักษา behavior สำคัญของ fork ให้ครบ

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
OPF → GrimmLink Adapter → Official Metadata/Cover API (PRIMARY)
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
