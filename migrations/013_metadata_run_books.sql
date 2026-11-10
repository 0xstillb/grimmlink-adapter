CREATE TABLE metadata_run_books (
    run_id TEXT NOT NULL REFERENCES metadata_runs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    book TEXT NOT NULL,
    PRIMARY KEY (run_id, position)
) WITHOUT ROWID;

CREATE INDEX metadata_runs_ready ON metadata_runs(server, state, updated_at, available_at);
CREATE INDEX metadata_runs_activity ON metadata_runs(server, library_id, owner_id, created_at DESC);
CREATE INDEX metadata_items_activity ON metadata_items(server, library_id, updated_at DESC);

UPDATE metadata_runs
SET result = json_remove(result, '$._bookIds')
WHERE state IN ('completed', 'cancelled', 'failed', 'blocked')
  AND json_type(result, '$._bookIds') IS NOT NULL;

CREATE TRIGGER metadata_run_books_cleanup
AFTER UPDATE OF state ON metadata_runs
WHEN NEW.state IN ('completed', 'cancelled', 'failed', 'blocked')
BEGIN
    DELETE FROM metadata_run_books WHERE run_id = NEW.id;
END;
