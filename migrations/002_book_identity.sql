-- Session 03: Book identity mapping with server/user isolation
-- Maps (server, user, hash) → (bookId, bookFileId) so the plugin's
-- MD5-based lookups resolve to official API identifiers.
--
-- Primary key on (server, user, book_id, book_file_id) ensures one
-- mapping per file per account. book_file_id NOT NULL DEFAULT 0 prevents
-- NULL PK duplicate issues in SQLite.
-- Dual indexes on current_hash and initial_hash support the ordered
-- resolution chain with ambiguity detection.

CREATE TABLE IF NOT EXISTS book_identity (
    server        TEXT    NOT NULL,
    user          TEXT    NOT NULL,
    current_hash  TEXT    NOT NULL,
    initial_hash  TEXT    NOT NULL,
    book_id       INTEGER NOT NULL,
    book_file_id  INTEGER NOT NULL DEFAULT 0,
    title         TEXT,
    authors       TEXT,         -- JSON array string
    format        TEXT,
    file_size     INTEGER,
    filename      TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    verified_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (server, user, book_id, book_file_id)
);

CREATE INDEX IF NOT EXISTS idx_book_identity_current_hash
    ON book_identity (server, user, current_hash);

CREATE INDEX IF NOT EXISTS idx_book_identity_initial_hash
    ON book_identity (server, user, initial_hash);

INSERT OR IGNORE INTO schema_version (version) VALUES (2);
