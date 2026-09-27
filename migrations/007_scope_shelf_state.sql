-- Session 05 review repair: isolate shelf and managed-file state per actor.
ALTER TABLE shelf_ownership_cache RENAME TO shelf_ownership_cache_v6;
CREATE TABLE shelf_ownership_cache (
    owner_key TEXT NOT NULL DEFAULT 'default',
    book_id INTEGER NOT NULL,
    shelf_id INTEGER NOT NULL,
    shelf_type TEXT NOT NULL,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_key, book_id, shelf_id, shelf_type)
);
INSERT INTO shelf_ownership_cache (owner_key, book_id, shelf_id, shelf_type, cached_at)
SELECT 'default', book_id, shelf_id, shelf_type, cached_at FROM shelf_ownership_cache_v6;
DROP TABLE shelf_ownership_cache_v6;

ALTER TABLE managed_files RENAME TO managed_files_v6;
CREATE TABLE managed_files (
    owner_key TEXT NOT NULL DEFAULT 'default',
    book_id INTEGER NOT NULL,
    book_file_id INTEGER NOT NULL DEFAULT 0,
    tracked_path TEXT NOT NULL,
    downloaded_by_grimmlink INTEGER NOT NULL DEFAULT 0,
    provider_reference_count INTEGER NOT NULL DEFAULT 0,
    expected_size INTEGER,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_key, book_id, book_file_id)
);
INSERT INTO managed_files (
    owner_key, book_id, book_file_id, tracked_path, downloaded_by_grimmlink,
    provider_reference_count, expected_size, cached_at
)
SELECT 'default', book_id, book_file_id, tracked_path, downloaded_by_grimmlink,
       provider_reference_count, expected_size, cached_at
FROM managed_files_v6;
DROP TABLE managed_files_v6;

INSERT OR IGNORE INTO schema_version (version) VALUES (7);
