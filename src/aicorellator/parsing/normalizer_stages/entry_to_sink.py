"""EntryToSinkStage — entry point to same-file sink reachability.

Run order: 9 of 19 (see `normalizer_stages.STAGES`).

C4 lists this resolver as step 13 of the normalizer — after the detector
merge. The code it was split from called it *before* the merge, and that
is where it still runs, because the merge invalidates the ids it uses:
the resolver reads the TSA-side `sinks_by_file` index, so an edge built
after the merge points at a node the merge removed. On dvmcp that is six
dangling edges and a `FOREIGN KEY constraint failed` at write time.
Before the merge, the merge's own id remap re-points those edges to the
detector nodes. C4's order is the one that is wrong.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Edge, Node
from ..entry_points import EntryPointOutput
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import CONTROL_FLOW, HOST_ID, edge_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class EntryToSinkStage:
    """Link each entry point to the sinks its handler can reach."""

    name = "entry_to_sink"

    def apply(self, delta: GraphDelta, ctx: NormalizeContext) -> GraphDelta:
        delta.edges.extend(link_entry_points_to_sinks(
            entry_points=ctx.entry_points,
            sinks_by_file=ctx.sinks_by_file,
            entry_node_by_handler=ctx.entry_node_by_handler,
            imports_by_file=ctx.entry_points.imports,
        ))
        return delta


def link_entry_points_to_sinks(
    entry_points: EntryPointOutput,
    sinks_by_file: dict[str, list[Node]],
    entry_node_by_handler: dict[str, str],
    imports_by_file: dict[str, list[str]],
) -> list[Edge]:
    """Resolve entry_point → sink edges via same-file call reachability.

    For HTTP routes and Streamlit entries: BFS from the handler (or
    module for module-level entries) through the file's call graph.

    For CLI entries: the handler is the module itself. BFS starts at
    every top-level function and every name imported from another
    module, so a CLI script that calls `run()` imported from a
    helper module can reach sinks in that helper.

    Control-flow reachability, not dataflow. False positives accepted
    (ADR-0009).
    """
    edges: list[Edge] = []

    for ep in entry_points.entry_points:
        entry_id = entry_node_by_handler.get(ep.handler)
        if entry_id is None:
            continue

        sinks_here = sinks_by_file.get(ep.file_path, [])
        if not sinks_here:
            continue

        # Determine BFS start nodes.
        if ep.method in ("CLI", "STREAMLIT"):
            # Module-level entry: start from every function defined
            # in the file and every imported name that could resolve
            # to a function in another module.
            starts: set[str] = set(entry_points.call_graph.keys())
            for module in imports_by_file.get(ep.file_path, []):
                # Imported module name; functions called via
                # `module.func(...)` are recorded by their last segment
                # in the call graph. Add the module's last segment too,
                # in case the call graph recorded it as a Name call.
                starts.add(module.split(".")[-1])
        else:
            starts = {ep.handler}

        reachable: set[str] = set()
        for start in starts:
            reachable |= reachable_functions(
                start=start,
                call_graph=entry_points.call_graph,
            )

        for sink_node in sinks_here:
            if sink_node.name in reachable:
                edges.append(Edge(
                    id=edge_id(entry_id, sink_node.id, "has_access_to"),
                    source_id=entry_id,
                    target_id=sink_node.id,
                    kind="has_access_to",
                    host_id=HOST_ID,
                    # Control-flow reachability, not dataflow (ADR-0015):
                    # the call graph is name-based, so nothing is known
                    # about what data reaches the sink.
                    **CONTROL_FLOW,
                ))
    # Fallback for CLI and Streamlit entries with no same-file sinks:
    # link to all sinks in the project. The rationale is the same as
    # for the orphan-entry resolver: a CLI script can call into any
    # module, and we do not track cross-file calls here. False
    # positives are accepted (ADR-0009).
    all_sinks: list[Node] = []
    for sinks in sinks_by_file.values():
        all_sinks.extend(sinks)

    if all_sinks:
        for ep in entry_points.entry_points:
            if ep.method not in ("CLI", "STREAMLIT"):
                continue
            entry_id = entry_node_by_handler.get(ep.handler)
            if entry_id is None:
                continue
            # Only add fallback edges if this entry has no outgoing
            # has_access_to edge yet.
            has_edge = any(
                e.source_id == entry_id and e.kind == "has_access_to"
                for e in edges
            )
            if has_edge:
                continue
            for sink_node in all_sinks:
                edges.append(Edge(
                    id=edge_id(entry_id, sink_node.id, "has_access_to"),
                    source_id=entry_id,
                    target_id=sink_node.id,
                    kind="has_access_to",
                    host_id=HOST_ID,
                    attributes={"resolved_via": "cli_fallback"},
                    **CONTROL_FLOW,
                ))
    return edges


def reachable_functions(
    start: str,
    call_graph: dict[str, set[str]],
) -> set[str]:
    """BFS from `start` through the call graph, including `start` itself.

    The call graph is a name-keyed mapping: function name → set of
    called function names. Only `ast.Name` calls are recorded (see
    `entry_points._collect_call_names`), so attribute calls like
    `self._helper()` are not followed.
    """
    visited: set[str] = set()
    queue: list[str] = [start]
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        for next_name in call_graph.get(current, ()):
            if next_name not in visited:
                queue.append(next_name)
    return visited
