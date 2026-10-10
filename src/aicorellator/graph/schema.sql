-- ============================================================
-- AICorellator — SQLite schema
-- Phase 0: Data Model
-- ============================================================
-- Conventions:
--   - Timestamps are SQLite-style UTC: 'YYYY-MM-DD HH:MM:SS'.
--     Lexicographically sortable. Not strict ISO 8601 (no 'Z').
--     Python models produce the same format via strftime().
--   - JSON columns are TEXT with a JSON validity CHECK
--     (requires SQLite 3.38+).
--   - confidence is REAL in [0.0, 1.0].
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
    id              TEXT PRIMARY KEY,          -- 'localhost' in Phase 0
    hostname        TEXT NOT NULL,
    ip              TEXT,
    platform        TEXT NOT NULL,             -- swarm | k8s | standalone
    agent_version   TEXT,
    last_heartbeat  TIMESTAMP,
    status          TEXT NOT NULL DEFAULT 'active',
                                               -- active | unreachable | stale
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('active', 'unreachable', 'stale')),
    CHECK (platform IN ('swarm', 'k8s', 'standalone'))
);

CREATE INDEX IF NOT EXISTS idx_hosts_status ON hosts(status);
CREATE INDEX IF NOT EXISTS idx_hosts_platform ON hosts(platform);

-- Bootstrap: Phase 0 always has exactly one host.
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
-- All derivations must include host_id in the hash input so that
-- two hosts with identical code produce distinct nodes.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nodes (
    id           TEXT PRIMARY KEY,             -- kind-specific sha256, includes host_id
    kind         TEXT NOT NULL,
    name         TEXT NOT NULL,
    host_id      TEXT NOT NULL,
    attributes   JSON NOT NULL DEFAULT '{}',
    confidence   REAL NOT NULL DEFAULT 1.0,
    content_hash TEXT,                         -- for re-run caching
    status       TEXT NOT NULL DEFAULT 'active',
                                               -- active | stale
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (host_id) REFERENCES hosts(id) ON DELETE CASCADE,

    CHECK (kind IN (
        'agent', 'llm_endpoint', 'mcp_server', 'tool',
        'credential', 'sink', 'entry_point', 'guardrail'
    )),
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (status IN ('active', 'stale')),
    CHECK (json_valid(attributes))
);

CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_host ON nodes(host_id);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_content_hash ON nodes(content_hash);
-- NOTE: idx_nodes_confidence is intentionally absent. confidence has
-- very low cardinality (defaults to 1.0) and SQLite's query planner
-- ignores it. If a query needs it, add it via a migration.

