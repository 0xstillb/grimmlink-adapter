# GrimmLink → Official Grimmory Migration Prompt Pack

## Execution model — recommended

ใช้ **Gemini Flash 3.8 เป็น implementer หลัก** และใช้ **GPT-5.6 Sol High เป็น review gate ระหว่าง session** ไม่ใช่รอตรวจทีเดียวตอนท้าย

- Gemini: inspect → implement → test → update HANDOFF → STOP before merge
- Sol: review architecture/semantics/data safety → APPROVE or FIX_REQUIRED
- Session 02/03/05/06/07/08/09/10/11 เป็น deep-review gates
- ดู `WORKFLOW_GEMINI_SOL.md` และ `SOL_REVIEW_TEMPLATE.md`


เป้าหมาย: ย้าย GrimmLink ออกจาก Grimmory fork โดย **ไม่แก้ Official Grimmory**, ไม่เขียน DB โดยตรง และรักษา behavior สำคัญของ fork ให้ครบ

Session 03 has a user-approved, read-only MariaDB lookup exception for exact
hash-to-book identity. It is disabled by default and requires a dedicated
SELECT-only DB account on a restricted network. It never writes to Grimmory's
database. See `sessions/03_BOOK_IDENTITY.md` and `docs/HANDOFF.md`.

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
