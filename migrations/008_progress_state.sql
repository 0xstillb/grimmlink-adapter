-- Canonical progress snapshots used for optimistic conflict checks.
-- Official Grimmory remains the source of truth; this table is only a scoped
-- adapter cache and must never be treated as a substitute for an upstream read.

CREATE TABLE IF NOT EXISTS progress_state (
    owner_key TEXT NOT NULL,
    book_hash TEXT NOT NULL,
    book_id INTEGER,
    book_file_id INTEGER,
    format TEXT,
    native_location TEXT,
    current_page INTEGER,
    total_pages INTEGER,
    display_percent REAL,
    official_fraction REAL,
    device TEXT,
    device_id TEXT,
    timestamp_epoch INTEGER,
    updated_at TEXT,
    source TEXT,
    manual_status TEXT,
    manual_status_epoch INTEGER,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_key, book_hash)
);

CREATE INDEX IF NOT EXISTS idx_progress_state_book
    ON progress_state (owner_key, book_id, book_file_id);

CREATE TABLE IF NOT EXISTS manual_status_state (
    owner_key TEXT NOT NULL,
    book_id INTEGER NOT NULL,
    manual_status TEXT NOT NULL,
    manual_status_epoch INTEGER NOT NULL,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_key, book_id)
);

INSERT OR IGNORE INTO schema_version (version) VALUES (8);
