"""AST Normalizer — turns TSA symbols into Node/Edge objects.

Responsibilities:
  1. Create Node objects for classified symbols.
  2. Create edges between nodes using simple heuristics.

Edges derived here:
  - provides_tool  (mcp_server → tool)
  - has_access_to  (tool → sink)
  - delegates_to   (entry_point → agent)
  - delegates_to   (agent → agent, orchestrator pattern)
  - has_access_to  (agent → mcp_server, via create_mcp_server)

Edges NOT derived here (require source analysis beyond TSA):
  - uses_model     (agent → llm_endpoint)
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
from .entry_points import EntryPointOutput
from .models import TSAOutput, TSASymbol


HOST_ID = "localhost"       # Phase 0/1 — single host


@dataclass
class GraphDelta:
    """Nodes and edges produced by normalization."""
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


def normalize(
    manifest: FileManifest,
    tsa_output: TSAOutput,
    entry_points: EntryPointOutput | None = None,
) -> GraphDelta:
    """Build graph delta from manifest, TSA output, and entry points.

    Args:
        manifest: file list from ingestion.discover()
        tsa_output: symbols from parsing.tsa_worker.run()
        entry_points: routes, agent calls, and MCP usages from
                      parsing.entry_points.run(). Pass None to skip.
    """
    delta = GraphDelta()
    entry_points = entry_points or EntryPointOutput()

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

    # --- Entry points ---
    entry_node_by_handler: dict[str, str] = {}
    for ep in entry_points.entry_points:
        node_id = _entry_point_id(ep)
        entry_node_by_handler[ep.handler] = node_id
        delta.nodes.append(Node(
            id=node_id,
            kind="entry_point",
            name=f"{ep.method} {ep.path}",
            host_id=HOST_ID,
            attributes={
                "source_file": ep.file_path,
                "line_start": ep.line,
                "method": ep.method,
                "path": ep.path,
                "handler": ep.handler,
            },
        ))

    # --- Indexes for edge derivation ---
    # agent_node_by_name:  "InvoiceAgent" -> node_id
    # agent_node_by_file:  "agents/invoice.py" -> node_id
    agent_node_by_name: dict[str, str] = {
        n.name: n.id for n in delta.nodes if n.kind == "agent"
    }
    agent_node_by_file: dict[str, str] = {
        n.attributes.get("source_file", ""): n.id
        for n in delta.nodes
        if n.kind == "agent"
    }

    # --- Edges: entry_point → agent (delegates_to) ---
    # Only for calls made from route handlers.
    for call in entry_points.agent_calls:
        if call.is_agent_to_agent:
            continue
        entry_id = entry_node_by_handler.get(call.handler)
        agent_id = agent_node_by_name.get(call.agent_name)
        if entry_id is None or agent_id is None:
            continue
        delta.edges.append(Edge(
            id=_edge_id(entry_id, agent_id, "delegates_to"),
            source_id=entry_id,
            target_id=agent_id,
            kind="delegates_to",
            host_id=HOST_ID,
        ))

    # --- Edges: agent → agent (delegates_to) ---
    # Calls made from agent modules (e.g. orchestrator → invoice).
    for call in entry_points.agent_calls:
        if not call.is_agent_to_agent:
            continue
        source_id = agent_node_by_file.get(call.file_path)
        target_id = agent_node_by_name.get(call.agent_name)
        if source_id is None or target_id is None:
            continue
        if source_id == target_id:
            continue  # skip self-loops
        delta.edges.append(Edge(
            id=_edge_id(source_id, target_id, "delegates_to"),
            source_id=source_id,
            target_id=target_id,
            kind="delegates_to",
            host_id=HOST_ID,
        ))

    # --- Edges: agent → mcp_server (has_access_to) ---
    # An agent that calls create_mcp_server("name") has access to
    # that MCP server.
    for usage in entry_points.mcp_usages:
        if not usage.is_agent_module:
            continue
        agent_id = agent_node_by_file.get(usage.file_path)
        server_id = mcp_servers.get(usage.mcp_server_name)
        if agent_id is None or server_id is None:
            continue
        delta.edges.append(Edge(
            id=_edge_id(agent_id, server_id, "has_access_to"),
            source_id=agent_id,
            target_id=server_id,
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


def _entry_point_id(ep) -> str:
    """Canonical ID for an entry point.

    Entry points are keyed by (file, method, path) — the handler
    name is not part of the key, so renaming the function does not
    create a new node.
    """
    key = f"entry_point:{ep.file_path}:{ep.method}:{ep.path}:{HOST_ID}"
    return f"entry_point:{_sha256(key)}"


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