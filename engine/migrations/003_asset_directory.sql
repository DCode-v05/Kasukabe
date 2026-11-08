CREATE TABLE IF NOT EXISTS server_registry (
    server_name     TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    description     TEXT,
    transport       TEXT NOT NULL,
    server_url      TEXT,
    server_command  TEXT,
    auth_method     TEXT NOT NULL,
    oauth_config    TEXT,
    auth_config     TEXT,
    category        TEXT,
    vetted          BOOLEAN NOT NULL DEFAULT FALSE,
    vetted_at       TEXT,
    vetted_by       TEXT,
    vet_decision    TEXT,
    vet_reason      TEXT,
    instructions    TEXT,
    action_pins     TEXT,
    provenance      TEXT NOT NULL DEFAULT 'septemberai',
    provenance_rank INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS connections (
    connection_id   TEXT PRIMARY KEY,
    server_name     TEXT NOT NULL REFERENCES server_registry(server_name),
    status          TEXT NOT NULL,
    credentials     BLOB NOT NULL,
    scopes          TEXT,
    expires_at      TEXT,
    refresh_token   BLOB,
    token_issued_at TEXT,
    provenance      TEXT NOT NULL DEFAULT 'user' CHECK (provenance IN ('engine','user','managed')),
    provenance_rank INTEGER NOT NULL DEFAULT 2,
    account_ref     TEXT,
    owner_ref       TEXT,
    idempotency_key TEXT,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    label           TEXT,
    account_label   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_conn_identity_active
    ON connections(server_name, provenance, COALESCE(account_ref, ''))
    WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_conn_idempotency
    ON connections(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS oauth_pkce (
    connection_id  TEXT PRIMARY KEY REFERENCES connections(connection_id),
    code_verifier  BLOB NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS action_registry (
    action_id       TEXT PRIMARY KEY,
    server_name     TEXT NOT NULL REFERENCES server_registry(server_name),
    action_name     TEXT NOT NULL,
    description     TEXT NOT NULL,
    input_schema    TEXT NOT NULL,
    annotations     TEXT DEFAULT '{}',
    tags            TEXT DEFAULT '[]',
    last_refreshed  TEXT NOT NULL DEFAULT (datetime('now')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS execution_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id          TEXT NOT NULL REFERENCES connections(connection_id),
    server_name     TEXT NOT NULL,
    action_name     TEXT NOT NULL,
    params          TEXT,
    successful      BOOLEAN NOT NULL,
    duration_ms     INTEGER NOT NULL,
    thread_id       TEXT NOT NULL,
    error           TEXT,
    account_label   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS connection_permissions (
    connection_id  TEXT NOT NULL REFERENCES connections(connection_id),
    action_name    TEXT NOT NULL,
    behavior       TEXT NOT NULL CHECK (behavior IN ('allow','ask','deny')),
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    locked         BOOLEAN NOT NULL DEFAULT FALSE,
    set_by         TEXT NOT NULL DEFAULT 'user',
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (connection_id, action_name)
);

CREATE INDEX IF NOT EXISTS idx_conn_perms_conn ON connection_permissions(connection_id);

CREATE TABLE IF NOT EXISTS connection_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id  TEXT NOT NULL REFERENCES connections(connection_id),
    server_name    TEXT NOT NULL,
    status         TEXT NOT NULL,
    needs_auth     BOOLEAN NOT NULL DEFAULT FALSE,
    oauth_url      TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conn_events_conn ON connection_events(connection_id);
