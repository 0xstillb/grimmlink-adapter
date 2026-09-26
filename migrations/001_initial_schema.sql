-- Initial SQLite Schema for GrimmLink Adapter
-- Auxiliary cache, outbox, and idempotency tracking.
-- NOTE: Official Grimmory remains the source of truth.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Token & credential mapping (GrimmLink MD5 auth to Official Bearer JWT)
CREATE TABLE IF NOT EXISTS auth_tokens (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    access_token TEXT,
    refresh_token TEXT,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Book hash to Official Book ID resolution cache
CREATE TABLE IF NOT EXISTS book_hash_cache (
    book_hash TEXT PRIMARY KEY,
    book_id INTEGER NOT NULL,
    book_file_id INTEGER,
    format TEXT,
    file_size INTEGER,
    filename TEXT,
    title TEXT,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_book_hash_cache_book_id ON book_hash_cache(book_id);

-- Shelf book composite mapping cache for safe multi-shelf ownership
CREATE TABLE IF NOT EXISTS shelf_ownership_cache (
    book_id INTEGER NOT NULL,
    shelf_id INTEGER NOT NULL,
    shelf_type TEXT NOT NULL, -- 'regular' or 'magic'
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (book_id, shelf_id, shelf_type)
);

-- Resilient Outbox queue for offline / delayed upstream mutations
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING, PROCESSING, COMPLETED, FAILED
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 5,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status);

-- Idempotency tracking to avoid duplicate session or metadata submission
CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    response_status INTEGER NOT NULL,
    response_body TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Metadata sync cursor tracking per user/device/scope
CREATE TABLE IF NOT EXISTS metadata_cursors (
    cursor_key TEXT PRIMARY KEY,
    last_cursor TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
