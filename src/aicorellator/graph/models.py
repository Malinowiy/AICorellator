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


# ------------------------------------------------------------
# Host
# ------------------------------------------------------------

@dataclass
class Host:
    id: str
    hostname: str
    platform: str                       # swarm | k8s | standalone
    ip: str | None = None
    agent_version: str | None = None
    last_heartbeat: str | None = None
    status: str = "active"              # active | unreachable | stale
    created_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row) -> "Host":
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
    id: str                             # kind-specific sha256, includes host_id
    kind: str                           # agent | llm_endpoint | mcp_server | tool | credential | sink | entry_point
    name: str
    host_id: str
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    content_hash: str | None = None
    status: str = "active"              # active | stale
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["attributes"] = json.dumps(self.attributes)
        return row

    @classmethod
    def from_row(cls, row: Any) -> "Node":
        return cls(
            id=row["id"],
            kind=row["kind"],
            name=row["name"],
            host_id=row["host_id"],
            attributes=json.loads(row["attributes"]),
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
    id: str                             # sha256(source + target + kind + host_id)
    source_id: str
    target_id: str
    kind: str                           # uses_model | provides_tool | delegates_to | has_access_to
    host_id: str
    precision: str = "syntactic"        # syntactic | compiler-verified
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    status: str = "active"
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["attributes"] = json.dumps(self.attributes)
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
            attributes=json.loads(row["attributes"]),
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
    scanner: str                        # aak | llm-scanner | mcp-scanner
    severity: str                       # critical | high | medium | low
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
    verdict: str                        # exploitable | not_exploitable | uncertain
    prompt_version: str
    confidence: float = 0.0
    reasoning: str | None = None
    voter_outputs: list[dict[str, Any]] = field(default_factory=list)
    validated: bool = False
    status: str = "active"
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["chain_nodes"] = json.dumps(self.chain_nodes)
        row["chain_edges"] = json.dumps(self.chain_edges)
        row["voter_outputs"] = json.dumps(self.voter_outputs)
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
    bundle_dir: str                     # 'current' | '<timestamp>'
    tier: str                           # fast | full
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
    kind: str                           # static | dynamic
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