"""FlowKindStage — `flow_kind` on every edge (ADR-0015).

Run order: 12 of 16 (see `normalizer_stages.STAGES`).

The graph carries two flow facts and they are not the same one:

    data_flow_confirmed   how confident the producer is that data moves
                          (six values; set where the edge is created,
                          because only that producer saw the evidence)
    flow_kind             control | data | structural — the coarse
                          classification the prompt and `find_chains`
                          read

This stage derives the second from the first, once, for every edge that
exists when it runs:

    data_flow_confirmed in DATA_KINDS          → data
    literal_only | no_args | unknown | NULL    → control
    a structural edge kind (`guards`)          → left alone

`FlowDecision` validates the value, so an edge carrying a kind outside
the six fails here rather than reaching the database. The mapping itself
is `flow.decision.flow_kind_for` — the single implementation of ADR-0015
decision 3, which is why this is the only place in the pipeline that
calls it.

The three resolvers that run after this stage (cross-file, MCP entry
synthesis, orphan entry, entry-to-sink) construct control edges and pass
`CONTROL_FLOW`, which is the same mapping's output for `unknown`; the
MCP entry synthesis is the one exception that can produce a data edge
after this point, and it carries the 1.2a.2 enrichment value from the
tool node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...patterns import STRUCTURAL_EDGE_KINDS
from ..flow import FlowDecision, flow_kind_for
from ..normalizer_context import NormalizeContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class FlowKindStage:
    """Derive `flow_kind` from `data_flow_confirmed` on every edge."""

    name = "flow_kind"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        for edge in delta.edges:
            if edge.kind in STRUCTURAL_EDGE_KINDS:
                # A declaration, not a path (ADR-0015, decision 5). No
                # resolver emits one today; a guardrail edge that
                # arrives here must not be downgraded to control.
                continue
            decision = FlowDecision(edge.data_flow_confirmed or "unknown")
            edge.flow_kind = flow_kind_for(decision)
        return delta
