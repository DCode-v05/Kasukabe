-- Resume state and pending input (replaces Redis).

CREATE TABLE IF NOT EXISTS resume_state (
    charter_id      TEXT PRIMARY KEY,
    state_json      TEXT NOT NULL,
    ttl_type        TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_input (
    thread_id         TEXT PRIMARY KEY,
    question        TEXT NOT NULL,
    context_json    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL
);
