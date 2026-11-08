-- Context Collapse persistence — two tables backing
-- src/engine_core/context_collapse.py::ContextCollapseManager.
--
-- context_collapse_snapshot is last-wins: one row per thread_id holding
-- the serialised staged + committed state. save_state() upserts on
-- the thread_id primary key; restore_state() reads the one row.
--
-- context_collapse_commits is append-only: every committed collapse
-- is logged for debugging / replay. Indexed by thread_id so the commit
-- history for a session is cheap to scan.
--
-- Both tables are safe to drop without affecting correctness — the
-- collapse manager treats persistence as best-effort and falls back
-- to in-memory-only state if the DB is missing or read fails.

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
