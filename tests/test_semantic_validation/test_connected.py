"""Check 1 — `is_chain_connected`: edge `i` links node `i` to node `i+1`.

A chain is a path. Existing between the right two nodes is not enough:
the edge must *run* from one to the other, in order, one edge per hop.
"""

from __future__ import annotations

from aicorellator import GraphStore

from .conftest import Graph


def test_a_path_that_links_its_nodes_is_connected(
    store: GraphStore, graph: Graph
) -> None:
    """entry --e1--> agent --e2--> sink is connected."""
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", entry, agent)
    e2 = graph.edge("e2", agent, sink)

    assert store.query.is_chain_connected(
        [entry, agent, sink], [e1, e2]
    ) is True


def test_a_missing_edge_is_not_connected(
    store: GraphStore, graph: Graph
) -> None:
    """An edge that is not in the graph does not link anything."""
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")

    assert store.query.is_chain_connected(
        [entry, agent], ["edge:not-in-the-graph"]
    ) is False


def test_an_edge_in_the_wrong_direction_is_not_connected(
    store: GraphStore, graph: Graph
) -> None:
    """A real edge that runs the other way is the A.4 bug, pinned.

    `sink --e1--> entry` exists, so the old `is_chain_valid` accepted
    `[entry, sink]` as a chain. It is not one: the hop it claims runs
    backwards. Both ends are typed correctly, so the direction is the
    only thing wrong with it.
    """
    entry = graph.node("entry", "entry_point")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", sink, entry, kind="uses_model")

    assert store.query.is_chain_connected([entry, sink], [e1]) is False

    _, _, notes = store.query.validate_chain([entry, sink], [e1])
    assert notes == [
        f"disconnected: edge {e1} links {sink} → {entry}, "
        f"expected {entry} → {sink}"
    ]


def test_a_chain_with_a_hole_is_not_connected(
    store: GraphStore, graph: Graph
) -> None:
    """`len(chain_edges)` must be `len(chain_nodes) - 1`."""
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", entry, agent)
    graph.edge("e2", agent, sink)          # exists, but is not in the chain

    assert store.query.is_chain_connected(
        [entry, agent, sink], [e1]
    ) is False


def test_an_edge_count_that_is_too_long_is_not_connected(
    store: GraphStore, graph: Graph
) -> None:
    """Extra edges are as wrong as missing ones."""
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    e1 = graph.edge("e1", entry, agent)
    e2 = graph.edge("e2", agent, entry, kind="uses_model")

    assert store.query.is_chain_connected(
        [entry, agent], [e1, e2]
    ) is False


def test_a_chain_that_revisits_a_node_is_not_connected(
    store: GraphStore, graph: Graph
) -> None:
    """A hop that claims to loop back to the node it left is not a hop.

    A real self-loop cannot be stored — the schema rejects
    `source_id = target_id` (`test_edge_self_loop_rejected`) — so the
    shape available is a chain that claims `[a, a]` over an edge that
    leaves `a`. `find_chains` refuses to revisit a node, so this is a
    hand-built chain; Layer 3 is the last line that can catch it.
    """
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", agent, sink)

    assert store.query.is_chain_connected([agent, agent], [e1]) is False


def test_an_empty_chain_is_not_connected(
    store: GraphStore, graph: Graph
) -> None:
    """No nodes, no path."""
    assert store.query.is_chain_connected([], []) is False


def test_every_enumerated_candidate_is_connected(
    store: GraphStore, graph: Graph
) -> None:
    """`find_chains` walks edges forward, so its candidates are connected.

    This is why A.4's direction check is not a second pass over
    enumeration: a candidate the enumerator produced cannot be
    disconnected, because `_dfs_paths` records `edge.id` only for an
    edge whose `source_id` is the node it is standing on and whose
    `target_id` becomes the next node. The check is for chains that
    arrive any other way — a hand-built candidate, a re-evaluated
    stored chain, or a future enumerator.
    """
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    graph.edge("e1", entry, agent)
    graph.edge("e2", agent, sink)
    graph.edge("back", sink, agent, kind="uses_model")   # a backwards edge

    candidates = store.query.find_chains()
    assert len(candidates) == 1

    for candidate in candidates:
        assert store.query.is_chain_connected(
            candidate.nodes, candidate.edges
        ) is True
