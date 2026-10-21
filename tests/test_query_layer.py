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

def test_find_chains_tool_with_sink_capability_is_endpoint(
    store: GraphStore,
) -> None:
    """A tool with executes_code is a valid chain endpoint (ADR-0009)."""
    # Build: entry → tool (executes_code)
    store.nodes.add(Node(
        id="entry:1", kind="entry_point", name="POST /chat",
        host_id="localhost",
    ))
    store.nodes.add(Node(
        id="tool:1", kind="tool", name="execute_python_script",
        host_id="localhost",
        capabilities=["executes_code"],
    ))
    store.edges.add(Edge(
        id="e:1", source_id="entry:1", target_id="tool:1",
        kind="has_access_to", host_id="localhost",
    ))

    chains = store.query.find_chains()
    assert len(chains) == 1
    assert chains[0].nodes == ["entry:1", "tool:1"]


def test_find_chains_tool_without_sink_capability_not_endpoint(
    store: GraphStore,
) -> None:
    """A tool without dangerous capabilities is not a chain endpoint."""
    store.nodes.add(Node(
        id="entry:1", kind="entry_point", name="POST /chat",
        host_id="localhost",
    ))
    store.nodes.add(Node(
        id="tool:1", kind="tool", name="search_docs",
        host_id="localhost",
        capabilities=[],
    ))
    store.edges.add(Edge(
        id="e:1", source_id="entry:1", target_id="tool:1",
        kind="has_access_to", host_id="localhost",
    ))

    chains = store.query.find_chains()
    assert chains == []


def test_find_chains_sink_by_kind_still_works(
    store: GraphStore, sample_graph: dict[str, str],
) -> None:
    """Existing kind='sink' behavior is preserved."""
    chains = store.query.find_chains()
    assert len(chains) == 1


# ============================================================
# ADR-0015 — structural edges and data_edges_only
# ============================================================


def _add_chain_graph(store: GraphStore) -> dict[str, str]:
    """Build a graph with one control-only and one data-flow chain.

        entry --control--> agent --control--> sink:ctl
        entry --control--> agent --data---> sink:dat

    Both paths share the entry -> agent edge; they diverge at the
    agent, whose two out-edges differ only in flow_kind.
    """
    ids = {
        "entry": "entry_point:" + "e" * 16,
        "agent": "agent:" + "a" * 16,
        "sink_ctl": "sink:" + "c" * 16,
        "sink_dat": "sink:" + "d" * 16,
        "e_entry": "edge:" + "0" * 16,
        "e_ctl": "edge:" + "1" * 16,
        "e_dat": "edge:" + "2" * 16,
    }

    store.nodes.upsert(Node(
        id=ids["entry"], kind="entry_point", name="HTTP /chat",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=ids["agent"], kind="agent", name="InvoiceAgent",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=ids["sink_ctl"], kind="sink", name="control_sink",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=ids["sink_dat"], kind="sink", name="data_sink",
        host_id="localhost",
    ))

    store.edges.upsert(Edge(
        id=ids["e_entry"], source_id=ids["entry"], target_id=ids["agent"],
        kind="uses_model", host_id="localhost", flow_kind="control",
    ))
    store.edges.upsert(Edge(
        id=ids["e_ctl"], source_id=ids["agent"], target_id=ids["sink_ctl"],
        kind="has_access_to", host_id="localhost", flow_kind="control",
        data_flow_confirmed="unknown",
    ))
    store.edges.upsert(Edge(
        id=ids["e_dat"], source_id=ids["agent"], target_id=ids["sink_dat"],
        kind="has_access_to", host_id="localhost", flow_kind="data",
        data_flow_confirmed="parameter_match",
    ))

    return ids


def test_find_chains_ignores_structural_edges(store: GraphStore) -> None:
    """Structural edges are declarations, not paths (ADR-0015).

    `guards` is the structural edge kind. No resolver emits it yet, so
    the edge is constructed directly here — the traversal rule is what
    is under test, not the resolver.
    """
    # entry --guards (structural)--> tool --has_access_to--> sink
    # The only route to the sink runs through a structural edge, so
    # no chain may be enumerated — even though every node and edge
    # exists and the sink is reachable if flow_kind were ignored.
    entry, tool, sink = (
        "entry_point:" + "e" * 16,
        "tool:" + "t" * 16,
        "sink:" + "s" * 16,
    )
    structural_edge = "edge:" + "1" * 16

    store.nodes.upsert(Node(
        id=entry, kind="entry_point", name="POST /chat",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=tool, kind="tool", name="read_file", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=sink, kind="sink", name="shell", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id=structural_edge, source_id=entry, target_id=tool,
        kind="guards", host_id="localhost", flow_kind="structural",
    ))
    store.edges.upsert(Edge(
        id="edge:" + "2" * 16, source_id=tool, target_id=sink,
        kind="has_access_to", host_id="localhost", flow_kind="control",
        data_flow_confirmed="unknown",
    ))

    # Sanity: the graph is not empty, so the empty result comes from
    # the structural edge, not from missing data.
    assert store.nodes.count() == 3
    assert store.edges.count() == 2

    chains = store.query.find_chains()
    assert chains == []

    # The structural edge never appears in a chain, in either mode.
    for chain in store.query.find_chains(data_edges_only=True):
        assert structural_edge not in chain.edges


