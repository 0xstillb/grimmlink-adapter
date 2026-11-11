# GrimmLink Adapter · ภาษาไทย

[🇬🇧 English](README.en.md) · [🇹🇭 ภาษาไทย](README.th.md) · [← หน้าหลักโครงการ](README.md)

## ✨ Adapter ทำอะไร

GrimmLink Adapter เชื่อม KOReader และ Readest เข้ากับ **Grimmory แบบ stock ที่ไม่แก้ไขตัว server** ทำหน้าที่แปลงคำขอจาก reader, sync ข้อมูลการอ่าน และให้หน้าเว็บส่วนตัวสำหรับตั้งค่า Metadata กับตรวจสอบระบบ

- 📚 Sync shelf, progress, read status, bookmark, highlight, rating และ reading session ตามความสามารถของ reader/plugin ที่ตั้งค่าไว้
- 🔐 Reader ใช้บัญชี/ข้อมูลรับรอง KOReader หรือ Grimmory ของตัวเอง ส่วนหน้า Metadata เชื่อมบัญชี Grimmory สำหรับงานเบื้องหลังครั้งแรก
- 🗃️ Cache, credential, retry state และข้อมูล sync ของ Adapter เก็บใน SQLite ของ Adapter เอง
- 🧭 ใช้ OPF และ JSON sidecar เป็นแหล่ง metadata ได้ ตัวอย่าง Compose สำหรับ Pi เขียนผ่าน Grimmory API และ mount โฟลเดอร์หนังสือแบบ read-only
- 🚫 Adapter ไม่แทนที่ Grimmory, ไม่เขียน MariaDB ของ Grimmory โดยตรง และไม่ต้องเปิด port สู่อินเทอร์เน็ตสาธารณะ

## 🚀 เลือกวิธีใช้งาน

| วิธี | เหมาะกับ | URL สำหรับ reader |
| --- | --- | --- |
| รัน Python ในเครื่อง | ทดลองบน Windows/macOS/Linux หรือใช้จากเครื่องเดียวกัน | `http://localhost:8085` |
| Docker บน Raspberry Pi | เปิดตลอดเพื่อ sync ในบ้านหรือผ่าน Tailscale | `http://<Pi-IP>:8085` |

> **เรื่อง localhost:** `localhost` หมายถึงเครื่องที่กำลังเปิดแอปอยู่เสมอ Reader อีกเครื่องจะใช้ `localhost` เพื่อเรียก Adapter บน PC หรือ Pi ไม่ได้ ต้องใช้ IP ของเครื่องนั้นแทน

## 🖥️ รันในเครื่อง Windows/macOS/Linux

### ต้องมีอะไรบ้าง

- Python 3.12 ขึ้นไป
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Grimmory ที่เครื่องนี้เรียกถึงได้ (ค่าเริ่มต้น `http://localhost:6060`)

### ติดตั้งและเปิดใช้งาน

```bash
git clone https://github.com/0xstillb/grimmlink-adapter.git
cd grimmlink-adapter
uv sync
```

สร้างไฟล์ `.env` จาก template:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

แก้ `.env` อย่างน้อยให้มีค่านี้:

```dotenv
GRIMMORY_BASE_URL=http://localhost:6060
ADAPTER_HOST=0.0.0.0
ADAPTER_PORT=8085
GRIMMORY_DB_ENABLED=false
```

จากนั้นเริ่ม Adapter:

```bash
uv run python -m grimmlink_adapter.main
```

