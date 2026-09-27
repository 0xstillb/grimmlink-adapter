-- Per-server, per-user Official JWT credentials for locally linked KOReader accounts.
-- Only the adapter writes this table. Never put a Grimmory DB account here.
CREATE TABLE IF NOT EXISTS linked_accounts (
    server TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    md5_key_digest TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (server, user_id)
);
