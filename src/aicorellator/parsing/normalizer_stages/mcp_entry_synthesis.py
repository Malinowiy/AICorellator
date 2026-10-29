"""McpEntrySynthesisStage — virtual MCP entry (Resolver 3).

Run order: 15 of 19 (see `normalizer_stages.STAGES`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Edge, Node
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import (
    CONTROL_FLOW,
    HOST_ID,
    edge_id,
    mcp_entry_point_id,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class McpEntrySynthesisStage:
    """Synthesize one entry point per framework-detected MCP server."""

    name = "mcp_entry_synthesis"

    def apply(self, delta: GraphDelta, ctx: NormalizeContext) -> GraphDelta:
        delta.edges.extend(link_mcp_entries_to_tools(delta=delta))
        return delta


def link_mcp_entries_to_tools(delta: GraphDelta) -> list[Edge]:
    """Create virtual entry points for framework-detected MCP servers
    and link them to the tools they expose.

    Side effects: appends the new entry_point nodes to `delta.nodes`.
    Returns only the new edges (the caller appends them).
    """
    edges: list[Edge] = []

    # Index tools by parent_server, only those with framework provenance.
    tools_by_parent: dict[str, list[Node]] = {}
    for node in delta.nodes:
        if node.kind != "tool":
            continue
        parent = node.attributes.get("parent_server")
        if parent:
            tools_by_parent.setdefault(parent, []).append(node)

    for mcp_node in delta.nodes:
        if mcp_node.kind != "mcp_server":
            continue
        framework = mcp_node.attributes.get("framework")
        if not framework:
            continue

        entry_id = mcp_entry_point_id(mcp_node.name)

        delta.nodes.append(Node(
            id=entry_id,
            kind="entry_point",
            name=f"MCP:{mcp_node.name}",
            host_id=HOST_ID,
            attributes={
                "source_file": mcp_node.attributes.get("source_file", ""),
                "mcp_server_name": mcp_node.name,
                "framework": framework,
                "entry_kind": "mcp",
            },
        ))

        for tool_node in tools_by_parent.get(mcp_node.name, []):
            edges.append(Edge(
                id=edge_id(entry_id, tool_node.id, "delegates_to"),
                source_id=entry_id,
                target_id=tool_node.id,
                kind="delegates_to",
                host_id=HOST_ID,
                attributes={"resolved_via": "mcp_entry"},
                # The MCP client → tool path is the protocol channel.
                # Control, and no data-flow claim: a detector may have
                # recorded whether the tool takes arguments (1.2a.2),
                # but that result stays on the node — it does not land
                # on the edge (ADR-0016, decision 3).
                **CONTROL_FLOW,
            ))

    return edges
