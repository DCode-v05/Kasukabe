-- Learning signals: inline-captured observations from the main model.
-- Replaces trajectory-based LLM extraction with zero-cost signal capture.

CREATE TABLE IF NOT EXISTS learning_signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    type         TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'user',
    subject      TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL,
    temporal_ref TEXT,
    thread_id      TEXT NOT NULL DEFAULT '',
    processed    INTEGER NOT NULL DEFAULT 0,
    batch_id     TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ls_processed ON learning_signals(processed);
CREATE INDEX IF NOT EXISTS idx_ls_created ON learning_signals(created_at);
CREATE INDEX IF NOT EXISTS idx_ls_type ON learning_signals(type);
CREATE INDEX IF NOT EXISTS idx_ls_thread ON learning_signals(thread_id);
CREATE INDEX IF NOT EXISTS idx_ls_temporal ON learning_signals(temporal_ref);

CREATE VIRTUAL TABLE IF NOT EXISTS learning_signals_fts
USING fts5(subject, content, content=learning_signals, content_rowid=id);

CREATE TRIGGER IF NOT EXISTS ls_fts_insert AFTER INSERT ON learning_signals BEGIN
    INSERT INTO learning_signals_fts(rowid, subject, content)
    VALUES (new.id, new.subject, new.content);
END;

CREATE TRIGGER IF NOT EXISTS ls_fts_delete AFTER DELETE ON learning_signals BEGIN
    INSERT INTO learning_signals_fts(learning_signals_fts, rowid, subject, content)
    VALUES ('delete', old.id, old.subject, old.content);
END;
