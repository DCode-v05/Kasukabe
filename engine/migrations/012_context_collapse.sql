CREATE TABLE IF NOT EXISTS context_collapse_snapshot (
    thread_id       TEXT PRIMARY KEY,
    snapshot_json TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS context_collapse_commits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id         TEXT NOT NULL,
    collapse_id     TEXT NOT NULL,
    start_fp        TEXT NOT NULL,
    end_fp          TEXT NOT NULL,
    summary         TEXT NOT NULL,
    original_tokens INTEGER NOT NULL,
    summary_tokens  INTEGER NOT NULL,
    committed_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_context_collapse_commits_thread
    ON context_collapse_commits(thread_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_context_collapse_commits_collapse_id
    ON context_collapse_commits(thread_id, collapse_id);
