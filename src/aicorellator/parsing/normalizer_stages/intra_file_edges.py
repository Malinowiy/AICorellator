"""IntraFileEdgeStage — `provides_tool` and same-file `has_access_to`.

Run order: 4 of 15 (see `normalizer_stages.STAGES`).

C4: "**Intra-file edges** — `provides_tool` (MCP server to tool),
`has_access_to` (tool to sink)."

Both edges are candidates, not calls: every tool in a file is linked to
every sink in the same file, and the edge's `flow_kind` says whether the
Flow Analyzer found the sink call inside the tool body and how the
argument reaches it (ADR-0015). Source is read through
`ctx.functions_for` (one parse per file per scan). Two other stages read
source the same way — `untrusted_sources` (a call-site walk, 1.3a) and
`body_attributes` (the tool/sink body analyzers, 1.2a.1); this is the
only one that runs the Flow Analyzer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Edge
from ..flow import FlowDecision, analyze_has_access_to
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import (
    edge_id,
    find_sink_call,
    flow_for_tool_node,
    node_line_start,
    resolve_definition,
    server_name_for_file,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class IntraFileEdgeStage:
    """`provides_tool` (server → tool) and `has_access_to` (tool → sink)."""

    name = "intra_file_edges"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        _provides_tool(delta, ctx)
        _has_access_to(delta, ctx)
        return delta


def _provides_tool(delta: "GraphDelta", ctx: NormalizeContext) -> None:
    """One `provides_tool` edge per tool, when the file is an MCP server."""
    for file_path, tools in ctx.tools_by_file.items():
        server_name = server_name_for_file(file_path)
        if server_name is None:
            continue
        server_id = ctx.mcp_servers.get(server_name)
        if server_id is None:
            continue
        for tool_node in tools:
            delta.edges.append(Edge(
                id=edge_id(server_id, tool_node.id, "provides_tool"),
                source_id=server_id,
                target_id=tool_node.id,
                kind="provides_tool",
                host_id=ctx.host_id,
                # Access path, not a declaration (ADR-0015, decision 5):
                # "the agent can call the tool" is the candidate attack
                # path until runtime monitoring separates the two. A
                # framework detector may have enriched the tool node
                # with its signature result (1.2a.2); the edge inherits
                # it and nothing else changes.
                **flow_for_tool_node(tool_node),
            ))


def _has_access_to(delta: "GraphDelta", ctx: NormalizeContext) -> None:
    """Every tool in a file to every sink in the same file."""
    for file_path, tools in ctx.tools_by_file.items():
        sinks = ctx.sinks_by_file.get(file_path, [])
        if not sinks:
            continue
        functions = ctx.functions_for(file_path)
        for tool_node in tools:
            tool_func = resolve_definition(
                functions, tool_node.name, node_line_start(tool_node)
            )
            for sink_node in sinks:
                delta.edges.append(Edge(
                    id=edge_id(
                        tool_node.id, sink_node.id, "has_access_to"
                    ),
                    source_id=tool_node.id,
                    target_id=sink_node.id,
                    kind="has_access_to",
                    host_id=ctx.host_id,
                    **_flow_kind(tool_func, sink_node),
                ))


def _flow_kind(tool_func, sink_node) -> dict[str, str]:
    """Edge flow attributes: the analyzer's decision, or reachability.

    The decision's kind is written as `data_flow_confirmed`;
    `FlowKindStage` derives `flow_kind` from it, so this stage never
    calls the mapping.
    """
    if tool_func is None:
        return {"data_flow_confirmed": "unknown"}
    sink_call = find_sink_call(tool_func, sink_node.name)
    if sink_call is None:
        # The sink call is not in the tool body: reachability without
        # evidence, so the decision says so.
        decision = FlowDecision(
            "unknown",
            ("no sink call in the tool body",),
            source="edge_analyzer",
        )
    else:
        decision = analyze_has_access_to(tool_func, sink_call)
    return {"data_flow_confirmed": decision.kind}