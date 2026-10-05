from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


@dataclass
class Provenance:
    tool: str
    timestamp: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Node:
    id: str
    kind: str
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: list[Provenance] = field(default_factory=list)


@dataclass
class Edge:
    source_id: str
    target_id: str
    kind: str
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: list[Provenance] = field(default_factory=list)


@dataclass
class ScanResult:
    tool: str
    timestamp: str
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def to_dict(obj) -> dict:
    return asdict(obj)