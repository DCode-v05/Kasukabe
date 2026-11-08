-- Trajectories and Learning Centre queue.
-- Single-user engine — no user_id columns.

CREATE TABLE IF NOT EXISTS trajectories (
    trajectory_id   TEXT PRIMARY KEY,
    charter_id      TEXT NOT NULL,
    thread_id         TEXT NOT NULL DEFAULT '',
    user_query      TEXT NOT NULL DEFAULT '',
    charter_json    TEXT NOT NULL DEFAULT '{}',
    node_results    TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'completed',
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    user_feedback   TEXT DEFAULT '{"type": "none"}',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trajectories_charter_id ON trajectories(charter_id);
CREATE INDEX IF NOT EXISTS idx_trajectories_created_at ON trajectories(created_at);

CREATE TABLE IF NOT EXISTS lc_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trajectory    TEXT NOT NULL,
    submitted_at  TEXT NOT NULL DEFAULT (datetime('now')),
    processed     INTEGER NOT NULL DEFAULT 0,
    processed_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_lc_queue_unprocessed
    ON lc_queue(submitted_at) WHERE processed = 0;
