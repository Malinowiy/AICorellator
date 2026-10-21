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


# ============================================================
# ADR-0010 — chain groups through untrusted sources (1.3b)
# ============================================================
#
# The write side of a group has no producer yet: 1.3a emits
# `source → reader` edges only, and nothing emits `writer → source`
# (that is 1.3a.1). So these tests build both sides by hand, which is
# what the enumerator works on — it reads the graph, it does not
# produce edges, exactly like `find_chains`.


def _put(
    store: GraphStore,
    node_id: str,
    kind: str,
    name: str,
    capabilities: list[str] | None = None,
) -> str:
    store.nodes.upsert(Node(
        id=node_id, kind=kind, name=name, host_id="localhost",
        capabilities=capabilities or [],
    ))
    return node_id


def _link(
    store: GraphStore,
    edge_id: str,
    source: str,
    target: str,
    flow_kind: str = "control",
    kind: str = "has_access_to",
) -> str:
    store.edges.upsert(Edge(
        id=edge_id, source_id=source, target_id=target, kind=kind,
        host_id="localhost", flow_kind=flow_kind,
    ))
    return edge_id


def _group_graph(store: GraphStore) -> dict[str, str]:
    """One write chain and one read chain around one untrusted source.

        entry --has_access_to--> writer --has_access_to(data)--> source
        source --has_access_to(data)--> reader --has_access_to--> sink

    The shape of ADR-0010's scenario with the names changed: a chain
    that writes into a retrieval corpus, and a chain that reads it on
    its way to a shell.
    """
    ids = {
        "entry": "entry_point:" + "e" * 16,
        "writer": "tool:" + "w" * 16,
        "source": "untrusted_source:" + "u" * 16,
        "reader": "tool:" + "r" * 16,
        "sink": "sink:" + "s" * 16,
        "e_w": "edge:" + "1" * 16,
        "w_s": "edge:" + "2" * 16,
        "s_r": "edge:" + "3" * 16,
        "r_k": "edge:" + "4" * 16,
    }
    _put(store, ids["entry"], "entry_point", "POST /upload")
    _put(store, ids["writer"], "tool", "save_document")
    _put(
        store, ids["source"], "untrusted_source",
        "rag_corpus:vectorstore.search",
    )
    _put(store, ids["reader"], "tool", "search_documents")
    _put(store, ids["sink"], "sink", "run_shell")
    _link(store, ids["e_w"], ids["entry"], ids["writer"])
    _link(store, ids["w_s"], ids["writer"], ids["source"], flow_kind="data")
    _link(store, ids["s_r"], ids["source"], ids["reader"], flow_kind="data")
    _link(store, ids["r_k"], ids["reader"], ids["sink"])
    return ids


def test_find_chain_groups_no_sources_is_empty(store: GraphStore) -> None:
    assert store.query.find_chain_groups() == []


def test_find_chain_groups_pairs_one_write_with_one_read(
    store: GraphStore,
) -> None:
    ids = _group_graph(store)

    groups = store.query.find_chain_groups()

    assert len(groups) == 1
    group = groups[0]
    assert group.untrusted_source_id == ids["source"]
    assert group.untrusted_source_name == "rag_corpus:vectorstore.search"
    # The write chain reads entry-first and ends at the anchor...
    assert group.write_chains == [[ids["entry"], ids["writer"], ids["source"]]]
    # ...and the read chain starts at it and ends at a sink.
    assert group.read_chains == [[ids["source"], ids["reader"], ids["sink"]]]
    assert len(group.write_chain_ids) == 1
    assert len(group.read_chain_ids) == 1


def test_find_chain_groups_ids_are_pinned_and_prefixed(
    store: GraphStore,
) -> None:
    """The ids are the ADR-0010 formula's output, pinned as literals.

    The formula is part of the contract (the same graph must always
    produce the same ids, so a group can be correlated across runs and
    versions), so changing one of these is a deliberate change of
    identity, not a detail. Here, on `_group_graph`:

        write chain  chain:8a014033edae9d10   entry → writer → source
        read chain   chain:d84f733f00586450   source → reader → sink
        group        chain_group:ae20cbe62d972466
    """
    _group_graph(store)
    group = store.query.find_chain_groups()[0]

    assert group.write_chain_ids == ["chain:8a014033edae9d10"]
    assert group.read_chain_ids == ["chain:d84f733f00586450"]
    assert group.group_id == "chain_group:ae20cbe62d972466"


def test_find_chain_groups_read_chain_must_reach_a_sink(
    store: GraphStore,
) -> None:
    """A read side that stops at a harmless tool is not a group."""
    ids = _group_graph(store)
    # Cut the read chain before the sink: source → reader, full stop.
    store.edges.delete(ids["r_k"])

    assert store.query.find_chain_groups() == []


