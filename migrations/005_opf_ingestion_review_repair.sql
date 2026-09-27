-- Extend Session 03A state without changing an already-applied migration.
ALTER TABLE opf_ingestion ADD COLUMN cover_fingerprint TEXT;
ALTER TABLE opf_ingestion ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE opf_ingestion ADD COLUMN last_result_status TEXT NOT NULL DEFAULT 'legacy';
