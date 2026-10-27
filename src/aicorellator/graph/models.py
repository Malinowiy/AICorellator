"""Data models for the AICorellator graph store.

These are plain dataclasses — no ORM. Repositories translate them
to/from SQLite rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    """ISO 8601 UTC timestamp, matching SQLite CURRENT_TIMESTAMP format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# Valid node kinds. Mirrors the CHECK constraint in schema.sql.
VALID_NODE_KINDS = frozenset({
    "agent",
    "llm_endpoint",
    "mcp_server",
    "tool",
    "credential",
    "sink",
    "entry_point",
    "guardrail",
    # Not an actor and not an action: the place untrusted content is
    # read from (1.3a, ADR-0010). Chain groups hang off it in 1.3b.
    "untrusted_source",
})

# Valid edge kinds. Mirrors the CHECK constraint in schema.sql.
VALID_EDGE_KINDS = frozenset({
    "uses_model",
    "uses_tool",
    "provides_tool",
    "delegates_to",
    "has_access_to",
    "guards",
    # A writer produces an untrusted source (1.3a.1, ADR-0010). Control,
    # not data: the edge records that the writer *declares* the source's
    # content, which is not a traced dataflow (ADR-0015).
    "writes_to",
})

# Valid flow kinds (ADR-0015). Mirrors the CHECK constraint in schema.sql.
#   control    — X calls Y, or X can reach Y through calls.
#   data       — data from X reaches Y (data flow confirmed).
#   structural — a declaration, not a path (guards).
VALID_FLOW_KINDS = frozenset({"control", "data", "structural"})

# Which Layer 3 rule level judged a chain (A.4). Mirrors the CHECK
# constraints in schema.sql (on `chains` and on `chain_groups`).
#   existence — existence was as far as the chain got: a node or an
#               edge was missing, or existence held and a structural
#               check rejected the chain.
#   semantic  — every check passed, which is also what `validated`
#               records (the two agree on every row A.4 wrote).
# `None` is a row written before A.4; `Chain.from_row` reads it back
# as "existence", which is the level that row was actually judged by.
VALID_VALIDATED_KINDS = frozenset({"existence", "semantic"})


# ------------------------------------------------------------
# Host
# ------------------------------------------------------------

@dataclass
class Host:
    id: str
    hostname: str
    platform: str
    ip: str | None = None
    agent_version: str | None = None
    last_heartbeat: str | None = None
    status: str = "active"
    created_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: Any) -> "Host":
        return cls(
            id=row["id"],
            hostname=row["hostname"],
            ip=row["ip"],
            platform=row["platform"],
            agent_version=row["agent_version"],
            last_heartbeat=row["last_heartbeat"],
            status=row["status"],
            created_at=row["created_at"],
        )


# ------------------------------------------------------------
# Node
# ------------------------------------------------------------

