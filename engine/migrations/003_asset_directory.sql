-- Asset Directory: connected MCP servers, connections, action cache, execution log,
-- and per-tool permissions. Single-user engine — no user_id columns.
--
-- Connection authority ladder (higher supersedes / disqualifies lower):
--   engine(1) < user(2) < managed(3)
-- `septemberai` is NOT a rung in that ladder — it is a separate SOURCE tier kept
-- beside it: September's own first-party hosted servers, fetched from the remote
-- September registry (dormant for now). Third-party connectors (browse + OAuth
-- brokering) live in the PRODUCT layer, not here. server_registry holds only servers
-- the engine is actually connected to or has registered; connection_permissions holds
-- per-tool allow/ask/deny set from the product and enforced here.

CREATE TABLE IF NOT EXISTS server_registry (
    server_name     TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    description     TEXT,
    transport       TEXT NOT NULL,
    server_url      TEXT,
    server_command  TEXT,
    auth_method     TEXT NOT NULL,
    oauth_config    TEXT,   -- how to OBTAIN a token (OAuth flow); secret fields sealed at rest
    auth_config     TEXT,   -- how to PRESENT the credential (api_key: {"api_key":{"in":"header"|"query","name":...}}); NULL = Authorization: Bearer
    category        TEXT,
    -- Trust gate for execute() and auto_auth. Self-registration can never set
    -- this: the registering caller is not allowed to certify its own server,
    -- so both self-serve writers insert FALSE and only POST /assets/servers/
    -- {name}/vet flips it. The managed tier is exempt (upsert_managed_server
    -- sets TRUE) because that path is the already-authenticated control plane.
    vetted          BOOLEAN NOT NULL DEFAULT FALSE,
    vetted_at       TEXT,   -- when the gate was last decided (NULL while never reviewed)
    vetted_by       TEXT,   -- who decided, for the audit trail
    -- "Reviewed and rejected" and "never reviewed" both left vetted=FALSE with
    -- NULL vetted_at, so a rejected server was indistinguishable from a brand
    -- new one and resurfaced in the pending queue forever with no durable
    -- record that anyone had said no.
    vet_decision    TEXT,   -- NULL = never reviewed | 'approved' | 'rejected'
    vet_reason      TEXT,
    -- The server's own usage guidance from the MCP initialize handshake: call
    -- ordering, required scopes, rate limits. It was read off the wire and
    -- dropped, so the model only ever saw per-tool descriptions with no
    -- server-level operating context. Capped at ingestion like every other
    -- server-controlled string.
    instructions    TEXT,
    action_pins     TEXT,
    provenance      TEXT NOT NULL DEFAULT 'septemberai',  -- origin/source of the definition (septemberai|engine|user|managed)
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
    token_issued_at TEXT,   -- when the current token was minted (proactive-refresh timing); NULL falls back to created_at
    -- Provenance + multi-account identity: the same server may hold several ACTIVE
    -- connections across (provenance, account_ref) at once (e.g. a personal `user`
    -- Figma login and a company `managed` Figma login coexisting).
    provenance      TEXT NOT NULL DEFAULT 'user' CHECK (provenance IN ('engine','user','managed')),
    provenance_rank INTEGER NOT NULL DEFAULT 2,   -- engine=1 < user=2 < managed=3
    account_ref     TEXT,   -- distinguishes multiple accounts of the same provider (NULL = default/self)
    owner_ref       TEXT,   -- managed: opaque org/seat id the product supplies (NULL = self)
    idempotency_key TEXT,   -- product-supplied stable key for managed provisioning (re-provision returns same row)
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,   -- per-asset pause: false keeps the connection ACTIVE + credentialed (NOT revoked) but excludes it from use
    label           TEXT,   -- product-set human name for this connection (e.g. "Work Gmail")
    account_label   TEXT,   -- human identifier for the account (e.g. the email or handle)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- At most one ACTIVE connection per (server, provenance, account); different
-- provenance/account for the same server coexist by design.
CREATE UNIQUE INDEX IF NOT EXISTS idx_conn_identity_active
    ON connections(server_name, provenance, COALESCE(account_ref, ''))
    WHERE status = 'ACTIVE';

-- Idempotency lookup for managed re-provisioning (re-provision returns the same row).
CREATE INDEX IF NOT EXISTS idx_conn_idempotency
    ON connections(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- PKCE code_verifier persistence. Single-use and encrypted at rest (same
-- CredentialStore as tokens), so an OAuth flow survives a worker restart and
-- works across workers; get_pkce_verifier consumes (deletes) it. Abandoned
-- flows are swept opportunistically after 15 minutes.
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
    -- Which account ran it. The same server holds several ACTIVE connections at
    -- once (personal + work Gmail), so a row naming only server + action cannot
    -- answer "which mailbox sent that": connection_id identifies it but nothing
    -- human-readable did, and the connection may be gone by audit time.
    account_label   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per-tool permissions live in the engine DB (replacing the in-memory tier_overrides).
-- 3-state, set from the product UI, enforced in the engine. `locked` = managed policy
-- the user cannot loosen. action_name '*' is the server-wide default.
CREATE TABLE IF NOT EXISTS connection_permissions (
    connection_id  TEXT NOT NULL REFERENCES connections(connection_id),
    action_name    TEXT NOT NULL,
    behavior       TEXT NOT NULL CHECK (behavior IN ('allow','ask','deny')),
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,   -- per-tool on/off: false = the tool is not exposed to the agent at all (distinct from behavior 'deny')
    locked         BOOLEAN NOT NULL DEFAULT FALSE,
    set_by         TEXT NOT NULL DEFAULT 'user',   -- user | managed | engine-default
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (connection_id, action_name)
);

CREATE INDEX IF NOT EXISTS idx_conn_perms_conn ON connection_permissions(connection_id);

-- Out-of-run connection state-change log: the durable pull complement to the
-- in-run asset_status block. Appended on refresh / needs_auth / revoke / pause /
-- circuit-open / expiry so the product can reflect background changes without
-- polling each connection.
-- The product reads it with GET /assets/connection-events?since=<event_id>.
CREATE TABLE IF NOT EXISTS connection_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id  TEXT NOT NULL REFERENCES connections(connection_id),
    server_name    TEXT NOT NULL,
    status         TEXT NOT NULL,   -- needs_auth | refreshed | revoked | paused | resumed | circuit_open | expired
    needs_auth     BOOLEAN NOT NULL DEFAULT FALSE,
    oauth_url      TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conn_events_conn ON connection_events(connection_id);
