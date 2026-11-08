CREATE TABLE IF NOT EXISTS obs_events (
    id               INTEGER PRIMARY KEY,
    timestamp        REAL NOT NULL,
    kind             TEXT NOT NULL,
    payload          TEXT NOT NULL,
    thread_id          TEXT,
    agent_step_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_events_timestamp ON obs_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_obs_events_kind      ON obs_events(kind);
CREATE INDEX IF NOT EXISTS idx_obs_events_thread      ON obs_events(thread_id);

CREATE TABLE IF NOT EXISTS obs_counters_daily (
    day      TEXT NOT NULL,
    metric   TEXT NOT NULL,
    model    TEXT NOT NULL,
    value    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, metric, model)
);

CREATE TABLE IF NOT EXISTS gw_cache_state (
    day           TEXT NOT NULL,
    model         TEXT NOT NULL,
    cache_hits    INTEGER NOT NULL DEFAULT 0,
    cache_reads   INTEGER NOT NULL DEFAULT 0,
    fresh_input   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, model)
);

CREATE TABLE IF NOT EXISTS gw_config_snapshot (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    applied_at   REAL NOT NULL,
    yaml_blob    TEXT NOT NULL,
    applied_by   TEXT NOT NULL
);
