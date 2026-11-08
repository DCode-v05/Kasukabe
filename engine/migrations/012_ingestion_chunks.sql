CREATE TABLE IF NOT EXISTS ingestion_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id         TEXT NOT NULL,
    content_id      TEXT NOT NULL,
    title           TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'upload',
    chunk_index     INTEGER NOT NULL,
    total_chunks    INTEGER NOT NULL,
    content         TEXT NOT NULL,
    size_tokens     INTEGER NOT NULL DEFAULT 0,
    mime_type       TEXT NOT NULL DEFAULT 'text/plain',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL,
    UNIQUE(thread_id, content_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_thread ON ingestion_chunks(thread_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_content ON ingestion_chunks(thread_id, content_id);
