"""ClassNodeStage — `class` symbols to nodes.

Run order: 2 of 16 (see `normalizer_stages.STAGES`).

C4: "**Class nodes** — Level 3 classification, with Level 2 capabilities
supplied as input."

A class can be an agent, an llm_endpoint, a tool, a sink or a guardrail;
the classification decides, and the node carries the capabilities the
extractor found in the class body. Class-level sinks (1.2a.5) get their
`sink_data_flow` later, in `BodyAttributeStage` — the attribute question
is asked of the finished node, after the detector merge, so it is one
stage instead of two.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..classification import classify_class
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import caps_for_symbol, node_from_class

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class ClassNodeStage:
    """One node per classified class symbol."""

    name = "class_nodes"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        for symbol in ctx.tsa_output.symbols:
            if symbol.kind != "class":
                continue
            symbol_caps = caps_for_symbol(
                ctx.capabilities, symbol.file_path, symbol.name
            )
            c = classify_class(
                symbol.name, symbol.file_path, capabilities=symbol_caps
            )
            if c is None:
                continue
            delta.nodes.append(
                node_from_class(symbol, c, ctx.capabilities)
            )
        return delta
