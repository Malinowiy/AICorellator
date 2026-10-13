-- ============================================================
-- AICorellator — SQLite schema
-- Phase 2: capabilities, chain groups
-- ============================================================
-- Conventions:
--   - Timestamps are SQLite-style UTC: 'YYYY-MM-DD HH:MM:SS'.
--     Lexicographically sortable. Not strict ISO 8601 (no 'Z').
--     Python models produce the same format via strftime().
--   - JSON columns are TEXT with a JSON validity CHECK
--     (requires SQLite 3.38+).
--   - confidence is REAL in [0.0, 1.0].
--   - capabilities is a JSON array of capability strings
--     (see ADR-0009).
--   - host_id references hosts(id). In Phase 0, only 'localhost'.
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ------------------------------------------------------------
-- schema_version — migration tracking (forward-only)
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT NOT NULL
);

-- ------------------------------------------------------------
-- hosts — one row per scanned host (Phase 0: localhost only)
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hosts (
    id              TEXT PRIMARY KEY,
    hostname        TEXT NOT NULL,
    ip              TEXT,
    platform        TEXT NOT NULL,
    agent_version   TEXT,
    last_heartbeat  TIMESTAMP,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('active', 'unreachable', 'stale')),
    CHECK (platform IN ('swarm', 'k8s', 'standalone'))
);

CREATE INDEX IF NOT EXISTS idx_hosts_status ON hosts(status);
CREATE INDEX IF NOT EXISTS idx_hosts_platform ON hosts(platform);

INSERT OR IGNORE INTO hosts (id, hostname, platform, status)
VALUES ('localhost', 'localhost', 'standalone', 'active');

-- ------------------------------------------------------------
-- nodes — graph vertices
-- ------------------------------------------------------------
-- Canonical ID is KIND-SPECIFIC (see C4_L3_Phase1.md):
--   agent:        sha256(file_path + class_name)
--   llm_endpoint: sha256(provider + base_url + model)
--   mcp_server:   sha256(file_path)
--   tool:         sha256(mcp_server_id + tool_name)
--   credential:   sha256(source_file + var_name)
--   sink:         sha256(call_target + file_path)
--   entry_point:  sha256(file_path + route_path)
--
-- capabilities (ADR-0009): JSON array of capability strings
-- derived from AST calls, not from names. Examples:
--   executes_code, executes_shell, reads_filesystem,
--   writes_filesystem, reads_db, writes_db, http_egress,
--   http_ingress, reads_env, reads_secrets, calls_llm,
--   invokes_mcp_tool, reads_untrusted
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nodes (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    name         TEXT NOT NULL,
    host_id      TEXT NOT NULL,
    attributes   JSON NOT NULL DEFAULT '{}',
    capabilities JSON NOT NULL DEFAULT '[]',
    confidence   REAL NOT NULL DEFAULT 1.0,
    content_hash TEXT,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (host_id) REFERENCES hosts(id) ON DELETE CASCADE,

    CHECK (kind IN (
        'agent', 'llm_endpoint', 'mcp_server', 'tool',
        'credential', 'sink', 'entry_point', 'guardrail'
    )),
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (status IN ('active', 'stale')),
    CHECK (json_valid(attributes)),
    CHECK (json_valid(capabilities))
);

CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_host ON nodes(host_id);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_content_hash ON nodes(content_hash);

