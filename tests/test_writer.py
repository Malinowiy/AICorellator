"""Smoke tests: Graph Writer — writes GraphDelta into GraphStore."""

from __future__ import annotations

import pytest

from aicorellator import GraphStore
from aicorellator.graph import Edge, Node
from aicorellator.parsing.ast_normalizer import GraphDelta
from aicorellator.parsing.writer import set_phase1_done, write


@pytest.fixture
def delta() -> GraphDelta:
    return GraphDelta(
        nodes=[
            Node(id="agent:1", kind="agent", name="A", host_id="localhost"),
            Node(id="tool:1", kind="tool", name="T", host_id="localhost"),
        ],
        edges=[
            Edge(
                id="edge:1",
                source_id="agent:1",
                target_id="tool:1",
                kind="has_access_to",
                host_id="localhost",
            ),
        ],
    )


def test_write_inserts_nodes_and_edges(store: GraphStore, delta: GraphDelta) -> None:
    result = write(store, delta)
    assert result["nodes_added"] == 2
    assert result["edges_added"] == 1
    assert store.nodes.exists("agent:1")
    assert store.edges.exists("edge:1")


def test_write_dedupes_on_second_call(store: GraphStore, delta: GraphDelta) -> None:
    write(store, delta)
    result = write(store, delta)
    assert result["nodes_added"] == 0
    assert result["nodes_updated"] == 2
    assert result["edges_added"] == 0
    assert result["edges_updated"] == 1


def test_set_phase1_done(store: GraphStore) -> None:
    assert store.state.get_bool("localhost", "phase1_done") is False
    set_phase1_done(store)
    assert store.state.get_bool("localhost", "phase1_done") is True