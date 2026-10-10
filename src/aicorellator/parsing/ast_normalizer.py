"""AST Normalizer — turns TSA symbols into Node/Edge objects.

Two responsibilities:
  1. Create Node objects for classified symbols.
  2. Create edges between nodes using simple heuristics.

Edges are best-effort: only `provides_tool` and `has_access_to`
are derived here. `uses_model` and `delegates_to` require source
analysis beyond what TSA provides — they are produced by the LLM
Extractor (later phase).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from ..graph.models import Edge, Node
from ..ingestion.discovery import FileManifest
from .classification import (
    Classification,
    classify_class,
    classify_file,
    classify_method,
)
from .models import TSAOutput, TSASymbol


HOST_ID = "localhost"       # Phase 0/1 — single host


@dataclass
class GraphDelta:
    """Nodes and edges produced by normalization."""
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


def normalize(manifest: FileManifest, tsa_output: TSAOutput) -> GraphDelta:
    """Build graph delta from manifest and TSA output."""
    delta = GraphDelta()

    # --- MCP servers from files ---
    # One node per server name, deduplicated across files.
    mcp_servers: dict[str, str] = {}   # server_name -> node_id
    for file_entry in manifest.files:
        c = classify_file(file_entry.path)
        if c is None:
            continue
        server_name = c.attributes["server_name"]
        if server_name in mcp_servers:
            continue
        node_id = _mcp_server_id(server_name)
        mcp_servers[server_name] = node_id
        delta.nodes.append(Node(
            id=node_id,
            kind="mcp_server",
            name=server_name,
            host_id=HOST_ID,
            attributes={"server_name": server_name},
            content_hash=None,  # server spans multiple files
        ))

    # --- Classes ---
    # agent, llm_endpoint, guardrail, mcp_server (per-class)
    for symbol in tsa_output.symbols:
        if symbol.kind != "class":
            continue
        c = classify_class(symbol.name, symbol.file_path)
        if c is None:
            continue
        node = _node_from_class(symbol, c)
        delta.nodes.append(node)

    # --- Methods (tools and sinks) ---
    tools_by_file: dict[str, list[Node]] = {}
    sinks_by_file: dict[str, list[Node]] = {}

    for symbol in tsa_output.symbols:
        if symbol.kind != "method":
            continue
        c = classify_method(symbol.name, symbol.file_path, symbol.class_name)
        if c is None:
            continue
        node = _node_from_method(symbol, c)
        delta.nodes.append(node)

        if c.kind == "tool":
            tools_by_file.setdefault(symbol.file_path, []).append(node)
        elif c.kind == "sink":
            sinks_by_file.setdefault(symbol.file_path, []).append(node)

    # --- Edges: provides_tool (mcp_server -> tool) ---
    for file_path, tools in tools_by_file.items():
        server_name = _server_name_for_file(file_path)
        if server_name is None:
            continue
        server_id = mcp_servers.get(server_name)
        if server_id is None:
            continue
        for tool_node in tools:
            delta.edges.append(Edge(
                id=_edge_id(server_id, tool_node.id, "provides_tool"),
                source_id=server_id,
                target_id=tool_node.id,
                kind="provides_tool",
                host_id=HOST_ID,
            ))

    # --- Edges: has_access_to (tool -> sink) ---
    # Heuristic: if a tool and a sink are in the same file, link them.
    for file_path, tools in tools_by_file.items():
        sinks = sinks_by_file.get(file_path, [])
        for tool_node in tools:
            for sink_node in sinks:
                delta.edges.append(Edge(
                    id=_edge_id(tool_node.id, sink_node.id, "has_access_to"),
                    source_id=tool_node.id,
                    target_id=sink_node.id,
                    kind="has_access_to",
                    host_id=HOST_ID,
                ))

    return delta


# ============================================================
# Canonical ID derivation (from C4_L3_Phase1)
# ============================================================

def _mcp_server_id(server_name: str) -> str:
    h = _sha256(f"mcp_server:{server_name}:{HOST_ID}")
    return f"mcp_server:{h}"


def _node_from_class(symbol: TSASymbol, c: Classification) -> Node:
    h = _sha256(f"{c.kind}:{symbol.file_path}:{symbol.name}:{HOST_ID}")
    node_id = f"{c.kind}:{h}"
    return Node(
        id=node_id,
        kind=c.kind,
        name=symbol.name,
        host_id=HOST_ID,
        attributes={
            "source_file": symbol.file_path,
            "line_start": symbol.line_start,
            **c.attributes,
        },
    )


def _node_from_method(symbol: TSASymbol, c: Classification) -> Node:
    # Tools under MCP servers need stable IDs tied to their server.
    # Tools elsewhere (tools/data/) use file+name.
    key_parts = [c.kind, symbol.file_path, symbol.name]
    if symbol.class_name:
        key_parts.append(symbol.class_name)
    key_parts.append(HOST_ID)
    h = _sha256(":".join(key_parts))
    node_id = f"{c.kind}:{h}"
    return Node(
        id=node_id,
        kind=c.kind,
        name=symbol.name,
        host_id=HOST_ID,
        attributes={
            "source_file": symbol.file_path,
            "line_start": symbol.line_start,
            **c.attributes,
        },
    )


def _edge_id(source_id: str, target_id: str, kind: str) -> str:
    h = _sha256(f"{source_id}:{target_id}:{kind}:{HOST_ID}")
    return f"edge:{h}"


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _server_name_for_file(file_path: str) -> str | None:
    """Extract mcp server name from a file path, or None."""
    parts = file_path.split("/")
    if "mcp" in parts and "servers" in parts:
        try:
            servers_idx = parts.index("servers")
        except ValueError:
            return None
        if servers_idx + 2 >= len(parts):
            return None
        name = parts[servers_idx + 1]
        if name.endswith(".py"):
            return None
        return name
    return None