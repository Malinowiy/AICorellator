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
    Превращает граф в текстовое представление для LLM.

    include_kinds: если задано — только узлы этих типов
    include_edge_kinds: если задано — только рёбра этих типов
    max_nodes: защита от переполнения контекста
    """
    # Фильтрация узлов
    if include_kinds:
        nodes = [n for n in nodes if n.kind in include_kinds]
    if max_nodes and len(nodes) > max_nodes:
        nodes = nodes[:max_nodes]

    node_ids = {n.id for n in nodes}

    # Фильтрация рёбер: оба конца должны быть в отфильтрованных узлах
    if include_edge_kinds:
        edges = [e for e in edges if e.kind in include_edge_kinds]
    edges = [
        e for e in edges
        if e.source_id in node_ids and e.target_id in node_ids
    ]

    lines = []

    # Секция NODES
    lines.append("NODES:")
    for n in nodes:
        attrs = _format_attrs(n)
        lines.append(f"- [{n.id}] ({n.kind}) {n.name}{attrs}")

    # Секция EDGES
    lines.append("")
    lines.append("EDGES:")
    for e in edges:
        attrs = _format_edge_attrs(e)
        lines.append(f"- {e.source_id} --{e.kind}--> {e.target_id}{attrs}")

    return "\n".join(lines)


def _format_attrs(node: Node) -> str:
    """Извлекает только значимые атрибуты, не весь словарь."""
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