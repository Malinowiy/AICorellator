"""Smoke tests: Query Layer — find_chains, validate_edges, get_neighbors."""

from __future__ import annotations

from aicorellator import GraphStore
from aicorellator.graph import Edge, Node
from aicorellator.graph.query import SCORE_WRITES_DB


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
# ADR-0015/ADR-0016 — structural edges and data_edges_only
#
# `data_edges_only` no longer reads `flow_kind`: an edge qualifies
# when a runtime run observed it — `"runtime" in observed_by`
# (ADR-0016, decision 4). On an AST-only scan every edge is `["ast"]`,
# so the shortlist is empty; that is the intended answer.
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
        kind="has_access_to", host_id="localhost",
        flow_kind="data",
        observed_by=["ast", "runtime"],
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
        kind="has_access_to", host_id="localhost",
        flow_kind="data",
        observed_by=["ast", "runtime"],
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
        kind="has_access_to", host_id="localhost",
        flow_kind="data",
        observed_by=["ast", "runtime"],
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


def test_find_chains_data_edges_only_asks_observed_by_not_flow_kind(
    store: GraphStore,
) -> None:
    """`flow_kind` is not the filter any more (ADR-0016, decision 4).

    A control edge a runtime run observed qualifies; a `data`-labelled
    edge no runtime run saw does not. On an AST-only scan every edge is
    `["ast"]`, so the shortlist is empty by construction.
    """
    entry, agent, sink = (
        "entry_point:" + "e" * 16,
        "agent:" + "a" * 16,
        "sink:" + "s" * 16,
    )
    for node_id, kind, name in (
        (entry, "entry_point", "POST /chat"),
        (agent, "agent", "InvoiceAgent"),
        (sink, "sink", "shell"),
    ):
        store.nodes.upsert(Node(
            id=node_id, kind=kind, name=name, host_id="localhost",
        ))

    # Control, but seen at runtime.
    e_runtime = _link(
        store, "edge:" + "1" * 16, entry, agent,
        observed_by=["ast", "runtime"],
    )
    # Labelled data, but no runtime run ever confirmed it.
    e_ast_only = _link(
        store, "edge:" + "2" * 16, agent, sink, flow_kind="data",
        observed_by=["ast"],
    )

    assert [c.edges for c in store.query.find_chains()] == [
        [e_runtime, e_ast_only]
    ]
    assert [
        c.edges for c in store.query.find_chains(data_edges_only=True)
    ] == [[e_runtime, e_ast_only]]


