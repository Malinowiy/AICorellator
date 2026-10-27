"""Check 2 — `is_chain_typed`: an entry point at one end, a sink at the
other.

Two rules define a sink and A.4 uses both, the same two `find_chains`
enumerates endpoints by: the kind is `sink`, or the node carries one of
`patterns.SINK_CAPABILITIES` (ADR-0009, ADR-0014). Nothing in between is
checked — the middle of a chain is where the work happens.
"""

from __future__ import annotations

from aicorellator import GraphStore

from .conftest import Graph


def test_entry_point_to_sink_is_typed(
    store: GraphStore, graph: Graph
) -> None:
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", entry, agent)
    e2 = graph.edge("e2", agent, sink)

    assert store.query.is_chain_typed(
        [entry, agent, sink], [e1, e2]
    ) is True


def test_a_first_node_that_is_not_an_entry_point_is_not_typed(
    store: GraphStore, graph: Graph
) -> None:
    """The head of the chain is the claim A.4 checks.

    This is the candidate `find_chains` may still return for a path
    starting at an `untrusted_source`; enumeration is not validation.
    """
    source = graph.node("inbox", "untrusted_source")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", source, sink)

    assert store.query.is_chain_typed([source, sink], [e1]) is False


def test_a_last_node_that_is_not_a_sink_is_not_typed(
    store: GraphStore, graph: Graph
) -> None:
    entry = graph.node("entry", "entry_point")
    tool = graph.node("tool", "tool")          # no sink capability
    e1 = graph.edge("e1", entry, tool)

    assert store.query.is_chain_typed([entry, tool], [e1]) is False


def test_a_sink_capability_makes_the_last_node_a_sink(
    store: GraphStore, graph: Graph
) -> None:
    """`http_egress` on a `tool` is an endpoint (ADR-0009)."""
    entry = graph.node("entry", "entry_point")
    tool = graph.node("egress", "tool", capabilities=["http_egress"])
    e1 = graph.edge("e1", entry, tool)

    assert store.query.is_chain_typed([entry, tool], [e1]) is True


def test_writes_db_makes_the_last_node_a_sink(
    store: GraphStore, graph: Graph
) -> None:
    """The second capability family, so the rule is not one special case."""
    entry = graph.node("entry", "entry_point")
    tool = graph.node("store", "tool", capabilities=["writes_db"])
    e1 = graph.edge("e1", entry, tool)

    assert store.query.is_chain_typed([entry, tool], [e1]) is True


def test_intermediate_kinds_are_not_checked(
    store: GraphStore, graph: Graph
) -> None:
    """A chain through a `tool` — and another `entry_point` — is typed.

    Only the ends carry the claim. A hand-built chain through a second
    entry point is unusual, not a construction error: `find_chains`
    already refuses to revisit a node, so it cannot be produced by
    enumeration.
    """
    entry = graph.node("entry", "entry_point")
    tool = graph.node("tool", "tool")
    other = graph.node("route", "entry_point")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", entry, tool)
    e2 = graph.edge("e2", tool, other)
    e3 = graph.edge("e3", other, sink)

    assert store.query.is_chain_typed(
        [entry, tool, other, sink], [e1, e2, e3]
    ) is True


def test_a_missing_end_node_is_not_typed(
    store: GraphStore, graph: Graph
) -> None:
    """A node that is not in the graph carries no kind at all."""
    entry = graph.node("entry", "entry_point")

    assert store.query.is_chain_typed(
        [entry, "sink:absent"], ["edge:x"]
    ) is False
