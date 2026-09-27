-- Session 03A idempotency and audit state owned by the Adapter.
CREATE TABLE IF NOT EXISTS opf_ingestion (
    source_opf_path TEXT PRIMARY KEY,
    source_fingerprint TEXT NOT NULL,
    book_id INTEGER NOT NULL,
    metadata_hash TEXT NOT NULL,
    last_api_result TEXT,
    last_sidecar_result TEXT,
    fallback_reason TEXT,
    last_success_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_opf_ingestion_book_id ON opf_ingestion(book_id);