def test_find_chains_traverses_provides_tool(store: GraphStore) -> None:
    """provides_tool is control, so the access path is a chain (ADR-0015).

    entry --delegates_to--> agent --has_access_to--> mcp_server
          --provides_tool--> tool --has_access_to--> sink

    The middle edges are control: the agent can reach the server, and
    the server exposes the tool. Together they form the access path to
    the sink — the thing the analysis is meant to evaluate. The final
    edge carries confirmed data flow.
    """
    ids = {
        "entry": "entry_point:" + "e" * 16,
        "agent": "agent:" + "a" * 16,
        "server": "mcp_server:" + "m" * 16,
        "tool": "tool:" + "t" * 16,
        "sink": "sink:" + "s" * 16,
        "e1": "edge:" + "1" * 16,
        "e2": "edge:" + "2" * 16,
        "e3": "edge:" + "3" * 16,
        "e4": "edge:" + "4" * 16,
    }

    store.nodes.upsert(Node(
        id=ids["entry"], kind="entry_point", name="POST /chat",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=ids["agent"], kind="agent", name="InvoiceAgent",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=ids["server"], kind="mcp_server", name="systemutils",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=ids["tool"], kind="tool", name="delete_file",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=ids["sink"], kind="sink", name="os.remove",
        host_id="localhost",
    ))

    store.edges.upsert(Edge(
        id=ids["e1"], source_id=ids["entry"], target_id=ids["agent"],
        kind="delegates_to", host_id="localhost", flow_kind="control",
        data_flow_confirmed="unknown",
    ))
    store.edges.upsert(Edge(
        id=ids["e2"], source_id=ids["agent"], target_id=ids["server"],
        kind="has_access_to", host_id="localhost", flow_kind="control",
        data_flow_confirmed="unknown",
    ))
    store.edges.upsert(Edge(
        id=ids["e3"], source_id=ids["server"], target_id=ids["tool"],
        kind="provides_tool", host_id="localhost", flow_kind="control",
        data_flow_confirmed="unknown",
    ))
    store.edges.upsert(Edge(
        id=ids["e4"], source_id=ids["tool"], target_id=ids["sink"],
        kind="has_access_to", host_id="localhost", flow_kind="data",
        data_flow_confirmed="parameter_match",
    ))

    chains = store.query.find_chains()
    assert len(chains) == 1
    assert chains[0].nodes == [
        ids["entry"], ids["agent"], ids["server"], ids["tool"], ids["sink"],
    ]
    assert chains[0].edges == [ids["e1"], ids["e2"], ids["e3"], ids["e4"]]

    # The access path carries the data edge, so it survives the
    # high-precision shortlist too.
    assert len(store.query.find_chains(data_edges_only=True)) == 1


def test_find_chains_default_keeps_control_only_chains(
    store: GraphStore,
) -> None:
    """data_edges_only=False (default) returns control-only chains."""
    ids = _add_chain_graph(store)

    chains = store.query.find_chains()
    assert len(chains) == 2

    by_last_node = {c.nodes[-1]: c for c in chains}
    assert set(by_last_node) == {ids["sink_ctl"], ids["sink_dat"]}

    control_chain = by_last_node[ids["sink_ctl"]]
    assert control_chain.edges == [ids["e_entry"], ids["e_ctl"]]


def test_find_chains_data_edges_only_excludes_control_only(
    store: GraphStore,
) -> None:
    """data_edges_only=True keeps only chains with a data edge."""
    ids = _add_chain_graph(store)

    chains = store.query.find_chains(data_edges_only=True)
    assert len(chains) == 1

    chain = chains[0]
    assert chain.nodes == [ids["entry"], ids["agent"], ids["sink_dat"]]
    assert chain.edges == [ids["e_entry"], ids["e_dat"]]


def test_find_chains_data_edges_only_without_data_edges_is_empty(
    store: GraphStore,
) -> None:
    """A control-only graph yields nothing when data_edges_only=True."""
    # entry --uses_model--> agent --has_access_to--> sink, all control.
    entry, agent, sink = (
        "entry_point:" + "e" * 16,
        "agent:" + "a" * 16,
        "sink:" + "s" * 16,
    )
    store.nodes.upsert(Node(
        id=entry, kind="entry_point", name="POST /chat",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=agent, kind="agent", name="InvoiceAgent", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=sink, kind="sink", name="shell", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id="edge:" + "1" * 16, source_id=entry, target_id=agent,
        kind="uses_model", host_id="localhost", flow_kind="control",
        data_flow_confirmed="unknown",
    ))
    store.edges.upsert(Edge(
        id="edge:" + "2" * 16, source_id=agent, target_id=sink,
        kind="has_access_to", host_id="localhost", flow_kind="control",
        data_flow_confirmed="unknown",
    ))

    # The control chain is enumerable by default...
    assert len(store.query.find_chains()) == 1
    # ...but not on the data-only shortlist.
    assert store.query.find_chains(data_edges_only=True) == []


def test_find_chains_data_edge_mid_path_qualifies(store: GraphStore) -> None:
    """A data edge anywhere in the chain is enough (not just the last)."""
    # entry --has_access_to (data)--> agent --uses_model (control)--> sink
    entry, agent, sink = (
        "entry_point:" + "e" * 16,
        "agent:" + "a" * 16,
        "sink:" + "s" * 16,
    )
    e_data = "edge:" + "1" * 16
    e_control = "edge:" + "2" * 16
    store.nodes.upsert(Node(
        id=entry, kind="entry_point", name="POST /chat",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=agent, kind="agent", name="InvoiceAgent", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=sink, kind="sink", name="shell", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id=e_data, source_id=entry, target_id=agent,
        kind="has_access_to", host_id="localhost", flow_kind="data",
        data_flow_confirmed="parameter_match",
    ))
    store.edges.upsert(Edge(
        id=e_control, source_id=agent, target_id=sink,
        kind="uses_model", host_id="localhost", flow_kind="control",
        data_flow_confirmed="unknown",
    ))

    chains = store.query.find_chains(data_edges_only=True)
    assert len(chains) == 1
    assert chains[0].edges == [e_data, e_control]

