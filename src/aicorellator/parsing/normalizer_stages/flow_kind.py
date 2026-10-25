"""FlowKindStage — `flow_kind` on every AST edge (ADR-0016).

Run order: 12 of 18 (see `normalizer_stages.STAGES`).

Every edge the AST layer produces is a *control* edge. The AST knows
that one symbol reaches another; it does not know whether data moves
along that path, and after ADR-0016 it does not pretend to:

    flow_kind = "control"   on every edge this stage sees

`data_flow_confirmed` is neither read nor written here. It stays `NULL`
on AST edges and is populated only by Layer 2, which runs the `flow/`
analyzers against a `(tool_func, sink_call)` pair that runtime observed
(ADR-0016, decision 3). `flow.decision.flow_kind_for` remains the single
implementation of the value → kind mapping, and on Layer 1a nothing
calls it: the kind is `control` unconditionally.

A structural edge (`guards`) is a declaration, not a path, and is left
alone (ADR-0015, decision 5). No resolver emits one today; a guardrail
edge that arrives here must not be downgraded to control.

Every resolver that runs after this stage also constructs control edges,
and none of them writes `data_flow_confirmed`, so the whole AST graph is
`control` with a `NULL` flow confirmation, except for structural edges.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...patterns import STRUCTURAL_EDGE_KINDS
from ..normalizer_context import NormalizeContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class FlowKindStage:
    """Set `flow_kind = "control"` on every AST edge (ADR-0016)."""

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
            edge.flow_kind = "control"
        return delta
