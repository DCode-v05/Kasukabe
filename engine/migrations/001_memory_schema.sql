CREATE TABLE IF NOT EXISTS episodes (
    id                  TEXT PRIMARY KEY,
    key                 TEXT         NOT NULL,
    description         TEXT         NOT NULL,
    value               TEXT,
    value_compressed    BLOB,
    tier                TEXT         NOT NULL DEFAULT 'episode',
    created_at          TEXT         NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_episodes_tier ON episodes(tier);
CREATE INDEX IF NOT EXISTS idx_episodes_created_at ON episodes(created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS episodes_vec USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[1536]
);

CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    key,
    description,
    id UNINDEXED
);

CREATE TABLE IF NOT EXISTS knowledge_store (
    id                  TEXT PRIMARY KEY,
    subject             TEXT            NOT NULL,
    content             TEXT            NOT NULL,
    type                TEXT            NOT NULL DEFAULT 'fact',
    tags                TEXT            NOT NULL DEFAULT '[]',
    confidence          REAL            NOT NULL DEFAULT 1.0,
    source              TEXT            NOT NULL DEFAULT 'user_stated',
    usage_count         INTEGER         NOT NULL DEFAULT 0,
    created_at          TEXT            NOT NULL DEFAULT (datetime('now')),
    last_reinforced     TEXT,
    valid_from          TEXT            NOT NULL DEFAULT (datetime('now')),
    valid_until         TEXT,
    recorded_at         TEXT            NOT NULL DEFAULT (datetime('now')),
    superseded_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_ks_type ON knowledge_store(type);
CREATE INDEX IF NOT EXISTS idx_ks_confidence ON knowledge_store(confidence);
CREATE INDEX IF NOT EXISTS idx_ks_active ON knowledge_store(id)
    WHERE superseded_at IS NULL;

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_store_vec USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[1536]
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_store_fts USING fts5(
    subject,
    content,
    id UNINDEXED
);

CREATE TABLE IF NOT EXISTS social_graph_nodes (
    id              TEXT PRIMARY KEY,
    name            TEXT         NOT NULL,
    is_primary      INTEGER      NOT NULL DEFAULT 0,
    created_at      TEXT         NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT         NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sgn_primary ON social_graph_nodes(is_primary)
    WHERE is_primary = 1;

CREATE VIRTUAL TABLE IF NOT EXISTS social_graph_nodes_vec USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[1536]
);

CREATE VIRTUAL TABLE IF NOT EXISTS social_graph_nodes_fts USING fts5(
    name,
    id UNINDEXED
);

CREATE TABLE IF NOT EXISTS social_graph_node_attributes (
    id              TEXT PRIMARY KEY,
    node_id         TEXT         NOT NULL REFERENCES social_graph_nodes(id),
    attribute_key   TEXT         NOT NULL,
    attribute_value TEXT         NOT NULL,
    updated_at      TEXT         NOT NULL DEFAULT (datetime('now')),
    UNIQUE (node_id, attribute_key)
);

CREATE INDEX IF NOT EXISTS idx_sgna_node_id ON social_graph_node_attributes(node_id);

CREATE VIRTUAL TABLE IF NOT EXISTS social_graph_node_attributes_vec USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[1536]
);

CREATE TABLE IF NOT EXISTS social_graph_edges (
    id                  TEXT PRIMARY KEY,
    person_a_id         TEXT         NOT NULL REFERENCES social_graph_nodes(id),
    person_b_id         TEXT         NOT NULL REFERENCES social_graph_nodes(id),
    user_perspective    TEXT,
    engine_inference    TEXT,
    observed_dynamics   TEXT,
    contexts            TEXT         NOT NULL DEFAULT '[]',
    created_at          TEXT         NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT         NOT NULL DEFAULT (datetime('now')),
    UNIQUE (person_a_id, person_b_id)
);

CREATE INDEX IF NOT EXISTS idx_sge_person_a ON social_graph_edges(person_a_id);
CREATE INDEX IF NOT EXISTS idx_sge_person_b ON social_graph_edges(person_b_id);

CREATE VIRTUAL TABLE IF NOT EXISTS social_graph_edges_vec USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[1536]
);

CREATE TABLE IF NOT EXISTS evolved_soul (
    id              TEXT PRIMARY KEY,
    trait           TEXT            NOT NULL,
    value           TEXT            NOT NULL,
    status          TEXT            NOT NULL DEFAULT 'signal',
    confidence      REAL            NOT NULL DEFAULT 0.00,
    evidence_count  INTEGER         NOT NULL DEFAULT 0,
    last_reinforced TEXT,
    created_at      TEXT            NOT NULL DEFAULT (datetime('now')),
    UNIQUE (trait)
);

CREATE INDEX IF NOT EXISTS idx_es_expressed ON evolved_soul(status)
    WHERE status = 'expressed';
