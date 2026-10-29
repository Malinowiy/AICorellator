"""CrossFileResolutionStage — cross-module edges (Resolvers 1 and B.1).

Run order: 14 of 18 (see `normalizer_stages.STAGES`).

C4: "**Cross-file resolution** — `uses_tool` edges from agent modules to
tool modules they import (ADR-0013, Resolver 1)."

Two resolutions run here, both import-driven:

    agent → tool     an agent module imports a tool module (Resolver 1,
                     ADR-0013) — `uses_tool`, resolved by the detector
                     module's `resolve_agent_tool_edges`
    tool  → sink     a tool module imports the module that holds a sink
                     the tool reaches through the call graph (BACKLOG
                     B.1) — `has_access_to`, resolved here

Framework detectors are per-file (ADR-0011), so an agent created in
`agent.py` that uses tools imported from `tools.py` has no `uses_tool`
edge from the detector alone. Resolver 1 links to *every* tool in an
imported module — a deliberate false-positive trade-off (ADR-0009):
Phase 3 filters them.

The tool → sink resolver is the same idea one hop further in. `tool →
sink` (`has_access_to`) is otherwise built only *within* a single file
(`intra_file_edges`), so a tool in `mcp/servers/findrive/server.py` that
calls a sink in `mcp/servers/findrive/repositories.py` gets no edge, and
`find_chains` loses the path. This resolver reads the tool and the sink
from the delta — a tool node and a sink node carry `source_file`, and the
import table (`ctx.entry_points.imports`) says the tool's module imports
the sink's — and links them when the tool reaches the sink by name
through the call graph, exactly the way `entry_to_sink` does (BFS over
`reachable_functions`). Every tool × every reachable sink in an imported
file is linked, mirroring `intra_file_edges`' tool × sink and accepting
the same false positives (ADR-0009).

**Read the nodes from the delta, not the per-file indexes.** This stage
runs *after* `detector_merge` (stage 10), which invalidates the TSA node
ids in `ctx.tools_by_file` / `ctx.sinks_by_file` (the reason
`entry_to_sink` runs before the merge — see its module docstring). A node
in `delta.nodes` carries the id the merge settled on, so both endpoints
are read from there.

**Flow discipline.** Both edges are control edges built by a
post-`flow_kind` resolver: `flow_kind = "control"` via `CONTROL_FLOW`,
`observed_by = ["ast"]` (the `Edge` default) and `data_flow_confirmed`
left at its schema default (`NULL`) — no value is traced and no data-flow
claim is written (ADR-0015, ADR-0016, decision 3).

Edges whose id the delta already has are skipped, so a detector edge
(which carries the 1.2a.2 enrichment) or a same-file `has_access_to`
edge from `intra_file_edges` is never overwritten or duplicated by the
control default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Edge, Node
from ..framework_detectors.cross_file import (
    _module_matches_file,
    resolve_agent_tool_edges,
)
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import CONTROL_FLOW, HOST_ID, edge_id
from .entry_to_sink import reachable_functions

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class CrossFileResolutionStage:
    """`uses_tool` (agent → tool) and `has_access_to` (tool → sink)."""

    name = "cross_file_resolution"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        existing_edge_ids = {e.id for e in delta.edges}

        new_edges = list(resolve_agent_tool_edges(
            manifest=ctx.manifest,
            detector_nodes=ctx.framework_detection.nodes,
            host_id=ctx.host_id,
        ))
        new_edges.extend(resolve_tool_sink_edges(
            nodes=delta.nodes,
            imports_by_file=ctx.entry_points.imports,
            call_graph=ctx.entry_points.call_graph,
            host_id=ctx.host_id,
        ))

        for edge in new_edges:
            if edge.id in existing_edge_ids:
                continue
            delta.edges.append(edge)
            existing_edge_ids.add(edge.id)
        return delta


def resolve_tool_sink_edges(
    *,
    nodes: list[Node],
    imports_by_file: dict[str, list[str]],
    call_graph: dict[str, set[str]],
    host_id: str = HOST_ID,
) -> list[Edge]:
    """`has_access_to` from a tool to a sink in a module it imports (B.1).

    Args:
        nodes: the delta's nodes. Tools and sinks are read from here, not
            from `ctx.tools_by_file` / `ctx.sinks_by_file`, whose TSA ids
            are stale after `detector_merge`.
        imports_by_file: `ctx.entry_points.imports` — `{file: [module]}`
            for every Python file. Relative imports are already dropped
            there, so they resolve to nothing here, as everywhere else.
        call_graph: `ctx.entry_points.call_graph` — name-keyed
            `{function: {called_name}}`, `ast.Name` calls only.
        host_id: host identifier for the edge.

    Returns:
        One `has_access_to` edge per `(tool, sink)` pair where the tool's
        file imports the sink's file and the tool reaches the sink by
        name through the call graph. Empty when no such pair exists.
    """
    # --- Index the delta's tools and sinks by source file ---
    tools_by_file: dict[str, list[Node]] = {}
    sinks_by_file: dict[str, list[Node]] = {}
    for node in nodes:
        source_file = node.attributes.get("source_file")
        if not source_file:
            continue
        if node.kind == "tool":
            tools_by_file.setdefault(source_file, []).append(node)
        elif node.kind == "sink":
            sinks_by_file.setdefault(source_file, []).append(node)

    if not tools_by_file or not sinks_by_file:
        return []

    edges: list[Edge] = []
    seen: set[str] = set()

    for tool_file, tools in tools_by_file.items():
        modules = imports_by_file.get(tool_file, [])
        if not modules:
            continue

        # The sink files this tool file imports. Same file is skipped:
        # `intra_file_edges` already built that edge, and it is the one
        # the graph keeps (the id check below would catch it anyway).
        sink_files = [
            sink_file for sink_file in sinks_by_file
            if sink_file != tool_file
            and any(
                _module_matches_file(module, sink_file)
                for module in modules
            )
        ]
        if not sink_files:
            continue

        for tool in tools:
            reachable = reachable_functions(
                start=tool.name, call_graph=call_graph
            )
            for sink_file in sink_files:
                for sink in sinks_by_file[sink_file]:
                    if sink.name not in reachable:
                        continue
                    new_id = edge_id(tool.id, sink.id, "has_access_to")
                    if new_id in seen:
                        continue
                    seen.add(new_id)
                    edges.append(Edge(
                        id=new_id,
                        source_id=tool.id,
                        target_id=sink.id,
                        kind="has_access_to",
                        host_id=host_id,
                        # Control-flow reachability, not dataflow: the
                        # call graph is name-based, so nothing is known
                        # about what data reaches the sink (ADR-0015).
                        # No `data_flow_confirmed` is written — the
                        # column is Layer 2's (ADR-0016, decision 3).
                        **CONTROL_FLOW,
                    ))

    return edges
