"""BodyAttributeStage — `sink_data_flow` / `returns_data` backfill.

Run order: 18 of 21 (see `normalizer_stages.STAGES`) — second to last.

Two prompt signals (1.2a.1) that ask the same shape of question — *what
does this node's own body do?* — of two node kinds:

    tool   `returns_data`     does the function hand data back?
    sink   `sink_data_flow`   does its body feed a dangerous call?

Both are read from source, through `ctx.functions_for` /
`ctx.classes_for` — the same per-scan cache the Flow Analyzer uses, so a
file is parsed at most once. A sink whose *name* is a class
(`UserProfileRepository`) resolves through the class index and is
analysed method by method (1.2a.5); a tool is always a function.

The stage runs late because the detector merge (stage 8) can replace a
TSA node with a detector node of the same `(name, source_file)`: the
replacement arrives without these attributes, and it is the node that
ends up in the graph. Filling them here — once, for whatever nodes
survived — is why the answer is asked after the merge rather than at
node construction. The guard is the same one the pipeline has always
used: a node that already carries the attribute (a detector-enriched
one) is left alone.

It is *not* last any more: `LethalTrifectaStage` (1.3d, stage 18) runs
after it. That stage shares this one's "after the merge" constraint and
has no constraint of its own, so it took the end of the pipeline; the
two are independent, and neither reads what the other writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Node
from ..flow import analyze_sink_body, analyze_sink_class, analyze_tool_returns
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import node_line_start, resolve_definition

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class BodyAttributeStage:
    """Backfill the tool and sink body attributes on the final nodes."""

    name = "body_attributes"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        for node in delta.nodes:
            if node.kind == "tool" and "returns_data" not in node.attributes:
                node.attributes.update(body_attributes(node, ctx))
            elif (
                node.kind == "sink"
                and "sink_data_flow" not in node.attributes
            ):
                node.attributes.update(body_attributes(node, ctx))
        return delta


def body_attributes(node: Node, ctx: NormalizeContext) -> dict[str, object]:
    """Flow attributes for a tool or sink node (1.2a.1, 1.2a.5).

    A tool records whether it returns data to its caller; a sink records
    how its own body passes data to a dangerous call. The body is
    resolved in the file's function index by name **and** `line_start`
    (1.2a.3f), so when a file defines the same name twice —
    `toggle_enabled` at `finbot/core/data/repositories.py:664` and
    `:1426` — each node reads its own definition instead of the first
    one. A sink whose name is a **class** — a capability-based sink such
    as `UserProfileRepository` — falls back to the class index and is
    analyzed by `analyze_sink_class` (1.2a.5); a tool is always a
    function, so it never consults the class index.

    When the body cannot be resolved — a heuristic node with no function
    or class of that name, or a file that could not be parsed — the
    fallback is `returns_data = False` / `sink_data_flow = "unknown"`.
    """
    source_file = str(node.attributes.get("source_file", ""))
    line_start = node_line_start(node)
    func = resolve_definition(
        ctx.functions_for(source_file), node.name, line_start
    )
    if node.kind == "tool":
        return {
            "returns_data": (
                func is not None and analyze_tool_returns(func)
            ),
        }
    if func is not None:
        # The node attributes carry the decision's kind; the decision's
        # evidence and source stay with the producer.
        return {"sink_data_flow": analyze_sink_body(func).kind}
    cls = resolve_definition(
        ctx.classes_for(source_file), node.name, line_start
    )
    if cls is not None:
        return {"sink_data_flow": analyze_sink_class(cls).kind}
    return {"sink_data_flow": "unknown"}