def test_find_chains_data_edges_only_is_empty_on_an_ast_only_scan(
    store: GraphStore,
) -> None:
    """The Layer 1a graph never qualifies — correctly, not a failure.

    Every AST edge is `observed_by = ["ast"]`, so the high-precision
    shortlist is empty until a runtime run stamps an edge (ADR-0016).
    """
    entry, agent, sink = (
        "entry_point:" + "e" * 16,
        "agent:" + "a" * 16,
        "sink:" + "s" * 16,
    )
    for node_id, kind, name in (
        (entry, "entry_point", "POST /chat"),
        (agent, "agent", "InvoiceAgent"),
        (sink, "sink", "shell"),
    ):
        store.nodes.upsert(Node(
            id=node_id, kind=kind, name=name, host_id="localhost",
        ))
    e_entry = _link(store, "edge:" + "1" * 16, entry, agent)
    e_access = _link(store, "edge:" + "2" * 16, agent, sink)

    # Enumerable by default...
    assert [c.edges for c in store.query.find_chains()] == [
        [e_entry, e_access]
    ]
    # ...and off the shortlist, because nothing was observed at runtime.
    assert store.edges.get(e_access).observed_by == ["ast"]
    assert store.query.find_chains(data_edges_only=True) == []


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
    observed_by: list[str] | None = None,
) -> str:
    """Link two nodes; a `data` edge is runtime-observed by default.

    The data-only shortlist filters on `"runtime" in observed_by`
    (ADR-0016), so a hand-built data edge has to say who saw it.
    """
    store.edges.upsert(Edge(
        id=edge_id, source_id=source, target_id=target, kind=kind,
        host_id="localhost", flow_kind=flow_kind,
        observed_by=observed_by
        or (["ast", "runtime"] if flow_kind == "data" else ["ast"]),
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


def test_find_chain_groups_traverses_a_control_write_edge(
    store: GraphStore,
) -> None:
    """The 1.3a.1 shape: `mcp_server → writes_to → source`, control.

    The edge the writer stage emits is `control` with
    `data_flow_confirmed = "unknown"` (ADR-0015 decision 5 — a
    declaration, not a traced dataflow). It must still be traversable:
    a group is a pair, and a control write edge is the only kind the
    graph can produce for this side today.
    """
    ids = {
        "entry": "entry_point:" + "e" * 16,
        "server": "mcp_server:" + "m" * 16,
        "source": "untrusted_source:" + "u" * 16,
        "reader": "tool:" + "r" * 16,
        "sink": "sink:" + "s" * 16,
        "e_m": "edge:" + "1" * 16,
        "m_u": "edge:" + "2" * 16,
        "u_r": "edge:" + "3" * 16,
        "r_k": "edge:" + "4" * 16,
    }
    _put(store, ids["entry"], "entry_point", "GET /tools")
    _put(store, ids["server"], "mcp_server", "knowledge")
    _put(
        store, ids["source"], "untrusted_source",
        "mcp_tool_description:client.list_tools",
    )
    _put(store, ids["reader"], "tool", "get_documents")
    _put(store, ids["sink"], "sink", "run_shell")
    _link(store, ids["e_m"], ids["entry"], ids["server"])
    _link(
        store, ids["m_u"], ids["server"], ids["source"],
        kind="writes_to",
    )
    _link(store, ids["u_r"], ids["source"], ids["reader"], flow_kind="data")
    _link(store, ids["r_k"], ids["reader"], ids["sink"])

    groups = store.query.find_chain_groups()

    assert len(groups) == 1
    assert groups[0].untrusted_source_id == ids["source"]
    assert groups[0].write_chains == [
        [ids["entry"], ids["server"], ids["source"]]
    ]
    assert groups[0].read_chains == [
        [ids["source"], ids["reader"], ids["sink"]]
    ]


def test_find_chain_groups_with_the_dynamic_invocation_edge(
    store: GraphStore,
) -> None:
    """The 1.3a.1.2 shape: the reader reaches the tools it can invoke.

    Both sides of ADR-0010's channel, with the edge the dynamic
    invocation stage adds:

        entry --delegates_to--> agent --has_access_to--> server
              --writes_to--> source
        source --has_access_to(data)--> agent --uses_tool--> tool
              --has_access_to--> sink

    Before this stage the read chain stopped at the agent: the catalogue
    read and the `call_tool(name)` dispatch are two calls, and only the
    first had an edge. Here the agent is on both sides of the anchor,
    which is the FinBot shape — the component that reads the catalogue
    is the component that can invoke what it describes.
    """
    ids = {
        "entry": "entry_point:" + "e" * 16,
        "agent": "agent:" + "a" * 16,
        "server": "mcp_server:" + "m" * 16,
        "source": "untrusted_source:" + "u" * 16,
        "tool": "tool:" + "t" * 16,
        "sink": "sink:" + "s" * 16,
        "e_a": "edge:" + "1" * 16,
        "a_m": "edge:" + "2" * 16,
        "m_u": "edge:" + "3" * 16,
        "u_a": "edge:" + "4" * 16,
        "a_t": "edge:" + "5" * 16,
        "t_k": "edge:" + "6" * 16,
    }
    _put(store, ids["entry"], "entry_point", "POST /chat")
    _put(
        store, ids["agent"], "agent", "MCPToolProvider",
        capabilities=["invokes_mcp_tool", "reads_untrusted"],
    )
    _put(store, ids["server"], "mcp_server", "finstripe")
    _put(
        store, ids["source"], "untrusted_source",
        "mcp_tool_description:client.list_tools",
    )
    _put(store, ids["tool"], "tool", "get_account_balance")
    _put(store, ids["sink"], "sink", "run_shell")
    _link(
        store, ids["e_a"], ids["entry"], ids["agent"],
        kind="delegates_to",
    )
    _link(store, ids["a_m"], ids["agent"], ids["server"])
    _link(
        store, ids["m_u"], ids["server"], ids["source"],
        kind="writes_to",
    )
    _link(store, ids["u_a"], ids["source"], ids["agent"], flow_kind="data")
    _link(
        store, ids["a_t"], ids["agent"], ids["tool"], kind="uses_tool",
    )
    _link(store, ids["t_k"], ids["tool"], ids["sink"])

    groups = store.query.find_chain_groups()

    assert len(groups) == 1
    assert groups[0].untrusted_source_id == ids["source"]
    assert groups[0].write_chains == [
        [ids["entry"], ids["agent"], ids["server"], ids["source"]]
    ]
    assert groups[0].read_chains == [
        [ids["source"], ids["agent"], ids["tool"], ids["sink"]]
    ]


def test_find_chain_groups_with_the_project_level_writer_anchor(
    store: GraphStore,
) -> None:
    """The 1.3a.1.3 shape: the writer is not the reader's server.

    The project-wide literal anchor (1.3a.1.3) links a server to a
    catalogue read in *another* file, so a group can pair a server that
    writes a catalogue with a read chain that reaches a tool a
    **different** server provides. The anchor is the source, not the
    writer's tools, so the pair forms either way — and the two halves
    share no server but the source:

        entry --has_access_to--> finmail --writes_to--> source
        source --has_access_to(data)--> MCPToolProvider
               --uses_tool--> get_account_balance --has_access_to--> sink

    Before the widening this graph was unreachable: the writer edge only
    existed when the server was visible from the reader's file.
    """
    ids = {
        "entry": "entry_point:" + "e" * 16,
        "writer": "mcp_server:" + "w" * 16,
        "source": "untrusted_source:" + "u" * 16,
        "agent": "agent:" + "a" * 16,
        "tool": "tool:" + "t" * 16,
        "sink": "sink:" + "s" * 16,
        "e_w": "edge:" + "1" * 16,
        "w_s": "edge:" + "2" * 16,
        "s_a": "edge:" + "3" * 16,
        "a_t": "edge:" + "4" * 16,
        "t_k": "edge:" + "5" * 16,
    }
    _put(store, ids["entry"], "entry_point", "POST /chat")
    _put(store, ids["writer"], "mcp_server", "finmail")
    _put(
        store, ids["source"], "untrusted_source",
        "mcp_tool_description:client.list_tools",
    )
    _put(
        store, ids["agent"], "agent", "MCPToolProvider",
        capabilities=["invokes_mcp_tool", "reads_untrusted"],
    )
    _put(store, ids["tool"], "tool", "get_account_balance")
    _put(store, ids["sink"], "sink", "run_shell")
    _link(store, ids["e_w"], ids["entry"], ids["writer"])
    _link(
        store, ids["w_s"], ids["writer"], ids["source"],
        kind="writes_to",
    )
    _link(store, ids["s_a"], ids["source"], ids["agent"], flow_kind="data")
    _link(
        store, ids["a_t"], ids["agent"], ids["tool"], kind="uses_tool",
    )
    _link(store, ids["t_k"], ids["tool"], ids["sink"])

    groups = store.query.find_chain_groups()

    assert len(groups) == 1
    assert groups[0].untrusted_source_id == ids["source"]
    assert groups[0].write_chains == [
        [ids["entry"], ids["writer"], ids["source"]]
    ]
    assert groups[0].read_chains == [
        [ids["source"], ids["agent"], ids["tool"], ids["sink"]]
    ]


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


# ------------------------------------------------------------
# prioritize_chain_groups (1.3c, ADR-0012)
# ------------------------------------------------------------

def _second_group(store: GraphStore) -> str:
    """A second write/read pair whose read chain ends at a shell.

        entry → writer2 → source2 → reader2 → execute_script

    Same shape as `_group_graph`, one difference: the sink carries
    `executes_shell`, so the group scores +4 where the first scores 0.
    """
    ids = {
        "writer": "tool:" + "W" * 16,
        "source": "untrusted_source:" + "V" * 16,
        "reader": "tool:" + "R" * 16,
        "sink": "sink:" + "S" * 16,
    }
    _put(store, ids["writer"], "tool", "save_document")
    _put(
        store, ids["source"], "untrusted_source",
        "rag_corpus:vectorstore.search",
    )
    _put(store, ids["reader"], "tool", "search_documents")
    _put(
        store, ids["sink"], "sink", "execute_script",
        capabilities=["executes_shell"],
    )
    _link(store, "edge:" + "6" * 16, "entry_point:" + "e" * 16, ids["writer"])
    _link(
        store, "edge:" + "7" * 16, ids["writer"], ids["source"],
        flow_kind="data",
        observed_by=["ast", "runtime"],
    )
    _link(
        store, "edge:" + "8" * 16, ids["source"], ids["reader"],
        flow_kind="data",
        observed_by=["ast", "runtime"],
    )
    _link(store, "edge:" + "9" * 16, ids["reader"], ids["sink"])
    return ids["source"]


def _long_pair(
    store: GraphStore,
    *,
    tag: str,
    sink_caps: list[str] | None = None,
) -> str:
    """A pair too long for the short-chain point, with a chosen sink.

        entry → w1 → w2 → source → r1 → r2 → r3 → sink

    Three write hops and four read hops is 7 — one past the short-chain
    threshold — so the score is the source type plus the sink and
    nothing else. The source is `mcp_tool_description`, ADR-0012's
    readable-only kind (+1).

    `tag` namespaces every id: two of these in one test must not share
    a node, because `nodes.upsert` unions capabilities.
    """
    ids = {
        "entry": "entry_point:" + tag * 16,
        "w1": f"tool:{tag}w1",
        "w2": f"tool:{tag}w2",
        "source": "untrusted_source:" + tag * 16,
        "r1": f"tool:{tag}r1",
        "r2": f"tool:{tag}r2",
        "r3": f"tool:{tag}r3",
        "sink": "sink:" + tag * 16,
    }
    _put(store, ids["entry"], "entry_point", "POST /chat")
    _put(store, ids["w1"], "tool", "prepare_upload")
    _put(store, ids["w2"], "tool", "save_document")
    _put(
        store, ids["source"], "untrusted_source",
        "mcp_tool_description:client.list_tools",
    )
    _put(store, ids["r1"], "tool", "get_documents")
    _put(store, ids["r2"], "tool", "summarize_documents")
    _put(store, ids["r3"], "tool", "file_update")
    _put(
        store, ids["sink"], "sink", "update_status",
        capabilities=sink_caps,
    )
    _link(store, f"edge:{tag}1", ids["entry"], ids["w1"])
    _link(store, f"edge:{tag}2", ids["w1"], ids["w2"], flow_kind="data")
    _link(store, f"edge:{tag}3", ids["w2"], ids["source"], flow_kind="data")
    _link(store, f"edge:{tag}4", ids["source"], ids["r1"], flow_kind="data")
    _link(store, f"edge:{tag}5", ids["r1"], ids["r2"])
    _link(store, f"edge:{tag}6", ids["r2"], ids["r3"])
    _link(store, f"edge:{tag}7", ids["r3"], ids["sink"])
    return ids["source"]


def test_prioritize_chain_groups_selects_the_stronger_group(
    store: GraphStore,
) -> None:
    """Two enumerated groups, one dangerous sink: the sink wins."""
    ids = _group_graph(store)
    other_source = _second_group(store)

    groups = store.query.find_chain_groups()
    assert len(groups) == 2

    selected = store.query.prioritize_chain_groups(
        groups, min_score_to_evaluate=0, max_groups_evaluated=1,
    )

    assert len(selected) == 1
    assert selected[0].untrusted_source_id == other_source
    assert selected[0].score == 7.0
    assert selected[0].priority == "low"
    assert selected[0].score_components["dangerous_sink"] == 4

    # The other group was scored and dropped, not skipped.
    weak = next(g for g in groups if g.untrusted_source_id == ids["source"])
    assert weak.score == 3.0
    assert weak.priority is None


def test_prioritize_chain_groups_applies_the_default_threshold(
    store: GraphStore,
) -> None:
    """ADR-0012's revised `min_score_to_evaluate = 3` is the default.

    `_group_graph` scores 3.0 (a writable source plus the short-chain
    point) and `_second_group` 7.0 (the shell sink), so both clear the
    revised threshold — the original 5 would have dropped the first.
    A readable source alone (+1) is still below it.
    """
    ids = _group_graph(store)
    other_source = _second_group(store)
    readable_only = _long_pair(store, tag="x")

    groups = store.query.find_chain_groups()
    selected = store.query.prioritize_chain_groups(groups)

    assert [g.untrusted_source_id for g in selected] == [
        other_source, ids["source"],
    ]
    assert [g.score for g in selected] == [7.0, 3.0]
    below = next(
        g for g in groups if g.untrusted_source_id == readable_only
    )
    assert below.score == 1.0
    assert below.priority is None


def test_prioritize_chain_groups_selects_a_writes_db_sink(
    store: GraphStore,
) -> None:
    """A readable source (+1) plus a database write (+3) is 4 — over 3.

    The FinBot shape at the new threshold: the sink is a `writes_db`
    method, which the two 1.3c impact components did not weight.
    """
    source = _long_pair(store, tag="x", sink_caps=["writes_db"])

    selected = store.query.prioritize_chain_groups(
        store.query.find_chain_groups()
    )

    assert [g.untrusted_source_id for g in selected] == [source]
    assert selected[0].score == 4.0
    assert selected[0].score_components["writes_db"] == SCORE_WRITES_DB
    assert selected[0].priority == "low"


def test_prioritize_chain_groups_drops_a_source_only_group(
    store: GraphStore,
) -> None:
    """A source without a sink is not worth the LLM call (ADR-0012)."""
    source = _long_pair(store, tag="x")

    groups = store.query.find_chain_groups()
    selected = store.query.prioritize_chain_groups(groups)

    assert selected == []
    weak = next(g for g in groups if g.untrusted_source_id == source)
    assert weak.score == 1.0
    assert weak.priority is None


def test_prioritize_chain_groups_does_not_write(
    store: GraphStore,
) -> None:
    """Selection is not persistence: the caller stores, not the query."""
    _group_graph(store)
    _second_group(store)
    before = (store.nodes.count(), store.edges.count())

    assert store.query.prioritize_chain_groups(
        store.query.find_chain_groups()
    )

    assert (store.nodes.count(), store.edges.count()) == before
    assert store.chain_groups.count() == 0

