# ADR-0001: Standalone External Adapter Architecture

- **Status:** Accepted
- **Date:** 2026-09-26
- **Authors:** 0xstillb, Gemini Flash 3.8
- **Reviewer:** GPT-5.6 Sol High (Session 00 Architecture Gate)

**Session 03 amendment (2026-09-26):** The user approved an optional,
dedicated SELECT-only Grimmory MariaDB lookup for exact book hashes. The
original zero-direct-access statements below describe the Session 00 design;
the active exception is documented in `sessions/03_BOOK_IDENTITY.md`. Direct
database writes remain prohibited.

---

## 1. Context and Problem Statement

The [GrimmLink](https://github.com/0xstillb/GrimmLink) KOReader plugin was originally designed to synchronize e-reading state (progress, shelves, ratings, bookmarks, reading sessions) with a fork of Grimmory ([0xstillb/grimmory](https://github.com/0xstillb/grimmory)).

The fork introduced several bespoke behaviors into the Grimmory backend:
1. A dedicated Spring Boot controller namespace (`/api/grimmlink/v1/**`).
2. Custom database tables (`grimmlink_metadata_items`, flyway migrations V9001 and V9002).
3. Legacy authentication using HTTP headers (`x-auth-user` + MD5 `x-auth-key`).
4. Flat data transfer objects (DTOs) tailored specifically for KOReader Lua serialization.
5. Server-side hash resolution (`/books/by-hash/{hash}`), reading session batching, and progress normalization.
6. Embedded/adjacent OPF discovery during library scanning.

Maintaining this fork created significant maintenance friction:
- Every upstream Grimmory release required manual rebasing and merge conflict resolution.
- Custom database schemas diverged from upstream migrations.
- Direct database access increased the risk of data corruption or lock contention.
- Upgrading Official Grimmory (`ghcr.io/grimmory-tools/grimmory`) was blocked by fork-specific features.

We need an architecture that allows users to run **unmodified, stock Official Grimmory** while continuing to use their KOReader devices with full fidelity, zero direct database access, and robust data safety.

---

## 2. Alternatives Considered

### Alternative A: Continue Maintaining the Grimmory Fork
- **Description:** Keep rebasing custom Spring Boot code and database migrations onto upstream Grimmory.
- **Pros:** Preserves existing wire contract without new components.
- **Cons:** High ongoing maintenance burden, delayed upstream updates, risk of schema divergence, and ongoing deviation from the official Grimmory ecosystem.
- **Verdict:** **Rejected**.

### Alternative B: Direct KOReader Lua Rewrite (No Server Middleware)
- **Description:** Rewrite the GrimmLink KOReader plugin in Lua to authenticate directly with Official Grimmory JWT endpoints and orchestrate all multi-step calls on the e-reader device.
- **Pros:** No intermediate server process required.
- **Cons:**
  - KOReader runs on resource-constrained embedded e-ink devices (Kindle, Kobo, Android e-ink).
  - Official Grimmory lacks batch endpoints for reading sessions and metadata; executing many HTTP calls from Lua over flaky e-reader Wi-Fi leads to high failure rates and battery drain.
  - Complex deduplication, outbox queuing, and hash resolution are difficult to test and maintain purely within KOReader Lua.
  - Requires immediate forced upgrade of all user devices.
- **Verdict:** **Rejected**.

### Alternative C: Standalone External Adapter (Chosen)
- **Description:** Introduce a lightweight Python (FastAPI + SQLite + httpx) middleware service placed between KOReader and stock Official Grimmory.
- **Pros:**
  - **Stock Official Grimmory:** Official Grimmory remains 100% unmodified and can be updated directly from official Docker images.
  - **Zero Direct DB Access:** Adapter communicates with Grimmory exclusively through its official REST API.
  - **Wire Contract Compatibility:** KOReader client continues speaking `/api/grimmlink/v1/**` during the migration phase.
  - **Reliable Local State:** SQLite in the adapter acts as an auxiliary cache, outbox queue, and idempotency store.
  - **Unified OPF Strategy (Session 03A):** OPF discovery and normalization are handled by the adapter targeting Official Grimmory Metadata/Cover APIs (Primary) with Sidecar JSON fallback, while preserving original ebook files untouched.
  - **Rapid Verification:** Easily testable via standard Python testing tools (`pytest`, `httpx`, CI).
- **Verdict:** **Accepted**.

---

## 3. Decision

We will build and deploy `grimmlink-adapter` as a standalone external service:

1. **Host & Runtime:** Python 3.12, FastAPI, Uvicorn, httpx, SQLite (`aiosqlite`), Pydantic v2.
2. **Official Grimmory Boundary:** Official Grimmory stays completely stock. The adapter communicates strictly via Official Grimmory HTTP endpoints. No direct SQL or database writes into Grimmory's DB.
3. **Compatibility Route Boundary:** All legacy `/api/grimmlink/v1/**` routes exist only in the adapter, never in Official Grimmory.
4. **SQLite Role:** The adapter's SQLite database is strictly an **auxiliary cache, outbox queue, and idempotency store**. Official Grimmory remains the sole source of truth for library entities.
5. **Security Invariant:** Sensitive credentials (passwords, JWT tokens, MD5 keys) must never be logged. Redaction filters are enforced at the application logging boundary.
6. **OPF Ingestion Strategy (Session 03A):** The adapter handles OPF discovery and normalization using Official Grimmory Metadata and Cover APIs as the primary destination, with structured `.metadata.json` and `.cover.jpg` sidecar generation as an approved fallback. Ingestion operates strictly without modifying source ebook files. Grimmory Bridge remains available as an external preview/migration tool.

---

## 4. Consequences and Invariants

### Positive
- Allows immediate migration to stock Grimmory container images.
- Protects user devices from complex multi-call network failures.
- Provides resilient idempotency and outbox retries for reading sessions and progress.
- Enforces strict contract freeze and step-by-step verification gates.

### Trade-offs & Mitigations
- **Operational Footprint:** Users run one additional container (`grimmlink-adapter`).
  - *Mitigation:* The container is lightweight (< 100MB RAM) and provided with ready-to-use `docker-compose.example.yml`.
- **Auxiliary State Management:** The adapter maintains a local SQLite database for cache and outbox.
  - *Mitigation:* SQLite tables are fully versioned with automated startup migrations and can be safely rebuilt if wiped (as Official Grimmory is source of truth).