-- ------------------------------------------------------------
-- edges — graph relations (stored kinds only)
-- ------------------------------------------------------------
-- Stored:  uses_model, provides_tool, delegates_to, has_access_to
-- Derived: reaches_sink, called_by — computed by Query Layer,
--          NOT stored. See C4_L3_Phase0.md.
--
-- precision (ADR-002):
--   'syntactic'         — from TSA (tree-sitter-analyzer)
--   'compiler-verified' — from SCIP indexer
--
-- host_id semantics:
--   host_id is the "local" host for this edge — the host where the
--   relationship was observed. For same-host edges, host_id matches
--   source_id.host_id and target_id.host_id. For CROSS-HOST edges
--   (e.g., agent on host A calls LLM on host B), host_id may differ
--   from either endpoint's host_id. Such edges are allowed and must
--   be explicit — there is no CHECK enforcing equality.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS edges (
    id           TEXT PRIMARY KEY,             -- sha256(source + target + kind + host_id)
    source_id    TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    host_id      TEXT NOT NULL,
    precision    TEXT NOT NULL DEFAULT 'syntactic',
    attributes   JSON NOT NULL DEFAULT '{}',
    confidence   REAL NOT NULL DEFAULT 1.0,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (source_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (host_id)   REFERENCES hosts(id) ON DELETE CASCADE,

    CHECK (kind IN (
        'uses_model', 'provides_tool', 'delegates_to', 'has_access_to', 'guards'
    )),
    CHECK (precision IN ('syntactic', 'compiler-verified')),
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (status IN ('active', 'stale')),
    CHECK (json_valid(attributes)),
    CHECK (source_id <> target_id)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_status ON edges(status);
CREATE INDEX IF NOT EXISTS idx_edges_host ON edges(host_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique
    ON edges(source_id, target_id, kind, host_id);
-- NOTE: idx_edges_precision is intentionally absent. precision has
-- two possible values and SQLite ignores low-cardinality indexes.

-- ------------------------------------------------------------
-- findings — scanner-reported weaknesses
-- ------------------------------------------------------------
-- A finding is a weakness reported by a scanner (AAK, LLM-scanner,
-- MCP-scanner) attached to EITHER a node OR an edge — never both,
-- never neither. The CHECK enforces strict exclusivity.
--
-- updated_at is used by the cleanup policy (see C4_L3_Phase0.md:
-- "Delete findings with status = 'stale' older than N days").
-- It is maintained by trg_findings_updated_at.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS findings (
    id           TEXT PRIMARY KEY,
    node_id      TEXT,
    edge_id      TEXT,
    scanner      TEXT NOT NULL,                -- aak | llm-scanner | mcp-scanner
    rule_id      TEXT,                         -- e.g. 'AAK-LLM-SQL-RCE-001'
    severity     TEXT NOT NULL,                -- critical | high | medium | low
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
-- A chain is an evaluated compromise path produced by the
-- correlator (ADR-0005, ADR-0006). Candidate chains are NOT
-- stored — only evaluated ones.
--
-- NO FOREIGN KEYS on chain_nodes / chain_edges.
-- Validation is Layer 3 app logic (ADR-0006). Chains with
-- validated = 0 may reference non-existent nodes/edges — these
-- are hallucinated chains kept for provenance, not for reports.
--
-- NOTE: chain_nodes and chain_edges are JSON arrays of IDs.
-- No FK validation is possible on JSON contents. After a CASCADE
-- delete of a referenced node/edge, these arrays may contain
-- dangling IDs. Validation happens at query time (see QueryLayer)
-- or via periodic integrity checks.
--
-- validated:
--   0 — rejected as hallucination (Layer 3, ADR-0006)
--   1 — all edges confirmed to exist in the edges table
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chains (
    id              TEXT PRIMARY KEY,          -- chain_id from Layer 1 (ADR-0006)
    chain_nodes     JSON NOT NULL,             -- ordered list of node IDs
    chain_edges     JSON NOT NULL,             -- ordered list of edge IDs
    verdict         TEXT NOT NULL,             -- exploitable | not_exploitable | uncertain
    confidence      REAL NOT NULL DEFAULT 0.0,
    reasoning       TEXT,
    voter_outputs   JSON NOT NULL DEFAULT '[]',-- raw voter responses (ADR-0005)
    prompt_version  TEXT NOT NULL,             -- prompt version used (ADR-0005)
    validated       INTEGER NOT NULL DEFAULT 0,-- 0 | 1 (Layer 3 result, ADR-0006)
    status          TEXT NOT NULL DEFAULT 'active',
                                               -- active | stale
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
-- state — per-host pipeline flags
-- ------------------------------------------------------------
-- Primary key is (host_id, key). Global flags are DERIVED by
-- query, not stored. See C4_L3_Phase0.md.
--
-- CHECK on key enforces the documented key set. Adding a new key
-- requires a migration — this is intentional: it catches typos
-- (e.g., 'Phase1_Done') at insert time.
--
-- value is nullable to allow 'last_report_at' = NULL before the
-- first report is generated.
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

-- Bootstrap: default flags and cleanup parameters for localhost.
-- last_report_at is NULL — set on first report generation.
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
-- kind: static | dynamic
-- scan_done is defined as "all tasks in terminal state
-- (done | failed | timeout)", not "active_count = 0".
-- Terminal-state counters are populated in Phase 2.
--
-- last_completed_at is NULL until the first task in this kind
-- completes (done, failed, or timeout) — or until a dynamic
-- worker finishes its first monitoring cycle.
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

-- Bootstrap: two rows per host
INSERT OR IGNORE INTO task_state (host_id, kind) VALUES
    ('localhost', 'static'),
    ('localhost', 'dynamic');

-- ------------------------------------------------------------
-- reports — metadata for generated report bundles
-- ------------------------------------------------------------
-- The report itself is a bundle directory on disk:
--   <bundle_dir>/report.md, report.json, report.sarif, assets/
-- bundle_dir is 'current' for the live report, or '<timestamp>'
-- for archived ones.
-- Phase 0: only metadata is stored.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS reports (
    id           TEXT PRIMARY KEY,
    host_id      TEXT NOT NULL,
    bundle_dir   TEXT NOT NULL,                -- 'current' | '<timestamp>'
    tier         TEXT NOT NULL,                -- fast | full
    is_current   INTEGER NOT NULL DEFAULT 0,   -- 0 | 1
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

-- Only one current report per host
CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_current
    ON reports(host_id) WHERE is_current = 1;

-- ------------------------------------------------------------
-- Triggers — keep updated_at fresh
-- ------------------------------------------------------------
-- The WHEN clause prevents the trigger from firing on its own
-- UPDATE (infinite recursion guard).
--
-- NOTE: GraphStore is not thread-safe by design (one connection
-- per thread). Parallel updates to the same row do not occur,
-- so the trigger does not race. If causal ordering becomes a
-- requirement, an ADR is needed.

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

-- ------------------------------------------------------------
-- Seed schema_version
-- ------------------------------------------------------------

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (1, 'Phase 0: initial schema');

-- ============================================================
-- End of schema
-- ============================================================