from collections import defaultdict
from aicorellator.models import Node, Edge


def serialize_graph(
    nodes: list[Node],
    edges: list[Edge],
    include_kinds: list[str] | None = None,
    include_edge_kinds: list[str] | None = None,
    max_nodes: int = 200,
) -> str:
    """
    Converts the graph into a text representation for an LLM.

    include_kinds: if set, only nodes of these kinds are included
    include_edge_kinds: if set, only edges of these kinds are included
    max_nodes: guard against context overflow
    """
    # Filter nodes
    if include_kinds:
        nodes = [n for n in nodes if n.kind in include_kinds]
    if max_nodes and len(nodes) > max_nodes:
        nodes = nodes[:max_nodes]

    node_ids = {n.id for n in nodes}

    # Filter edges: both endpoints must be among the filtered nodes
    if include_edge_kinds:
        edges = [e for e in edges if e.kind in include_edge_kinds]
    edges = [
        e for e in edges
        if e.source_id in node_ids and e.target_id in node_ids
    ]

    lines = []

    # NODES section
    lines.append("NODES:")
    for n in nodes:
        attrs = _format_attrs(n)
        lines.append(f"- [{n.id}] ({n.kind}) {n.name}{attrs}")

    # EDGES section
    lines.append("")
    lines.append("EDGES:")
    for e in edges:
        attrs = _format_edge_attrs(e)
        lines.append(f"- {e.source_id} --{e.kind}--> {e.target_id}{attrs}")

    return "\n".join(lines)


def _format_attrs(node: Node) -> str:
    """Extracts only the significant attributes, not the whole dict."""
    parts = []
    a = node.attributes or {}

    if a.get("provider"):
        parts.append(f"provider={a['provider']}")
    if a.get("risk"):
        risk = a["risk"]
        if isinstance(risk, dict):
            parts.append(f"risk={risk.get('level', '?')}")
        else:
            parts.append(f"risk={risk}")
    if a.get("capabilities"):
        caps = a["capabilities"]
        active = [k for k, v in caps.items() if v]
        if active:
            parts.append(f"caps=[{','.join(active)}]")
    if a.get("classification"):
        cls = a["classification"]
        if isinstance(cls, dict) and cls.get("sensitivity_label"):
            parts.append(f"sensitivity={cls['sensitivity_label']}")

    return f" {', '.join(parts)}" if parts else ""


def _format_edge_attrs(edge: Edge) -> str:
    parts = []
    a = edge.attributes or {}
    if a.get("risk"):
        parts.append(f"risk={a['risk']}")
    if a.get("exfil"):
        parts.append("exfil=true")
    return f" ({', '.join(parts)})" if parts else ""