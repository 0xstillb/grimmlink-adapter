-- Migration 009: Metadata sync tables (remote mapping, applied history, scoped cursor)
-- Official Grimmory remains the source of truth for bookmarks and personal ratings.
-- SQLite stores auxiliary mappings, deduplication history, and cursor checkpoints.

CREATE TABLE IF NOT EXISTS metadata_remote_mappings (
    owner_key TEXT NOT NULL,
    book_id INTEGER NOT NULL,
    item_type TEXT NOT NULL, -- 'bookmark', 'annotation'
    local_id TEXT NOT NULL,
    remote_id INTEGER NOT NULL,
    dedupe_key TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_key, book_id, item_type, local_id)
);

CREATE INDEX IF NOT EXISTS idx_metadata_remote_dedupe
    ON metadata_remote_mappings (owner_key, book_id, dedupe_key);

CREATE INDEX IF NOT EXISTS idx_metadata_remote_id
    ON metadata_remote_mappings (owner_key, remote_id);

CREATE TABLE IF NOT EXISTS metadata_applied_history (
    owner_key TEXT NOT NULL,
    book_id INTEGER NOT NULL,
    item_type TEXT NOT NULL, -- 'rating', 'bookmark', 'annotation'
    dedupe_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    device TEXT,
    device_id TEXT,
    source_scale INTEGER,
    source_value REAL,
    official_value INTEGER,
    official_id INTEGER,
    payload_json TEXT,
    is_deleted INTEGER DEFAULT 0,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_key, book_id, item_type, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_metadata_history_hash
    ON metadata_applied_history (owner_key, book_id, content_hash);

CREATE INDEX IF NOT EXISTS idx_metadata_history_device
    ON metadata_applied_history (owner_key, device_id);

-- Scoped cursor isolation: strictly keyed by server, owner_key, book_id, book_file_id, item_type.
CREATE TABLE IF NOT EXISTS scoped_metadata_cursors (
    server TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    book_id INTEGER NOT NULL,
    book_file_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    last_cursor TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (server, owner_key, book_id, book_file_id, item_type)
);

INSERT OR IGNORE INTO schema_version (version) VALUES (9);
