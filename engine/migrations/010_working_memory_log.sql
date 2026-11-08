CREATE TABLE IF NOT EXISTS working_memory_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id      TEXT    NOT NULL,
    entry_type   TEXT    NOT NULL,
    content      TEXT    NOT NULL,
    sub_agent_id TEXT    DEFAULT '',
    timestamp    TEXT    NOT NULL,
    session_id   TEXT    NOT NULL,
    created_at   REAL    DEFAULT (unixepoch('now'))
);

CREATE INDEX IF NOT EXISTS idx_wm_thread    ON working_memory_log(thread_id);
CREATE INDEX IF NOT EXISTS idx_wm_session ON working_memory_log(session_id);
CREATE INDEX IF NOT EXISTS idx_wm_type    ON working_memory_log(thread_id, entry_type);

CREATE VIRTUAL TABLE IF NOT EXISTS working_memory_fts USING fts5(
    content, content=working_memory_log, content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS wm_fts_insert AFTER INSERT ON working_memory_log BEGIN
    INSERT INTO working_memory_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS wm_fts_delete AFTER DELETE ON working_memory_log BEGIN
    INSERT INTO working_memory_fts(working_memory_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
END;
