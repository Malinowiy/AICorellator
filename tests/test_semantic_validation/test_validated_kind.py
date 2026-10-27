"""`validated` and `validated_kind`: exists is not the same as coherent.

A.4 changes what `validated` means. It was "every node and every edge
exists"; it is now "exists *and* is structurally coherent". The
`validated_kind` field records the rule level the chain reached, so a
chain that exists and is incoherent is `validated = False` with kind
`"existence"` — it exists, it is not a chain.
"""

from __future__ import annotations

from aicorellator import GraphStore

from .conftest import Graph


def test_a_coherent_chain_is_semantic(
    store: GraphStore, graph: Graph
) -> None:
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", entry, agent)
    e2 = graph.edge("e2", agent, sink)

    assert store.query.validate_chain(
        [entry, agent, sink], [e1, e2]
    ) == (True, "semantic", [])


def test_a_disconnected_chain_exists_but_is_not_semantic(
    store: GraphStore, graph: Graph
) -> None:
    """Existence passes, connection fails: kind stays `"existence"`.

    Both ends are typed correctly, so the direction is the only thing
    wrong: one note, and the chain is rejected anyway.
    """
    entry = graph.node("entry", "entry_point")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", sink, entry, kind="uses_model")

    ok, kind, notes = store.query.validate_chain([entry, sink], [e1])

    assert ok is False
    assert kind == "existence"
    assert notes == [
        f"disconnected: edge {e1} links {sink} → {entry}, "
        f"expected {entry} → {sink}"
    ]


def test_every_failed_check_is_reported(
    store: GraphStore, graph: Graph
) -> None:
    """One note per failure, in check order: the shape the report prints.

    `entry --e1?--> agent --e2--> tool` where `e1` runs the wrong way and
    `tool` is not a sink. Both are true at once, and a reader of the
    report is owed both.
    """
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    tool = graph.node("tool", "tool")
    e1 = graph.edge("e1", agent, entry, kind="uses_model")
    e2 = graph.edge("e2", agent, tool)

    ok, kind, notes = store.query.validate_chain(
        [entry, agent, tool], [e1, e2]
    )

    assert ok is False
    assert kind == "existence"
    assert notes == [
        f"disconnected: edge {e1} links {agent} → {entry}, "
        f"expected {entry} → {agent}",
        f"last node {tool} is not a sink",
    ]


def test_a_hallucinated_chain_is_rejected_on_existence(
    store: GraphStore, graph: Graph
) -> None:
    """Existence is checked first and alone: one note, not three."""
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    e1 = graph.edge("e1", entry, agent)

    ok, kind, notes = store.query.validate_chain(
        [entry, agent, "sink:absent"], [e1, "edge:absent"]
    )

    assert ok is False
    assert kind == "existence"
    assert notes == ["missing node sink:absent", "missing edge edge:absent"]


def test_the_boolean_wrapper_agrees_with_the_tuple(
    store: GraphStore, graph: Graph
) -> None:
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", agent, entry, kind="uses_model")
    e2 = graph.edge("e2", agent, sink)

    nodes = [entry, agent, sink]
    edges = [e1, e2]

    assert store.query.is_chain_valid(nodes, edges) is False
    assert store.query.validate_chain(nodes, edges)[0] is False


def test_a_reversed_hop_that_used_to_pass_now_fails(
    store: GraphStore, graph: Graph
) -> None:
    """The A.4 bug, pinned end to end.

    Both edges exist, so Layer 3 before A.4 accepted this chain; the hop
    it claims runs backwards. `validate_edges` still says every edge
    exists — which is exactly why existence alone was not enough.
    """
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", agent, entry, kind="uses_model")
    e2 = graph.edge("e2", agent, sink)

    existing, missing = store.query.validate_edges([e1, e2])
    assert (existing, missing) == ([e1, e2], [])

    ok, kind, notes = store.query.validate_chain(
        [entry, agent, sink], [e1, e2]
    )
    assert ok is False
    assert kind == "existence"
    assert notes[0].startswith("disconnected: ")
