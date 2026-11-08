CREATE TABLE IF NOT EXISTS user_agents (
    id                      TEXT PRIMARY KEY,
    name                    TEXT         NOT NULL,
    source                  TEXT         NOT NULL DEFAULT 'platform',
    platform_agent_name     TEXT,
    platform_agent_version  TEXT,
    learned_state           TEXT,
    custom_config           TEXT,
    type                    TEXT,
    capabilities            TEXT,
    input_signature         TEXT,
    output_signature        TEXT,
    declared_deps           TEXT,
    description             TEXT,
    created_at              TEXT         NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT         NOT NULL DEFAULT (datetime('now')),
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS user_skills (
    id                       TEXT PRIMARY KEY,
    name                     TEXT          NOT NULL,
    source                   TEXT          NOT NULL DEFAULT 'platform',
    platform_skill_name      TEXT,
    platform_skill_version   TEXT,
    learned_state            TEXT,
    status                   TEXT          NOT NULL DEFAULT 'tentative',
    confidence               REAL          NOT NULL DEFAULT 0.5,
    occurrence_count         INTEGER       NOT NULL DEFAULT 0,
    correction_count         INTEGER       NOT NULL DEFAULT 0,
    gotchas_count            INTEGER       NOT NULL DEFAULT 0,
    gotchas                  TEXT,
    forked_from              TEXT,
    last_used                TEXT,
    created_at               TEXT          NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT          NOT NULL DEFAULT (datetime('now')),
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS user_tools (
    id                      TEXT PRIMARY KEY,
    platform_tool_name      TEXT         NOT NULL,
    platform_tool_version   TEXT         NOT NULL,
    custom_config           TEXT,
    created_at              TEXT         NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform_tool_name)
);