เปิด **[http://localhost:8085](http://localhost:8085)** หน้าแรกจะพาไปหน้า Metadata ที่ `/api/grimmlink/v2/local/metadata`

### ใช้จากอุปกรณ์อีกเครื่อง

ดู LAN IP ของ PC แล้วตั้ง URL ใน reader เป็น `http://<PC-LAN-IP>:8085` อนุญาต TCP port `8085` ใน firewall เฉพาะ private LAN หากอยู่นอกบ้านให้เชื่อม Tailscale แล้วใช้ Tailscale IP ของ PC อย่าทำ port forwarding `8085` จาก router

ถ้า Grimmory ไม่ได้รันในเครื่องเดียวกัน ให้ตั้ง `GRIMMORY_BASE_URL` เป็น URL ที่ **process ของ Adapter เรียกถึงได้** เช่น IP ของ Grimmory แทน `localhost` ถ้า Adapter อยู่ใน Docker, `localhost` จะหมายถึง container ของ Adapter เอง ให้ใช้ชื่อ service ของ Grimmory บน Docker network เดียวกัน หรือ address ของ host ที่ container เรียกถึงได้

## 🍓 ติดตั้งใหม่บน Raspberry Pi

วิธีนี้ติดตั้ง **เฉพาะ Adapter** โดยสมมติว่ามี Grimmory, MariaDB, Docker Compose และ mount NAS อยู่บน Pi แล้ว แนะนำ host ที่ใช้ Raspberry Pi OS/Docker แบบ 64-bit

### 1. สร้างโฟลเดอร์และ volume เก็บข้อมูล

```bash
sudo mkdir -p /opt/grimmlink-adapter
cd /opt/grimmlink-adapter
sudo curl -fsSL https://raw.githubusercontent.com/0xstillb/grimmlink-adapter/main/deploy/pi-compose.yml -o compose.yml
sudo docker volume create grimmlink_adapter_data
```

Named volume นี้เก็บสถานะของ Adapter ให้คงอยู่เมื่อลบหรืออัปเดต container

### 2. สร้างไฟล์ `.env` ส่วนตัว

```bash
sudo nano .env
```

ใส่ค่าเหล่านี้ แล้วเปลี่ยนตัวอย่างเป็นค่าจริง:

```dotenv
GRIMMLINK_ADAPTER_IMAGE=ghcr.io/0xstillb/grimmlink-adapter:latest
LAN_IP=192.168.1.50
NAS_ROOT=/mnt/nas/books
GRIMMORY_DB_HOST=172.18.0.2
GRIMMORY_DB_USER=grimmlink_readonly
GRIMMORY_DB_PASSWORD=replace-with-a-strong-password
GRIMMORY_DB_NAME=grimmory
GRIMMORY_DB_PORT=3306
```

- `LAN_IP`: จอง IP ของ Pi ให้คงที่ใน router
- Compose บน Pi จะ listen ที่ `0.0.0.0` เพื่อให้เรียกได้ทั้งจาก LAN และ Tailscale ให้จำกัด TCP `8085` ใน firewall ของ Pi เฉพาะ network ที่ไว้ใจ และอย่าทำ port forwarding จาก router
- `NAS_ROOT`: **path บน host ของ Pi** ที่ Grimmory ใช้อ่านหนังสือได้ Adapter จะ mount path นี้เป็น `/imports` แบบ read-only
- `GRIMMORY_DB_HOST`: address ของ MariaDB ที่ container แบบ host-network ของ Pi เรียกถึงได้ ซึ่งอาจไม่ใช่ URL ของ Grimmory API ถ้าไม่แน่ใจ ให้ตรวจ container/network ของ MariaDB บน Pi และใช้ address ที่เชื่อมต่อด้วยบัญชี read-only ได้
- `GRIMMORY_DB_USER` / `PASSWORD`: ใช้บัญชี MariaDB แยกที่มีสิทธิ์ **SELECT เท่านั้น** ห้ามใช้บัญชี admin ของ Grimmory
- `GRIMMLINK_ADAPTER_IMAGE`: image ที่จะใช้จาก GHCR ค่าเริ่มต้น `latest` จะถูก publish เมื่อ build ของ `main` หรือ version release ผ่าน validation แล้ว หากต้องการ pin เวอร์ชัน ให้เปลี่ยน `latest` เป็น version/commit tag แบบระบุแน่นอน

การค้นหา hash ของหนังสือบางเล่มครั้งแรกบน Grimmory stock ต้องใช้ DB lookup นี้ ซึ่งเป็นแบบอ่านอย่างเดียว เก็บ `.env` ให้เป็นส่วนตัว:

```bash
sudo chmod 600 .env
```

### 3. เริ่ม Adapter

```bash
sudo docker compose -f compose.yml config
sudo docker compose -f compose.yml pull
sudo docker compose -f compose.yml up -d
```

ตรวจสถานะ:

```bash
sudo docker compose -f compose.yml ps
sudo docker compose -f compose.yml logs --tail=100 grimmlink-adapter
curl -fsS http://127.0.0.1:8085/healthcheck
curl -i http://127.0.0.1:8085/readyz
```

`/healthcheck` ตรวจว่า process Adapter ยังทำงานหรือไม่ ส่วน `/readyz` ตอบ `200` เมื่อเรียก Grimmory ได้ และ `503` ระหว่างที่ Grimmory ยังเริ่มระบบหรือยังติดต่อไม่ได้ ถ้า `/readyz` เป็น `503` ไม่ได้แปลว่าต้อง restart Adapter

### 4. เปิดหน้าเว็บและเชื่อม reader

- เปิดจาก Pi เอง: **[http://localhost:8085](http://localhost:8085)**
- อุปกรณ์ในบ้าน: **`http://<Pi-LAN-IP>:8085`**
- อยู่นอกบ้านและต่อ Tailscale: **`http://<Pi-Tailscale-IP>:8085`**

ใน KOReader และ Readest ให้ใส่ URL ข้างต้นในช่อง GrimmLink/Adapter server URL อย่าใส่ `localhost` บนอุปกรณ์ reader อีกเครื่อง ปิด router port-forwarding ไว้ และใช้ Tailscale สำหรับเชื่อมจากนอกบ้าน

## ⚙️ ตั้งค่าและใช้งานครั้งแรก

### Sync reader

1. เปิดการตั้งค่า GrimmLink/Adapter ใน reader
2. ตั้ง server URL เป็น address ของ Adapter เช่น `http://192.168.1.50:8085`
3. เข้าสู่ระบบด้วยข้อมูลบัญชี KOReader/Grimmory ที่ reader ใช้อยู่ แล้วสั่ง refresh/sync shelf

เลือก IP ที่อุปกรณ์ reader เรียกถึง: `localhost` ใช้ได้เฉพาะเครื่องเดียวกัน, ในบ้านใช้ Pi LAN IP และเมื่อต่อจากนอกบ้านผ่าน Tailscale ให้ใช้ Pi Tailscale IP

### หน้า Metadata

1. เปิด `http://<Adapter-address>:8085/` แล้วเข้าสู่ระบบด้วยบัญชี Grimmory ที่ใช้จัดการ Library
2. ที่ **Setup** เชื่อมบัญชีงานเบื้องหลังครั้งเดียว หลังจากนั้นหน้าเว็บใช้ Metadata-only session อายุสั้นได้
3. ที่ **Diagnostics** กดตรวจระบบ แก้รายการที่ไม่ผ่าน เช่น Grimmory API, database identity lookup, NAS root หรือ mDNS ก่อนพึ่งพา automation
4. ที่ **Metadata** โหลด Library แล้วจับคู่ path ของแต่ละ Grimmory Library กับ NAS root/โฟลเดอร์ที่ mount ไว้ ตัวอย่าง Compose บน Pi ใช้ `/imports`
5. เลือก source/fields ที่ต้องการแล้วบันทึก policy หากต้องการตรวจทีละไฟล์ ให้ Scan → Preview → Apply เพื่อยืนยันการเขียน

เมื่อเปิด Auto ระบบทำงานกับหนังสือใหม่ได้ หนังสือเก่าอาจต้องสั่ง backfill หนึ่งครั้ง Compose สำหรับ Pi เขียน Metadata ผ่าน API เท่านั้นและไม่เขียนลง NAS ที่ mount แบบ read-only ส่วนการเลือก source รองรับ OPF และ JSON sidecar fallback เมื่อ policy ของ Library อนุญาต การเปลี่ยนปกอาจยังค้างเป็น pending หาก Grimmory ไม่มี upload route ที่ปลอดภัยและรองรับ

### ค่าตั้งค่าหลัก

| ตัวแปร | ใช้ทำอะไร | ตัวอย่าง |
| --- | --- | --- |
| `GRIMMORY_BASE_URL` | URL Grimmory ที่ Adapter เรียกได้ | local host: `http://localhost:6060`; Pi host-network: `http://<Pi-LAN-IP>:6060` |
| `ADAPTER_HOST` / `ADAPTER_PORT` | address และ port ที่ Adapter รับการเชื่อมต่อ | `0.0.0.0` / `8085` |
| `GRIMMORY_DB_ENABLED` | เปิด DB lookup แบบอ่านอย่างเดียว | Compose บน Pi ตั้งเป็น `true` |
| `GRIMMORY_DB_HOST`, `USER`, `PASSWORD` | บัญชี MariaDB SELECT-only สำหรับ lookup | เก็บใน `.env` ส่วนตัว |
| `SQLITE_DB_PATH` | ฐานข้อมูล cache/outbox ของ Adapter | local: `data/grimmlink_adapter.db`; Pi: persistent volume |
| `METADATA_IMPORT_ROOTS` | โฟลเดอร์ที่หน้า Metadata ใช้ scan ได้ | local: `[]`; Pi Compose ใช้ `[/imports]` |
| `METADATA_INGESTION_MODE` | วิธีเขียน Metadata | `api_only` เขียนผ่าน Grimmory API |
| `METADATA_FALLBACK` | เปิด/ปิด output-sidecar fallback | `none` ปิดการเขียน output sidecar |
| `MDNS_ENABLED` | ประกาศชื่อ `grimmlink.local` ใน LAN | `true` |

ดูค่าขั้นสูง เช่น timeout, TLS และ Metadata ได้ใน [`.env.example`](.env.example) การแก้ `.env` ต้อง restart process/container ของ Adapter

## 🔄 อัปเดตและสำรองข้อมูล

Compose บน Pi ใช้ `latest` เป็นค่าเริ่มต้น และตรวจ image ใหม่จาก registry เมื่อสั่งเริ่ม service หากต้องการดึง image ล่าสุดที่ publish แล้ว ให้รัน:

```bash
sudo docker compose -f compose.yml pull grimmlink-adapter
sudo docker compose -f compose.yml up -d --no-deps grimmlink-adapter
sudo docker compose -f compose.yml ps
```

สำรอง `.env` และ Docker volume `grimmlink_adapter_data` ไปด้วยกัน ใน volume มี SQLite state และ Metadata encryption key หาก key หายอาจต้องเชื่อมบัญชีเบื้องหลัง/บัญชีที่ผูกไว้อีกครั้ง เก็บ backup และ `.env` ให้เป็นส่วนตัว อย่าลบ volume ตอนอัปเดต container

## 🛠️ แก้ปัญหาเบื้องต้น

| อาการ | ตรวจสอบ |
| --- | --- |
| Reader เชื่อมต่อไม่ได้ | URL ต้องเป็น LAN IP ของ Pi/PC และ port `8085`; ตรวจว่า Adapter ทำงานและ firewall อนุญาต TCP `8085` ใน LAN |
| เปิดเว็บบน Pi ได้ แต่ reader ใช้ไม่ได้ | Reader อีกเครื่องใช้ `localhost` ไม่ได้ ให้ใช้ Pi LAN IP หรือ Tailscale IP |
| `/healthcheck` ผ่าน แต่ `/readyz` เป็น `503` | Grimmory กำลัง start หรือเรียกไม่ถึง ตรวจ `GRIMMORY_BASE_URL` และ log ของ Grimmory; Adapter ไม่ต้องรอ Grimmory ตอน start |
| Metadata หาไฟล์ไม่พบ | ตรวจว่า `NAS_ROOT` มีอยู่และ mount แล้ว รวมถึง Library mapping ชี้ไปยัง subfolder ที่ถูกต้องใต้ `/imports` |
| หา book/hash ไม่เจอ | ตรวจว่า MariaDB address เรียกถึง และบัญชีมี SELECT grants ที่จำเป็น |
| container ปิดหลังแก้ config | รัน `docker compose -f compose.yml config` และดู `docker compose -f compose.yml logs --tail=100 grimmlink-adapter` |

URL ที่ใช้บ่อย:

- หน้า Metadata: `/` หรือ `/api/grimmlink/v2/local/metadata`
- ตรวจ process: `/healthcheck`
- ตรวจว่า Grimmory พร้อมใช้งาน: `/readyz`
- API schema สำหรับ local development: `/docs`

## 🔒 เครือข่ายและความปลอดภัยของข้อมูล

- อย่าเปิด port `8085` สู่อินเทอร์เน็ตสาธารณะหรือทำ port forwarding ที่ router
- ใช้ home LAN ส่วนตัวหรือ Tailscale สำหรับ reader และหน้า Metadata
- เก็บ `.env`, SQLite และ encryption key เป็นส่วนตัว และสำรองไว้ด้วยกัน
- Compose สำหรับ Pi mount `/imports` แบบ read-only และเขียนผ่าน Grimmory API เท่านั้น Adapter ไม่เขียน MariaDB โดยตรง
- ใช้ Adapter worker/replica เดียว จนกว่าจะมี shared session store

## 📖 เอกสารเพิ่มเติม

- [ข้อกำหนด Adapter](docs/ADAPTER_V2_SPEC.md)
- [รายการตรวจสอบ reader/server แบบ end-to-end](docs/E2E_CHECKLIST.md)
- [สรุปส่งมอบและปฏิบัติการ](docs/HANDOFF.md)
- [บันทึกประสิทธิภาพ upstream API](docs/UPSTREAM_PERFORMANCE.md)
