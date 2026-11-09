CREATE TABLE metadata_accounts (
    server TEXT NOT NULL, user_id INTEGER NOT NULL, encrypted_credentials TEXT NOT NULL,
    blocked_reason TEXT,
    PRIMARY KEY (server, user_id)
);
CREATE TABLE metadata_libraries (
    server TEXT NOT NULL, library_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
    name TEXT NOT NULL, config TEXT NOT NULL, next_due REAL NOT NULL DEFAULT 0,
    enrolled_at REAL NOT NULL,
    PRIMARY KEY (server, library_id)
);
CREATE TABLE metadata_runs (
    id TEXT PRIMARY KEY, server TEXT NOT NULL, library_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
    requested_by TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    followup INTEGER NOT NULL DEFAULT 0, result TEXT NOT NULL DEFAULT '{}',
    cancel_requested INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX metadata_runs_queue ON metadata_runs(state, created_at);
CREATE TABLE metadata_items (
    server TEXT NOT NULL, library_id INTEGER NOT NULL, book_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL, baseline TEXT NOT NULL DEFAULT '{}',
    intent TEXT NOT NULL DEFAULT '{}', observation TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL,
    PRIMARY KEY (server, library_id, book_id)
);
CREATE TABLE metadata_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, server TEXT NOT NULL, library_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX metadata_events_library ON metadata_events(server,library_id,id);
