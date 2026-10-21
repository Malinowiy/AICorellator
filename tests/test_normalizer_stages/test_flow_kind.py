"""FlowKindStage: `flow_kind` derived from `data_flow_confirmed`."""

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
    ("confirmed", "expected"),
    [
        ("parameter_match", "data"),
        ("local_var_match", "data"),
        ("call_result_match", "data"),
        ("literal_only", "control"),
        ("no_args", "control"),
        ("unknown", "control"),
        (None, "control"),
    ],
)
def test_flow_kind_follows_the_decision(project, confirmed, expected) -> None:
    """One mapping, applied to every edge (ADR-0015, decision 3)."""
    p = project()
    p.delta.edges.append(
        _edge("edge:1", "has_access_to", confirmed)
    )

    delta = FlowKindStage().apply(p.delta, p.ctx)

    assert delta.edges[0].flow_kind == expected
    # The stage derives flow_kind; it does not touch the decision.
    assert delta.edges[0].data_flow_confirmed == confirmed


def test_structural_edges_are_left_alone(project) -> None:
    """`guards` is a declaration, not a path (ADR-0015, decision 5)."""
    p = project()
    edge = _edge("edge:1", "guards", "unknown")
    edge.flow_kind = "structural"
    p.delta.edges.append(edge)

    delta = FlowKindStage().apply(p.delta, p.ctx)

    assert delta.edges[0].flow_kind == "structural"
