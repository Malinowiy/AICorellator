"""OrphanEntryStage — entry points with no delegation (Resolver 2).

Run order: 16 of 19 (see `normalizer_stages.STAGES`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Edge
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import CONTROL_FLOW, edge_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class OrphanEntryStage:
    """Link entry points with no outgoing delegation to the one agent."""

    name = "orphan_entry"

    def apply(self, delta: GraphDelta, ctx: NormalizeContext) -> GraphDelta:
        delta.edges.extend(
            link_orphan_entry_points(delta=delta, host_id=ctx.host_id)
        )
        return delta


def link_orphan_entry_points(
    delta: GraphDelta,
    host_id: str,
) -> list[Edge]:
    """Link entry_points with no outgoing edges to the single framework agent.

    Preconditions for linking (all must hold):
      - Exactly one agent node with a `framework` attribute exists.
      - At least one entry_point node exists.
      - The entry_point has no outgoing `delegates_to` edge yet.

    Returns an empty list if the preconditions do not hold. This is
    a deliberate no-op in ambiguous cases (0 or ≥2 framework agents).
    """
    # 1. Find framework agents.
    framework_agents = [
        n for n in delta.nodes
        if n.kind == "agent" and n.attributes.get("framework")
    ]
    if len(framework_agents) != 1:
        return []

    agent_node = framework_agents[0]

    # 2. Find entry_points with no outgoing delegates_to edge.
    sources_with_outgoing: set[str] = {
        e.source_id for e in delta.edges if e.kind == "delegates_to"
    }
    orphan_entries = [
        n for n in delta.nodes
        if n.kind == "entry_point" and n.id not in sources_with_outgoing
    ]
    if not orphan_entries:
        return []

    # 3. Build edges.
    edges: list[Edge] = []
    for entry in orphan_entries:
        new_id = edge_id(entry.id, agent_node.id, "delegates_to")
        edges.append(Edge(
            id=new_id,
            source_id=entry.id,
            target_id=agent_node.id,
            kind="delegates_to",
            host_id=host_id,
            attributes={
                "framework": agent_node.attributes.get("framework", ""),
                "resolved_via": "orphan_entry_to_single_agent",
            },
            **CONTROL_FLOW,
        ))
    return edges
