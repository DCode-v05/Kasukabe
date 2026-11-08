CREATE TABLE IF NOT EXISTS conversations (
    thread_id       TEXT PRIMARY KEY,
    charter_json    TEXT,
    scratchpad_json TEXT DEFAULT '{}',
    summary         TEXT NOT NULL DEFAULT '',
    current_state   TEXT NOT NULL DEFAULT '',
    recent_turns    TEXT NOT NULL DEFAULT '[]',
    turn_count      INTEGER NOT NULL DEFAULT 0,
    lc_watermark    INTEGER NOT NULL DEFAULT 0,
    pinned          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_activity   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id       TEXT NOT NULL REFERENCES conversations(thread_id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(thread_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_conv_messages_thread
    ON conversation_messages(thread_id, seq);
