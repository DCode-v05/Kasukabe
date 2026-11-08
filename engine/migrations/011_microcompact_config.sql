CREATE TABLE IF NOT EXISTS microcompact_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO microcompact_config (key, value) VALUES ('enabled', 'true');
INSERT OR IGNORE INTO microcompact_config (key, value) VALUES ('gap_threshold_minutes', '60.0');
INSERT OR IGNORE INTO microcompact_config (key, value) VALUES ('keep_recent', '5');