def test_find_chain_groups_write_chain_must_start_at_an_entry(
    store: GraphStore,
) -> None:
    """A writer that no entry point can reach is not a write chain."""
    ids = _group_graph(store)
    store.edges.delete(ids["e_w"])

    assert store.query.find_chain_groups() == []


def test_find_chain_groups_write_only_source_produces_nothing(
    store: GraphStore,
) -> None:
    ids = _group_graph(store)
    store.edges.delete(ids["s_r"])
    store.edges.delete(ids["r_k"])

    assert store.query.find_chain_groups() == []


def test_find_chain_groups_read_only_source_produces_nothing(
    store: GraphStore,
) -> None:
    ids = _group_graph(store)
    store.edges.delete(ids["e_w"])
    store.edges.delete(ids["w_s"])

    assert store.query.find_chain_groups() == []


def test_find_chain_groups_is_the_cartesian_product(store: GraphStore) -> None:
    """Two writes and three reads around one source make six groups."""
    ids = _group_graph(store)
    # A second write chain: another entry point reaching the same writer.
    _put(store, "entry_point:" + "f" * 16, "entry_point", "POST /import")
    _link(store, "edge:" + "5" * 16, "entry_point:" + "f" * 16, ids["writer"])
    # Two more read chains: the reader reaches two more sinks directly.
    for suffix, name in (("a", "delete_files"), ("b", "send_email")):
        sink = "sink:" + suffix * 16
        _put(store, sink, "sink", name)
        _link(store, "edge:" + suffix * 16, ids["reader"], sink)

    groups = store.query.find_chain_groups()

    assert len(groups) == 2 * 3
    # Every group is anchored on the one source, and every pair is
    # distinct: the ids are a set of the same size.
    assert {g.untrusted_source_id for g in groups} == {ids["source"]}
    assert len({g.group_id for g in groups}) == 6
    assert len({(g.write_chain_ids[0], g.read_chain_ids[0]) for g in groups}) == 6


def test_find_chain_groups_max_groups_caps_the_total(
    store: GraphStore,
) -> None:
    _group_graph(store)
    groups = store.query.find_chain_groups(max_groups=1)
    assert len(groups) == 1


def test_find_chain_groups_write_cap_bounds_the_write_side(
    store: GraphStore,
) -> None:
    ids = _group_graph(store)
    _put(store, "entry_point:" + "f" * 16, "entry_point", "POST /import")
    _link(store, "edge:" + "5" * 16, "entry_point:" + "f" * 16, ids["writer"])

    assert len(store.query.find_chain_groups()) == 2
    capped = store.query.find_chain_groups(max_write_chains_per_source=1)
    assert len(capped) == 1
    assert store.query.find_chain_groups(
        max_write_chains_per_source=0
    ) == []


def test_find_chain_groups_read_cap_bounds_the_read_side(
    store: GraphStore,
) -> None:
    ids = _group_graph(store)
    sink = "sink:" + "a" * 16
    _put(store, sink, "sink", "delete_files")
    _link(store, "edge:" + "a" * 16, ids["reader"], sink)

    assert len(store.query.find_chain_groups()) == 2
    capped = store.query.find_chain_groups(max_read_chains_per_source=1)
    assert len(capped) == 1
    assert store.query.find_chain_groups(
        max_read_chains_per_source=0
    ) == []


def test_find_chain_groups_max_hops_bounds_each_side(
    store: GraphStore,
) -> None:
    """Both chains here are two hops, so one hop admits neither."""
    _group_graph(store)

    assert store.query.find_chain_groups(max_hops=1) == []
    assert len(store.query.find_chain_groups(max_hops=2)) == 1


def test_find_chain_groups_rejects_a_hop_budget_below_one(
    store: GraphStore,
) -> None:
    import pytest

    with pytest.raises(ValueError):
        store.query.find_chain_groups(max_hops=0)


def test_find_chain_groups_rejects_a_negative_cap(store: GraphStore) -> None:
    import pytest

    with pytest.raises(ValueError):
        store.query.find_chain_groups(max_groups=-1)


def test_find_chain_groups_is_deterministic(store: GraphStore) -> None:
    """Same graph, same ids, same order — twice and after a re-open."""
    ids = _group_graph(store)
    _put(store, "entry_point:" + "f" * 16, "entry_point", "POST /import")
    _link(store, "edge:" + "5" * 16, "entry_point:" + "f" * 16, ids["writer"])
    sink = "sink:" + "a" * 16
    _put(store, sink, "sink", "delete_files")
    _link(store, "edge:" + "a" * 16, ids["reader"], sink)

    first = store.query.find_chain_groups()
    second = store.query.find_chain_groups()

    assert [g.to_dict() for g in first] == [g.to_dict() for g in second]
    # Ordered by source, then write chain id, then read chain id.
    keys = [
        (g.untrusted_source_id, g.write_chain_ids[0], g.read_chain_ids[0])
        for g in first
    ]
    assert keys == sorted(keys)