@dataclass
class Node:
    id: str
    kind: str
    name: str
    host_id: str
    attributes: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    observed_by: list[str] = field(default_factory=lambda: ["ast"])
    confidence: float = 1.0
    content_hash: str | None = None
    status: str = "active"
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["attributes"] = json.dumps(self.attributes)
        row["capabilities"] = json.dumps(self.capabilities)
        row["observed_by"] = json.dumps(self.observed_by)
        return row

    @classmethod
    def from_row(cls, row: Any) -> "Node":
        return cls(
            id=row["id"],
            kind=row["kind"],
            name=row["name"],
            host_id=row["host_id"],
            attributes=json.loads(row["attributes"]),
            capabilities=json.loads(row["capabilities"] or "[]"),
            observed_by=json.loads(row["observed_by"] or '["ast"]'),
            confidence=row["confidence"],
            content_hash=row["content_hash"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ------------------------------------------------------------
# Edge
# ------------------------------------------------------------

@dataclass
class Edge:
    id: str
    source_id: str
    target_id: str
    kind: str
    host_id: str
    precision: str = "syntactic"
    flow_kind: str = "control"
    data_flow_confirmed: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    observed_by: list[str] = field(default_factory=lambda: ["ast"])
    confidence: float = 1.0
    status: str = "active"
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["attributes"] = json.dumps(self.attributes)
        row["capabilities"] = json.dumps(self.capabilities)
        row["observed_by"] = json.dumps(self.observed_by)
        return row

    @classmethod
    def from_row(cls, row: Any) -> "Edge":
        return cls(
            id=row["id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            kind=row["kind"],
            host_id=row["host_id"],
            precision=row["precision"],
            flow_kind=row["flow_kind"],
            data_flow_confirmed=row["data_flow_confirmed"],
            attributes=json.loads(row["attributes"]),
            capabilities=json.loads(row["capabilities"] or "[]"),
            observed_by=json.loads(row["observed_by"] or '["ast"]'),
            confidence=row["confidence"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ------------------------------------------------------------
# Finding (scanner-reported weakness)
# ------------------------------------------------------------

@dataclass
class Finding:
    id: str
    scanner: str
    severity: str
    title: str
    node_id: str | None = None
    edge_id: str | None = None
    rule_id: str | None = None
    description: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    status: str = "active"
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["evidence"] = json.dumps(self.evidence)
        return row

    @classmethod
    def from_row(cls, row: Any) -> "Finding":
        return cls(
            id=row["id"],
            node_id=row["node_id"],
            edge_id=row["edge_id"],
            scanner=row["scanner"],
            rule_id=row["rule_id"],
            severity=row["severity"],
            title=row["title"],
            description=row["description"],
            evidence=json.loads(row["evidence"]),
            confidence=row["confidence"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ------------------------------------------------------------
# Chain (correlator-evaluated compromise chain)
# ------------------------------------------------------------

@dataclass
class Chain:
    id: str
    chain_nodes: list[str]
    chain_edges: list[str]
    verdict: str
    prompt_version: str
    confidence: float = 0.0
    reasoning: str | None = None
    voter_outputs: list[dict[str, Any]] = field(default_factory=list)
    validated: bool = False
    validated_kind: str | None = None
    validation_notes: list[str] = field(default_factory=list)
    status: str = "active"
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @property
    def length(self) -> int:
        """Number of hops. Derived from chain_nodes."""
        return max(0, len(self.chain_nodes) - 1)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["chain_nodes"] = json.dumps(self.chain_nodes)
        row["chain_edges"] = json.dumps(self.chain_edges)
        row["voter_outputs"] = json.dumps(self.voter_outputs)
        row["validation_notes"] = json.dumps(self.validation_notes)
        row["validated"] = 1 if self.validated else 0
        return row

    @classmethod
    def from_row(cls, row: Any) -> "Chain":
        return cls(
            id=row["id"],
            chain_nodes=json.loads(row["chain_nodes"]),
            chain_edges=json.loads(row["chain_edges"]),
            verdict=row["verdict"],
            prompt_version=row["prompt_version"],
            confidence=row["confidence"],
            reasoning=row["reasoning"],
            voter_outputs=json.loads(row["voter_outputs"]),
            validated=bool(row["validated"]),
            validated_kind=row["validated_kind"] or "existence",
            validation_notes=json.loads(row["validation_notes"] or "[]"),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ------------------------------------------------------------
# ChainGroupRecord (a `chain_groups` row)
# ------------------------------------------------------------

@dataclass
class ChainGroupRecord:
    """A chain group as stored — one row of `chain_groups`.

    Distinct from `query.ChainGroup`, which is the *enumerated* group
    returned by `find_chain_groups`: that one is transient and carries
    only structure. This one carries what the table carries — the
    prioritization results 1.3c writes (`score`, `score_components`,
    `priority`) and the evaluation results 1.3d/1.3e will write
    (`lethal_trifecta`, `verdict`, `confidence`, `reasoning`,
    `validated`). Two types rather than one because they have two
    owners and two lifetimes; `query.ChainGroup.to_record()` is the
    one-way bridge (a stored row never becomes an enumeration result).
    """

    id: str
    untrusted_source_id: str
    write_chain_ids: list[str]
    read_chain_ids: list[str]
    name: str | None = None
    lethal_trifecta: bool = False
    score: float = 0.0
    score_components: dict[str, Any] = field(default_factory=dict)
    verdict: str | None = None
    confidence: float = 0.0
    reasoning: str | None = None
    validated: bool = False
    validated_kind: str | None = None
    priority: str | None = None
    status: str = "active"
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["write_chain_ids"] = json.dumps(self.write_chain_ids)
        row["read_chain_ids"] = json.dumps(self.read_chain_ids)
        row["score_components"] = json.dumps(self.score_components)
        row["lethal_trifecta"] = 1 if self.lethal_trifecta else 0
        row["validated"] = 1 if self.validated else 0
        return row

    @classmethod
    def from_row(cls, row: Any) -> "ChainGroupRecord":
        return cls(
            id=row["id"],
            untrusted_source_id=row["untrusted_source_id"],
            write_chain_ids=json.loads(row["write_chain_ids"]),
            read_chain_ids=json.loads(row["read_chain_ids"]),
            name=row["name"],
            lethal_trifecta=bool(row["lethal_trifecta"]),
            score=row["score"],
            score_components=json.loads(row["score_components"] or "{}"),
            verdict=row["verdict"],
            confidence=row["confidence"],
            reasoning=row["reasoning"],
            validated=bool(row["validated"]),
            validated_kind=row["validated_kind"] or "existence",
            priority=row["priority"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ------------------------------------------------------------
# Report
# ------------------------------------------------------------

@dataclass
class Report:
    id: str
    host_id: str
    bundle_dir: str
    tier: str
    is_current: bool = False
    generated_at: str = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["is_current"] = 1 if self.is_current else 0
        row["metadata"] = json.dumps(self.metadata)
        return row

    @classmethod
    def from_row(cls, row: Any) -> "Report":
        return cls(
            id=row["id"],
            host_id=row["host_id"],
            bundle_dir=row["bundle_dir"],
            tier=row["tier"],
            is_current=bool(row["is_current"]),
            generated_at=row["generated_at"],
            metadata=json.loads(row["metadata"]),
        )


# ------------------------------------------------------------
# TaskCounter
# ------------------------------------------------------------

@dataclass
class TaskCounter:
    host_id: str
    kind: str
    active_count: int = 0
    done_count: int = 0
    failed_count: int = 0
    timeout_count: int = 0
    last_completed_at: str | None = None
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: Any) -> "TaskCounter":
        return cls(
            host_id=row["host_id"],
            kind=row["kind"],
            active_count=row["active_count"],
            done_count=row["done_count"],
            failed_count=row["failed_count"],
            timeout_count=row["timeout_count"],
            last_completed_at=row["last_completed_at"],
            updated_at=row["updated_at"],
        )