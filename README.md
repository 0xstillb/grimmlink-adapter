# GrimmLink Adapter

Standalone compatibility adapter bridging the [GrimmLink](https://github.com/0xstillb/GrimmLink) KOReader plugin to **stock [Official Grimmory](https://github.com/grimmory-tools/grimmory)** via standard HTTP/JSON APIs.

---

## 1. Overview & Architecture

Historically, GrimmLink interacted with a customized Grimmory fork (`0xstillb/grimmory`) that included a dedicated Spring Boot namespace (`/api/grimmlink/v1`) with custom database tables (`grimmlink_metadata_items`, flyway migrations V9001/V9002), MD5 auth headers, and specialized batch endpoints.

**GrimmLink Adapter** removes all custom server-side fork code and allows running **unmodified, stock Official Grimmory** (`grimmory-tools/grimmory:v3.5.0+`).

```text
+-------------------------+
|   KOReader / GrimmLink   |
|   (Existing Plugin)     |
+------------+------------+
             |
             | Legacy GrimmLink Wire Contract (/api/grimmlink/v1)
             | Transport: x-auth-user + MD5(x-auth-key)
             v
+-------------------------+
|    GrimmLink Adapter    | <---> [ Local SQLite Cache / Outbox ]
|  (Standalone FastAPI)   |       (Tokens, Hash Cache, Idempotency)
+------------+------------+
             |
             | Standard Official Grimmory HTTP API
             | - /api/v1/auth/login & refresh (Bearer JWT)
             | - /api/v1/shelves & /api/v1/app/shelves/magic
             | - /api/koreader/syncs/progress (Official KOReader sync)
             | - /api/v1/bookmarks, /api/v1/books, reading sessions
             v
+-------------------------+
|    Official Grimmory    |
|   (Unmodified Stock)    |
+-------------------------+
```

### Key Architectural Invariants
1. **Official Grimmory stays stock:** Zero source modifications, zero custom DB migrations in Grimmory.
2. **Zero Direct DB Access:** The adapter communicates with Grimmory exclusively through official HTTP REST APIs.
3. **SQLite Role:** SQLite in the adapter serves strictly as an **auxiliary cache, outbox queue, and idempotency store**—never as the primary source of truth.
4. **Wire Contract Preservation:** The existing KOReader GrimmLink client continues to send legacy `/api/grimmlink/v1` calls without requiring immediate client-side rewrites.
5. **Security Invariant:** Sensitive credentials (passwords, JWT tokens, MD5 keys) are **never** logged to stdout or persistent log files. Redaction filters are enforced at the logging level.
6. **OPF Scope:** OPF sidecar discovery and ingestion is handled separately by [Grimmory Bridge](https://github.com/0xstillb/grimmory-bridge) (`OPF -> .metadata.json / .cover.jpg -> Official Grimmory Import All`). The adapter does **not** process OPF files.

---

## 2. Tech Stack

- **Runtime:** Python 3.12+
- **Web Framework:** [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/)
- **HTTP Client:** [httpx](https://www.python-httpx.org/) (Async HTTP client for upstream Grimmory calls)
- **Validation:** [Pydantic v2](https://docs.pydantic.dev/) + `pydantic-settings`
- **Cache/Outbox Store:** SQLite via `aiosqlite`
- **Testing:** [pytest](https://pytest.org/), `pytest-asyncio`, `httpx` ASGI test client
- **Containerization:** Docker & Docker Compose

---

## 3. Project Structure

```text
grimmlink-adapter/
├── src/grimmlink_adapter/
│   ├── api/             # GrimmLink compatibility API routes (/api/grimmlink/v1/**)
│   ├── official/        # Upstream Official Grimmory HTTP client & endpoints
│   ├── services/        # Translation, normalization, business logic & dispatch
│   ├── state/           # SQLite cache, outbox queue & idempotency managers
│   ├── models/          # Pydantic schemas (GrimmLink wire DTOs, Official DTOs, Internal)
│   ├── security/        # Auth credential extractors & log masking/redaction
│   ├── config.py        # Environment & application settings
│   └── main.py          # FastAPI application factory
├── migrations/          # SQLite database schema migrations (cache/outbox)
├── tests/
│   ├── contract/        # GrimmLink legacy wire contract compatibility tests
│   ├── unit/            # Isolated unit tests (masking, normalization, models)
│   └── integration/     # End-to-end adapter pipeline tests
├── docs/
│   ├── ARCHITECTURE.md  # Detailed architecture specification & data flow
│   ├── MIGRATION_PLAN.md# 13-session defork migration roadmap
│   ├── HANDOFF.md       # Inter-session handoff state & review gates
│   └── ADR/
│       └── ADR-0001-external-adapter-choice.md
├── Dockerfile           # Minimal Python 3.12 container definition
├── docker-compose.example.yml
├── pyproject.toml       # Modern Python packaging configuration
└── .env.example         # Environment variable template
```

---

## 4. Quickstart

### Prerequisites
- Python 3.12+ (or [uv](https://docs.astral.sh/uv/))
- Docker & Docker Compose (optional, for containerized run)

### Local Development Setup
```bash
# Clone the repository
git clone https://github.com/0xstillb/grimmlink-adapter.git
cd grimmlink-adapter

# Install dependencies using uv
uv sync

# Run test suite
uv run pytest -v

# Run lint and format check
uv run ruff check .
uv run mypy src
```

### Running with Docker Compose
```bash
cp .env.example .env
# Edit .env with your Grimmory URL and settings
docker compose -f docker-compose.example.yml up -d
```

---

## 5. Development & Review Governance

This repository follows a strict **Gemini First, Sol Review Gate** workflow:
- **Implementer:** Gemini Flash 3.8
- **Review Gatekeeper:** GPT-5.6 Sol High (deep review on data safety, semantics, contracts)
- See [docs/MIGRATION_PLAN.md](docs/MIGRATION_PLAN.md) and [docs/HANDOFF.md](docs/HANDOFF.md) for details.
