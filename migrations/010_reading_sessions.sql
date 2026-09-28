-- Migration 010: Reading session state and reconciliation tracking
-- Official Grimmory remains the source of truth for reading sessions.
-- SQLite stores auxiliary idempotency tracking, pending/committed state,
-- and preserves unsupported legacy fields (book_hash, device, device_id, current_page, total_pages).

CREATE TABLE IF NOT EXISTS reading_sessions_state (
    idempotency_key TEXT PRIMARY KEY,
    server TEXT NOT NULL,
    user_id TEXT NOT NULL,
    book_id INTEGER NOT NULL,
    book_hash TEXT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    device TEXT,
    device_id TEXT,
    current_page INTEGER,
    total_pages INTEGER,
    start_progress REAL,
    end_progress REAL,
    start_page INTEGER,
    end_page INTEGER,
    start_location TEXT,
    end_location TEXT,
    official_session_id INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'POSTING', 'COMMITTED'
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reading_sessions_status
    ON reading_sessions_state (status);

CREATE INDEX IF NOT EXISTS idx_reading_sessions_lookup
    ON reading_sessions_state (server, user_id, book_id, start_time, end_time);

CREATE INDEX IF NOT EXISTS idx_reading_sessions_book
    ON reading_sessions_state (book_id);

INSERT OR IGNORE INTO schema_version (version) VALUES (10);
