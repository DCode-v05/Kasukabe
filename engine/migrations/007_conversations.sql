-- Persistent conversation storage for threads.
-- conversations: per-thread metadata (charter, scratchpad, task-index).
-- conversation_messages: individual messages in sequence order.
-- Both tables deleted after 24h TTL — episodic memory is the long-term store.
-- Single-user engine — no user_id columns.

CREATE TABLE IF NOT EXISTS conversations (
    thread_id       TEXT PRIMARY KEY,
    charter_json    TEXT,
    scratchpad_json TEXT DEFAULT '{}',
    -- Thread-routing fields. recent_turns is a JSON-encoded bounded list (up to
    -- 3 short strings). NOT NULL with safe defaults so a row inserted before the
    -- first turn behaves sanely.
    summary         TEXT NOT NULL DEFAULT '',
    current_state   TEXT NOT NULL DEFAULT '',
    recent_turns    TEXT NOT NULL DEFAULT '[]',
    turn_count      INTEGER NOT NULL DEFAULT 0,
    lc_watermark    INTEGER NOT NULL DEFAULT 0,
    -- Pinned threads are exempt from the TTL reaper and every waiting-period
    -- expiry; the product layer pins a thread it has taken ownership of.
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