def test_find_chain_groups_survives_cycles_on_both_sides(
    store: GraphStore,
) -> None:
    """A cycle in either side must not hang the DFS or repeat nodes."""
    ids = _group_graph(store)
    # writer → source → reader → writer: a cycle spanning the anchor.
    _link(store, "edge:" + "9" * 16, ids["reader"], ids["writer"])

    groups = store.query.find_chain_groups()

    assert len(groups) == 1
    for group in groups:
        assert group.write_chains == [[
            ids["entry"], ids["writer"], ids["source"],
        ]]
        assert group.read_chains == [[
            ids["source"], ids["reader"], ids["sink"],
        ]]


def test_find_chain_groups_ignores_structural_edges(
    store: GraphStore,
) -> None:
    """`guards` is a declaration, not a write path (ADR-0015, decision 5)."""
    ids = _group_graph(store)
    store.edges.upsert(Edge(
        id="edge:" + "8" * 16, source_id=ids["entry"], target_id=ids["source"],
        kind="guards", host_id="localhost", flow_kind="structural",
    ))
    store.edges.delete(ids["e_w"])
    store.edges.delete(ids["w_s"])

    assert store.query.find_chain_groups() == []


def test_find_chain_groups_sink_by_capability_ends_a_read_chain(
    store: GraphStore,
) -> None:
    """A tool with executes_code is a sink (ADR-0009), as in find_chains."""
    ids = _group_graph(store)
    store.edges.delete(ids["r_k"])
    _put(store, ids["reader"], "tool", "search_documents",
         capabilities=["executes_code"])

    groups = store.query.find_chain_groups()

    assert len(groups) == 1
    assert groups[0].read_chains == [[
        ids["source"], ids["reader"],
    ]]


def test_find_chain_groups_does_not_pass_through_the_anchor(
    store: GraphStore,
) -> None:
    """The anchor terminates a write chain and starts a read chain.

    A read chain may not double back through its own source: the
    channel is the anchor, not a hop. Without the visited-set rule the
    extra edges below would add `source → spare → source → reader →
    sink` — a path that counts the same channel twice. The legitimate
    `source → spare → sink` stays.
    """
    ids = _group_graph(store)
    spare = "tool:" + "p" * 16
    sink = "sink:" + "q" * 16
    _put(store, spare, "tool", "reindex")
    _put(store, sink, "sink", "delete_files")
    _link(store, "edge:" + "6" * 16, ids["source"], spare)
    _link(store, "edge:" + "7" * 16, spare, ids["source"])
    _link(store, "edge:" + "8" * 16, spare, sink)

    groups = store.query.find_chain_groups()

    assert {tuple(g.read_chains[0]) for g in groups} == {
        (ids["source"], ids["reader"], ids["sink"]),
        (ids["source"], spare, sink),
    }
    # No chain visits the anchor more than once.
    assert all(
        [ids["source"]] == [node for node in chain if node == ids["source"]]
        for g in groups
        for chain in g.read_chains + g.write_chains
    )


def test_find_chain_groups_orders_by_source_then_chains(
    store: GraphStore,
) -> None:
    """Two sources: groups come out grouped by source id, then by ids."""
    ids = _group_graph(store)
    other_source = "untrusted_source:" + "v" * 16
    _put(store, other_source, "untrusted_source", "email_inbox:imap.search")
    _link(store, "edge:" + "b" * 16, ids["entry"], other_source)
    _link(store, "edge:" + "c" * 16, other_source, ids["reader"])

    groups = store.query.find_chain_groups()

    assert len(groups) == 2
    assert [g.untrusted_source_id for g in groups] == sorted(
        [ids["source"], other_source]
    )
    # Each source's write chain is the one that ends at it.
    for group in groups:
        assert group.write_chains[0][-1] == group.untrusted_source_id
        assert group.read_chains[0][0] == group.untrusted_source_id


def test_find_chain_groups_is_read_only(store: GraphStore) -> None:
    """An enumerator, not a writer: no table moves (1.3b, scope note).

    The `chain_groups` table exists and stays empty. Its columns carry
    evaluation results (`verdict`, `score`, `lethal_trifecta`, ...) that
    belong to 1.3c-1.3e, so a half-filled row now would need a second
    migration later; the enumerator returns in-memory `ChainGroup`s and
    the writer is a separate step.
    """
    ids = _group_graph(store)
    _put(store, "entry_point:" + "f" * 16, "entry_point", "POST /import")
    _link(store, "edge:" + "5" * 16, "entry_point:" + "f" * 16, ids["writer"])

    before = (
        store.nodes.count(),
        store.edges.count(),
        len(store.query.find_chains()),
    )

    assert store.query.find_chain_groups()

    after = (
        store.nodes.count(),
        store.edges.count(),
        len(store.query.find_chains()),
    )
    assert before == after
    assert store._conn.execute(
        "SELECT COUNT(*) AS n FROM chain_groups"
    ).fetchone()["n"] == 0

