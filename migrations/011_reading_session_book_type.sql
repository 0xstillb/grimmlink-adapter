-- Add the original book type needed for faithful reconciliation metadata.
-- Kept separate so databases that already applied 010 can migrate safely.
ALTER TABLE reading_sessions_state ADD COLUMN book_type TEXT;

INSERT OR IGNORE INTO schema_version (version) VALUES (11);
