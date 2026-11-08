-- Microcompact configuration — SQLite-persisted thresholds for the time-based
-- and forced microcompact strategies in src/engine_core/time_based_microcompact.py.
-- Single row (key-value pairs). Loaded once per session via MicrocompactConfig.load().
-- Defaults mirror the src reference (tengu_slate_heron feature flag):
--   enabled=true, gap_threshold_minutes=60, keep_recent=5.

CREATE TABLE IF NOT EXISTS microcompact_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO microcompact_config (key, value) VALUES ('enabled', 'true');
INSERT OR IGNORE INTO microcompact_config (key, value) VALUES ('gap_threshold_minutes', '60.0');
INSERT OR IGNORE INTO microcompact_config (key, value) VALUES ('keep_recent', '5');
