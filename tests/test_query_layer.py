"""Smoke tests: Query Layer — find_chains, validate_edges, get_neighbors."""

from __future__ import annotations

from aicorellator import GraphStore
from aicorellator.graph import Edge, Node


def test_find_chains_entry_to_sink(
    store: GraphStore, sample_graph: dict[str, str]
) -> None:
    chains = store.query.find_chains()
    assert len(chains) == 1

    chain = chains[0]
    assert chain.nodes[0] == sample_graph["entry"]
    assert chain.nodes[-1] == sample_graph["sink"]
    assert chain.edges == [sample_graph["e1"], sample_graph["e2"]]
    assert chain.length == 2


def test_find_chains_max_hops(
    store: GraphStore, sample_graph: dict[str, str]
) -> None:
    chains = store.query.find_chains(max_hops=1)
    assert chains == []


def test_find_chains_no_entry(store: GraphStore) -> None:
    store.nodes.add(Node(id="n:1", kind="agent", name="A", host_id="localhost"))
    assert store.query.find_chains() == []


def test_find_chains_respects_max_candidates(
    store: GraphStore, sample_graph: dict[str, str]
) -> None:
    chains = store.query.find_chains(max_candidates=1)
    assert len(chains) <= 1


def test_get_neighbors_depth_one(
    store: GraphStore, sample_graph: dict[str, str]
) -> None:
    slice_ = store.query.get_neighbors(sample_graph["agent"], depth=1)
    node_ids = {n.id for n in slice_.nodes}
    assert node_ids == {sample_graph["entry"], sample_graph["agent"], sample_graph["sink"]}


def test_get_neighbors_depth_zero_raises(store: GraphStore, sample_graph: dict[str, str]) -> None:
    import pytest
    with pytest.raises(ValueError):
        store.query.get_neighbors(sample_graph["agent"], depth=0)


def test_validate_edges_all_exist(
    store: GraphStore, sample_graph: dict[str, str]
) -> None:
    existing, missing = store.query.validate_edges(
        [sample_graph["e1"], sample_graph["e2"]]
    )
    assert existing == [sample_graph["e1"], sample_graph["e2"]]
    assert missing == []


def test_validate_edges_missing(
    store: GraphStore, sample_graph: dict[str, str]
) -> None:
    existing, missing = store.query.validate_edges(
        [sample_graph["e1"], "edge:nonexistent"]
    )
    assert existing == [sample_graph["e1"]]
    assert missing == ["edge:nonexistent"]


def test_is_chain_valid(
    store: GraphStore, sample_graph: dict[str, str]
) -> None:
    assert store.query.is_chain_valid(
        chain_nodes=[sample_graph["entry"], sample_graph["agent"], sample_graph["sink"]],
        chain_edges=[sample_graph["e1"], sample_graph["e2"]],
    ) is True


def test_is_chain_valid_hallucination(
    store: GraphStore, sample_graph: dict[str, str]
) -> None:
    assert store.query.is_chain_valid(
        chain_nodes=[sample_graph["entry"], sample_graph["agent"]],
        chain_edges=[sample_graph["e1"], "edge:fake"],
    ) is False


def test_get_subgraph_by_kind(store: GraphStore, sample_graph: dict[str, str]) -> None:
    slice_ = store.query.get_subgraph(node_kinds=["agent", "sink"])
    node_kinds = {n.kind for n in slice_.nodes}
    assert node_kinds == {"agent", "sink"}