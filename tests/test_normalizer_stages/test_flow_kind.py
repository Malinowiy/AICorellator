"""FlowKindStage: `flow_kind = "control"` on every AST edge (ADR-0016)."""

from __future__ import annotations

import pytest

from aicorellator.graph.models import Edge
from aicorellator.parsing.normalizer_stages import FlowKindStage


def _edge(edge_id: str, kind: str, confirmed: str | None) -> Edge:
    return Edge(
        id=edge_id, source_id="a", target_id="b", kind=kind,
        host_id="localhost", data_flow_confirmed=confirmed,
    )


@pytest.mark.parametrize(
    "confirmed",
    [
        "parameter_match",
        "local_var_match",
        "call_result_match",
        "literal_only",
        "no_args",
        "unknown",
        None,
    ],
)
def test_flow_kind_is_control_whatever_the_decision(
    project, confirmed,
) -> None:
    """No value of `data_flow_confirmed` yields `flow_kind = "data"`.

    The stage does not read the column any more (ADR-0016, decision 2).
    The values here are the six of ADR-0015 plus the `NULL` an AST edge
    actually carries: only a runtime observation can make an edge a data
    edge, and that is Layer 2's write.
    """
    p = project()
    p.delta.edges.append(
        _edge("edge:1", "has_access_to", confirmed)
    )

    delta = FlowKindStage().apply(p.delta, p.ctx)

    assert delta.edges[0].flow_kind == "control"
    # The stage sets flow_kind; it does not touch the decision.
    assert delta.edges[0].data_flow_confirmed == confirmed


def test_structural_edges_are_left_alone(project) -> None:
    """`guards` is a declaration, not a path (ADR-0015, decision 5)."""
    p = project()
    edge = _edge("edge:1", "guards", "unknown")
    edge.flow_kind = "structural"
    p.delta.edges.append(edge)

    delta = FlowKindStage().apply(p.delta, p.ctx)

    assert delta.edges[0].flow_kind == "structural"
