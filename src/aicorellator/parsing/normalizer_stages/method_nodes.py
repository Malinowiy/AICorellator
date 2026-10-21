"""MethodNodeStage — `method` symbols to nodes; tools and sinks indexed.

Run order: 3 of 14 (see `normalizer_stages.STAGES`).

C4: "**Method nodes** — Level 3 classification, with capabilities.
Tools and sinks are tracked per file."

Side effects: fills `ctx.tools_by_file` and `ctx.sinks_by_file`, which
`IntraFileEdgeStage`, `EntryToSinkStage` and `BodyAttributeStage` read.
The per-file keys are the same relative paths the manifest uses.

`sink_data_flow` / `returns_data` are *not* set here: `BodyAttributeStage`
does that once, after the detector merge (1.2a.1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..classification import classify_method
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import caps_for_symbol, node_from_method

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class MethodNodeStage:
    """One node per classified method symbol, indexed by file and kind."""

    name = "method_nodes"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        for symbol in ctx.tsa_output.symbols:
            if symbol.kind != "method":
                continue
            symbol_caps = caps_for_symbol(
                ctx.capabilities, symbol.file_path, symbol.name
            )
            c = classify_method(
                symbol.name,
                symbol.file_path,
                symbol.class_name,
                capabilities=symbol_caps,
            )
            if c is None:
                continue
            node = node_from_method(symbol, c, ctx.capabilities)
            delta.nodes.append(node)

            if c.kind == "tool":
                ctx.tools_by_file.setdefault(
                    symbol.file_path, []
                ).append(node)
            elif c.kind == "sink":
                ctx.sinks_by_file.setdefault(
                    symbol.file_path, []
                ).append(node)
        return delta
