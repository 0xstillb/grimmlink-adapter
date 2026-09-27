-- Session 05: local file ownership markers for safe shelf cleanup.
-- A row is only eligible for deletion when it was downloaded by this adapter,
-- has no provider references, and the exact tracked path is verified.
CREATE TABLE IF NOT EXISTS managed_files (
    book_id INTEGER NOT NULL,
    book_file_id INTEGER NOT NULL DEFAULT 0,
    tracked_path TEXT NOT NULL,
    downloaded_by_grimmlink INTEGER NOT NULL DEFAULT 0,
    provider_reference_count INTEGER NOT NULL DEFAULT 0,
    expected_size INTEGER,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (book_id, book_file_id)
);

INSERT OR IGNORE INTO schema_version (version) VALUES (6);