-- ------------------------------------------------------------
-- edges — graph relations (stored kinds only)
-- ------------------------------------------------------------
-- Stored:  uses_model, uses_tool, provides_tool, delegates_to, has_access_to, guards
-- Derived: reaches_sink, called_by — computed by Query Layer.
--
-- capabilities (ADR-0009): JSON array of edge-level capabilities:
--   data_flows_forward, control_flows_forward, untrusted_propagates
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS edges (
    id           TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    host_id      TEXT NOT NULL,
    precision    TEXT NOT NULL DEFAULT 'syntactic',
    attributes   JSON NOT NULL DEFAULT '{}',
    capabilities JSON NOT NULL DEFAULT '[]',
    confidence   REAL NOT NULL DEFAULT 1.0,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (source_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (host_id)   REFERENCES hosts(id) ON DELETE CASCADE,

    CHECK (kind IN (
        'uses_model', 'uses_tool', 'provides_tool', 'delegates_to', 'has_access_to', 'guards'
    )),
    CHECK (precision IN ('syntactic', 'compiler-verified')),
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (status IN ('active', 'stale')),
    CHECK (json_valid(attributes)),
    CHECK (json_valid(capabilities)),
    CHECK (source_id <> target_id)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_status ON edges(status);
CREATE INDEX IF NOT EXISTS idx_edges_host ON edges(host_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique
    ON edges(source_id, target_id, kind, host_id);

-- ------------------------------------------------------------
-- findings — scanner-reported weaknesses
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS findings (
    id           TEXT PRIMARY KEY,
    node_id      TEXT,
    edge_id      TEXT,
    scanner      TEXT NOT NULL,
    rule_id      TEXT,
    severity     TEXT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT,
    evidence     JSON NOT NULL DEFAULT '{}',
    confidence   REAL NOT NULL DEFAULT 1.0,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (edge_id) REFERENCES edges(id) ON DELETE CASCADE,

    CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    CHECK (status IN ('active', 'stale')),
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (json_valid(evidence)),
    CHECK (
        (node_id IS NOT NULL AND edge_id IS NULL) OR
        (node_id IS NULL AND edge_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_findings_node ON findings(node_id);
CREATE INDEX IF NOT EXISTS idx_findings_edge ON findings(edge_id);
CREATE INDEX IF NOT EXISTS idx_findings_scanner ON findings(scanner);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_rule ON findings(rule_id);
CREATE INDEX IF NOT EXISTS idx_findings_updated ON findings(updated_at);

-- ------------------------------------------------------------
-- chains — correlator-evaluated compromise chains
-- ------------------------------------------------------------
-- A chain is an evaluated compromise path (ADR-0005, ADR-0006).
-- Candidate chains are NOT stored — only evaluated ones.
--
-- No foreign keys on chain_nodes / chain_edges. Validation is
-- Layer 3 app logic. Chains with validated = 0 may reference
-- non-existent nodes/edges — kept for provenance, not for reports.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chains (
    id              TEXT PRIMARY KEY,
    chain_nodes     JSON NOT NULL,
    chain_edges     JSON NOT NULL,
    verdict         TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 0.0,
    reasoning       TEXT,
    voter_outputs   JSON NOT NULL DEFAULT '[]',
    prompt_version  TEXT NOT NULL,
    validated       INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (verdict IN ('exploitable', 'not_exploitable', 'uncertain')),
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (validated IN (0, 1)),
    CHECK (status IN ('active', 'stale')),
    CHECK (json_valid(chain_nodes)),
    CHECK (json_valid(chain_edges)),
    CHECK (json_valid(voter_outputs))
);

CREATE INDEX IF NOT EXISTS idx_chains_verdict ON chains(verdict);
CREATE INDEX IF NOT EXISTS idx_chains_status ON chains(status);
CREATE INDEX IF NOT EXISTS idx_chains_confidence ON chains(confidence);
CREATE INDEX IF NOT EXISTS idx_chains_validated ON chains(validated);
CREATE INDEX IF NOT EXISTS idx_chains_prompt_version ON chains(prompt_version);

-- ------------------------------------------------------------
-- chain_groups — multi-chain attack groups (ADR-0010, ADR-0012)
-- ------------------------------------------------------------
-- A chain group is a set of chains that meet through an
-- untrusted_source. Dangerous when one chain WRITES to the
-- source and another READS from it, reaching a sink.
--
-- write_chain_ids / read_chain_ids: JSON arrays of chain IDs.
-- No FKs — chains may be re-evaluated between runs.
--
-- score / score_components: from ADR-0012 (deterministic
-- prioritization). score_components is a JSON object with the
-- breakdown of contributing signals.
--
-- lethal_trifecta: 1 if the reading side passes through a node
-- with reads_untrusted + reads_secrets + egress capability.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chain_groups (
    id                  TEXT PRIMARY KEY,
    name                TEXT,
    untrusted_source_id TEXT NOT NULL,
    write_chain_ids     JSON NOT NULL DEFAULT '[]',
    read_chain_ids      JSON NOT NULL DEFAULT '[]',
    lethal_trifecta     INTEGER NOT NULL DEFAULT 0,
    score               REAL NOT NULL DEFAULT 0.0,
    score_components    JSON NOT NULL DEFAULT '{}',
    verdict             TEXT,
    confidence          REAL NOT NULL DEFAULT 0.0,
    reasoning           TEXT,
    validated           INTEGER NOT NULL DEFAULT 0,
    priority            TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (untrusted_source_id) REFERENCES nodes(id) ON DELETE CASCADE,

    CHECK (lethal_trifecta IN (0, 1)),
    CHECK (validated IN (0, 1)),
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (verdict IS NULL OR verdict IN (
        'exploitable', 'not_exploitable', 'uncertain'
    )),
    CHECK (priority IS NULL OR priority IN ('low', 'medium', 'high')),
    CHECK (status IN ('active', 'stale')),
    CHECK (json_valid(write_chain_ids)),
    CHECK (json_valid(read_chain_ids)),
    CHECK (json_valid(score_components))
);

CREATE INDEX IF NOT EXISTS idx_chain_groups_source
    ON chain_groups(untrusted_source_id);
CREATE INDEX IF NOT EXISTS idx_chain_groups_score
    ON chain_groups(score DESC);
CREATE INDEX IF NOT EXISTS idx_chain_groups_verdict
    ON chain_groups(verdict);
CREATE INDEX IF NOT EXISTS idx_chain_groups_status
    ON chain_groups(status);

-- ------------------------------------------------------------
-- state — per-host pipeline flags
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS state (
    host_id     TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (host_id, key),
    FOREIGN KEY (host_id) REFERENCES hosts(id) ON DELETE CASCADE,

    CHECK (key IN (
        'phase1_done',
        'scan_done',
        'report_fresh',
        'last_report_at',
        'dynamic_enabled',
        'cleanup_stale_days',
        'cleanup_findings_days',
        'cleanup_chains_days',
        'cleanup_reports_days'
    ))
);

CREATE INDEX IF NOT EXISTS idx_state_key ON state(key);

INSERT OR IGNORE INTO state (host_id, key, value) VALUES
    ('localhost', 'phase1_done',           'false'),
    ('localhost', 'scan_done',             'false'),
    ('localhost', 'report_fresh',          'false'),
    ('localhost', 'last_report_at',         NULL),
    ('localhost', 'dynamic_enabled',       'false'),
    ('localhost', 'cleanup_stale_days',    '30'),
    ('localhost', 'cleanup_findings_days', '90'),
    ('localhost', 'cleanup_chains_days',   '90'),
    ('localhost', 'cleanup_reports_days',  '365');

-- ------------------------------------------------------------
-- task_state — queue counters, per host per kind
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS task_state (
    host_id            TEXT NOT NULL,
    kind               TEXT NOT NULL,
    active_count       INTEGER NOT NULL DEFAULT 0,
    done_count         INTEGER NOT NULL DEFAULT 0,
    failed_count       INTEGER NOT NULL DEFAULT 0,
    timeout_count      INTEGER NOT NULL DEFAULT 0,
    last_completed_at  TIMESTAMP,
    updated_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (host_id, kind),
    FOREIGN KEY (host_id) REFERENCES hosts(id) ON DELETE CASCADE,

    CHECK (kind IN ('static', 'dynamic')),
    CHECK (active_count  >= 0),
    CHECK (done_count    >= 0),
    CHECK (failed_count  >= 0),
    CHECK (timeout_count >= 0)
);

INSERT OR IGNORE INTO task_state (host_id, kind) VALUES
    ('localhost', 'static'),
    ('localhost', 'dynamic');

-- ------------------------------------------------------------
-- reports — metadata for generated report bundles
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS reports (
    id           TEXT PRIMARY KEY,
    host_id      TEXT NOT NULL,
    bundle_dir   TEXT NOT NULL,
    tier         TEXT NOT NULL,
    is_current   INTEGER NOT NULL DEFAULT 0,
    generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata     JSON NOT NULL DEFAULT '{}',

    FOREIGN KEY (host_id) REFERENCES hosts(id) ON DELETE CASCADE,

    CHECK (tier IN ('fast', 'full')),
    CHECK (is_current IN (0, 1)),
    CHECK (json_valid(metadata))
);

CREATE INDEX IF NOT EXISTS idx_reports_host ON reports(host_id);
CREATE INDEX IF NOT EXISTS idx_reports_tier ON reports(tier);
CREATE INDEX IF NOT EXISTS idx_reports_generated ON reports(generated_at);
CREATE INDEX IF NOT EXISTS idx_reports_bundle ON reports(bundle_dir);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_current
    ON reports(host_id) WHERE is_current = 1;

-- ------------------------------------------------------------
-- Triggers — keep updated_at fresh
-- ------------------------------------------------------------

CREATE TRIGGER IF NOT EXISTS trg_nodes_updated_at
AFTER UPDATE ON nodes
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE nodes SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_edges_updated_at
AFTER UPDATE ON edges
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE edges SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_findings_updated_at
AFTER UPDATE ON findings
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE findings SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_chains_updated_at
AFTER UPDATE ON chains
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE chains SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_chain_groups_updated_at
AFTER UPDATE ON chain_groups
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE chain_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- ------------------------------------------------------------
-- Seed schema_version
-- ------------------------------------------------------------

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (1, 'Phase 0: initial schema');

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (2, 'Phase 2: capabilities + chain_groups');

-- ============================================================
-- End of schema
-- ============================================================