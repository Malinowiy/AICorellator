"""UntrustedSourceStage — `untrusted_source` nodes and their readers.

Run order: 6 of 16 (see `normalizer_stages.STAGES`).

C4: no step of the pre-1.3 pipeline corresponds to this one; it is the
node kind ADR-0010 introduces, and it sits here for two mechanical
reasons:

  - **After `entry_points`.** A read inside a route handler has no node
    of its own — a plain handler is not classified as a tool, a sink or
    an agent, so the only node that can carry the edge is the
    `entry_point` node, which the previous stage creates.
  - **Before `detector_merge`.** The merge may replace a reader node and
    invalidate its id; running before it means the merge's own id remap
    re-points these edges to the detector nodes, which is the same
    reason `entry_to_sink` runs before the merge.

`flow_kind` is not set here. The edge carries `data_flow_confirmed` and
`FlowKindStage` derives the kind from it, once, for every edge (ADR-0015
decision 3) — the same division of labour as `intra_file_edges`.

Side effects: none. The stage reads the module cache (`ctx.module_for`,
one parse per file per scan) and the delta built so far.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Edge, Node
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import HOST_ID, edge_id, untrusted_source_id
from ..untrusted_sources import UntrustedSourceRead, find_untrusted_source_reads

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class UntrustedSourceStage:
    """One node per read site, plus an edge to the reader that has one."""

    name = "untrusted_sources"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        readers = _reader_index(delta)
        emitted: set[str] = set()
        linked: set[str] = set()

        for file_entry in ctx.manifest.files:
            file_path = file_entry.path
            module = ctx.module_for(file_path)
            if module is None:
                continue
            for read in find_untrusted_source_reads(module):
                node_id = untrusted_source_id(
                    read.source_type, read.call_target, file_path
                )
                if node_id not in emitted:
                    emitted.add(node_id)
                    delta.nodes.append(
                        _source_node(read, file_path, node_id)
                    )
                reader_id = _reader_node_id(read, file_path, readers)
                if reader_id is None:
                    # No node for the reader: the source is still a fact
                    # about the file. An edge with no target is not.
                    continue
                edge = _read_edge(node_id, reader_id, ctx)
                if edge.id in linked:
                    continue
                linked.add(edge.id)
                delta.edges.append(edge)
        return delta


def _source_node(
    read: UntrustedSourceRead,
    file_path: str,
    node_id: str,
) -> Node:
    """The `untrusted_source` node for one read site.

    Capabilities are empty, deliberately: `reads_untrusted` is a property
    of the *reader* — the component whose body makes the call — and the
    extractor already credited it there. A source is a place, and a place
    does not act; giving it the capability would say the corpus reads
    itself.

    The name is `<source_type>:<call_target>`, which is the identity in
    `untrusted_source_id` rendered for a human and for the prompt: a
    report says `mcp_tool_description:client.list_tools`, not a hash.
    """
    attributes: dict[str, object] = {
        "source_type": read.source_type,
        "call_target": read.call_target,
        "source_file": file_path,
        "line_start": read.line,
        "reader_kind": _reader_kind(read),
    }
    if read.reader is not None:
        attributes["reader"] = read.reader
    if read.container is not None:
        attributes["reader_container"] = read.container
    return Node(
        id=node_id,
        kind="untrusted_source",
        name=f"{read.source_type}:{read.call_target}",
        host_id=HOST_ID,
        attributes=attributes,
        content_hash=None,
    )


def _read_edge(
    source_id: str,
    reader_id: str,
    ctx: NormalizeContext,
) -> Edge:
    """`has_access_to` from the source to the reader that reads it.

    Direction is the data's: the source flows into the reader, which is
    the direction `find_chains` traverses, so a chain that starts at an
    untrusted source and ends at a sink is a path through the reader.

    `call_result_match` is the honest `data_flow_confirmed` value — the
    reader's data is the result of this call (`flow/arguments.py`: an
    `ast.Call` expression classifies as `call_result_match`) — and it is
    the decision ADR-0015 decision 3 maps to `flow_kind = "data"`, which
    is what the read is. Nothing here is inferred from names beyond the
    call site itself.
    """
    return Edge(
        id=edge_id(source_id, reader_id, "has_access_to"),
        source_id=source_id,
        target_id=reader_id,
        kind="has_access_to",
        host_id=ctx.host_id,
        data_flow_confirmed="call_result_match",
    )


def _reader_kind(read: UntrustedSourceRead) -> str:
    """Where the read happens: a method, a function, or the module."""
    if read.container is not None:
        return "method"
    if read.reader is not None:
        return "function"
    return "module"


def _reader_index(delta: "GraphDelta") -> dict[tuple[str, str], str]:
    """`(source_file, name)` → node id, for the nodes built so far.

    Two names map to one node: a node's own `name`, and — for entry
    points, whose name is `"GET /path"` rather than the handler's — its
    `handler` attribute. That is what lets a read inside a route handler
    resolve to the entry point.

    First write wins (`setdefault`), and `delta.nodes` is in stage order,
    so a method node (created by `method_nodes`) takes precedence over an
    entry point node of the same name: the more specific node is the
    reader. A node without a `source_file` — a detector node that has not
    been merged yet — is not a reader candidate at all.
    """
    index: dict[tuple[str, str], str] = {}
    for node in delta.nodes:
        file_path = node.attributes.get("source_file")
        if not isinstance(file_path, str):
            continue
        index.setdefault((file_path, node.name), node.id)
        handler = node.attributes.get("handler")
        if isinstance(handler, str):
            index.setdefault((file_path, handler), node.id)
    return index


def _reader_node_id(
    read: UntrustedSourceRead,
    file_path: str,
    readers: dict[tuple[str, str], str],
) -> str | None:
    """The most specific existing node for the reader, or None.

    The enclosing definition first (a method or a function node), then
    the enclosing class — a method that is not classified has no node,
    but the class that exercises the same capability does — then the
    entry point for the handler name. Returns None when the file has no
    node for either name, which is a read in a script.
    """
    for name in (read.reader, read.container):
        if name is None:
            continue
        node_id = readers.get((file_path, name))
        if node_id is not None:
            return node_id
    return None


__all__ = ["UntrustedSourceStage"]
