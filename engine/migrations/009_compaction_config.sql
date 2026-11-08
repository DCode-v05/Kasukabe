CREATE TABLE IF NOT EXISTS compaction_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO compaction_config (key, value) VALUES ('min_tokens', '10000');
INSERT OR IGNORE INTO compaction_config (key, value) VALUES ('min_text_block_messages', '5');
INSERT OR IGNORE INTO compaction_config (key, value) VALUES ('max_tokens', '40000');
INSERT OR IGNORE INTO compaction_config (key, value) VALUES ('compaction_threshold', '0.80');
INSERT OR IGNORE INTO compaction_config (key, value) VALUES ('circuit_breaker_max_failures', '3');
