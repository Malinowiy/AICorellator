"""IntraFileEdgeStage — `provides_tool` and same-file `has_access_to`.

Run order: 4 of 18 (see `normalizer_stages.STAGES`).

C4: "**Intra-file edges** — `provides_tool` (MCP server to tool),
`has_access_to` (tool to sink)."

Both edges are candidates, not calls: every tool in a file is linked to
every sink in the same file. Neither edge claims data flow. After
ADR-0016 this stage no longer runs the Flow Analyzer on the
`(tool_func, sink_call)` pair it can see: its result does not land on an
AST edge. Layer 2 runs the analyzer against a pair that runtime observed.
Every edge here is `flow_kind = "control"` with `data_flow_confirmed`
left at its schema default (`NULL`); `FlowKindStage` pins the kind.

A framework detector may still record a tool's signature result on the
tool *node* (`tool_data_flow`, 1.2a.2). Nothing copies it onto an edge
any more: an AST edge does not carry a data-flow claim (ADR-0016,
decision 3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Edge
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import (
    CONTROL_FLOW,
    edge_id,
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
                # path until runtime monitoring separates the two.
                # Control, and no data-flow claim: the edge leaves
                # `data_flow_confirmed` at its schema default (ADR-0016,
                # decision 3).
                **CONTROL_FLOW,
            ))


def _has_access_to(delta: "GraphDelta", ctx: NormalizeContext) -> None:
    """Every tool in a file to every sink in the same file.

    Reachability, not dataflow (ADR-0016, decision 3): the edge records
    that the tool and the sink share a file. Whether the tool body calls
    the sink — and whether an argument reaches it — is a question the
    AST layer does not answer and does not store.
    """
    for file_path, tools in ctx.tools_by_file.items():
        sinks = ctx.sinks_by_file.get(file_path, [])
        if not sinks:
            continue
        for tool_node in tools:
            for sink_node in sinks:
                delta.edges.append(Edge(
                    id=edge_id(
                        tool_node.id, sink_node.id, "has_access_to"
                    ),
                    source_id=tool_node.id,
                    target_id=sink_node.id,
                    kind="has_access_to",
                    host_id=ctx.host_id,
                ))