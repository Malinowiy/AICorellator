"""Smoke tests: CRUD over each repository."""

from __future__ import annotations

import pytest

from aicorellator import GraphStore
from aicorellator.graph import Edge, Finding, Node


# ------------------------------------------------------------
# Nodes
# ------------------------------------------------------------

def test_node_add_get(store: GraphStore) -> None:
    node = Node(id="n:1", kind="agent", name="A", host_id="localhost")
    store.nodes.add(node)

    fetched = store.nodes.get("n:1")
    assert fetched is not None
    assert fetched.name == "A"
    assert fetched.kind == "agent"


def test_node_upsert_merges_attributes(store: GraphStore) -> None:
    store.nodes.upsert(Node(
        id="n:1", kind="agent", name="A", host_id="localhost",
        attributes={"a": 1, "b": 2},
    ))
    store.nodes.upsert(Node(
        id="n:1", kind="agent", name="A", host_id="localhost",
        attributes={"b": 99, "c": 3},
    ))

    fetched = store.nodes.get("n:1")
    assert fetched.attributes == {"a": 1, "b": 99, "c": 3}


def test_node_roundtrips_the_lethal_trifecta_attribute(
    store: GraphStore,
) -> None:
    """The 1.3d flag survives SQLite as a boolean.

    It rides inside the `attributes` JSON column — no schema change — so
    what is worth pinning is the type: `json.loads` gives back a `bool`,
    not `1` or `"true"`, and `False` survives as a stored value rather
    than disappearing (1.3d stores the negative; see the stage).
    """
    store.nodes.add(Node(
        id="n:1", kind="tool", name="t", host_id="localhost",
        attributes={"lethal_trifecta": True},
    ))
    store.nodes.add(Node(
        id="n:2", kind="tool", name="u", host_id="localhost",
        attributes={"lethal_trifecta": False},
    ))

    assert store.nodes.get("n:1").attributes["lethal_trifecta"] is True
    assert store.nodes.get("n:2").attributes["lethal_trifecta"] is False


def test_node_update_confidence(store: GraphStore) -> None:
    store.nodes.add(Node(
        id="n:1", kind="agent", name="A", host_id="localhost",
        confidence=1.0,
    ))
    store.nodes.update_confidence("n:1", -0.3)
    assert store.nodes.get("n:1").confidence == pytest.approx(0.7)

    store.nodes.update_confidence("n:1", -0.5)
    assert store.nodes.get("n:1").confidence == pytest.approx(0.2)

    # Clamped at 0
    store.nodes.update_confidence("n:1", -1.0)
    assert store.nodes.get("n:1").confidence == 0.0


def test_node_mark_stale(store: GraphStore) -> None:
    store.nodes.add(Node(
        id="n:1", kind="agent", name="A", host_id="localhost",
    ))
    store.nodes.mark_stale("n:1")
    assert store.nodes.get("n:1").status == "stale"


def test_node_list_by_kind(store: GraphStore) -> None:
    store.nodes.add(Node(id="n:1", kind="agent", name="A", host_id="localhost"))
    store.nodes.add(Node(id="n:2", kind="sink", name="S", host_id="localhost"))
    store.nodes.add(Node(id="n:3", kind="agent", name="B", host_id="localhost"))

    agents = store.nodes.list(kind="agent")
    assert {n.id for n in agents} == {"n:1", "n:3"}


# ------------------------------------------------------------
# Edges
# ------------------------------------------------------------

def test_edge_add_unique(store: GraphStore) -> None:
    store.nodes.add(Node(id="n:1", kind="agent", name="A", host_id="localhost"))
    store.nodes.add(Node(id="n:2", kind="sink", name="S", host_id="localhost"))

    store.edges.add(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
    ))

    # Same (source, target, kind, host) with different id — unique index rejects
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        store.edges.add(Edge(
            id="e:2", source_id="n:1", target_id="n:2",
            kind="has_access_to", host_id="localhost",
        ))


def test_edge_upsert_upgrades_precision(store: GraphStore) -> None:
    store.nodes.add(Node(id="n:1", kind="agent", name="A", host_id="localhost"))
    store.nodes.add(Node(id="n:2", kind="sink", name="S", host_id="localhost"))

    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
        precision="syntactic",
    ))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
        precision="compiler-verified",
    ))

    assert store.edges.get("e:1").precision == "compiler-verified"

    # Downgrade attempt: syntactic does not override compiler-verified
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
        precision="syntactic",
    ))
    assert store.edges.get("e:1").precision == "compiler-verified"


# ------------------------------------------------------------
# Findings
# ------------------------------------------------------------

def test_finding_add_to_node(store: GraphStore) -> None:
    store.nodes.add(Node(id="n:1", kind="agent", name="A", host_id="localhost"))
    store.findings.add(Finding(
        id="f:1", scanner="aak", severity="high", title="x",
        node_id="n:1",
    ))
    assert store.findings.get("f:1").node_id == "n:1"


def test_finding_validates_exclusivity(store: GraphStore) -> None:
    """Python-level validation catches the same issue as the CHECK."""
    with pytest.raises(ValueError, match="cannot attach to both"):
        store.findings.add(Finding(
            id="f:1", scanner="aak", severity="high", title="x",
            node_id="n:1", edge_id="e:1",
        ))

    with pytest.raises(ValueError, match="must attach to either"):
        store.findings.add(Finding(
            id="f:1", scanner="aak", severity="high", title="x",
        ))


def test_finding_mark_stale_for_node(store: GraphStore) -> None:
    store.nodes.add(Node(id="n:1", kind="agent", name="A", host_id="localhost"))
    for i in range(3):
        store.findings.add(Finding(
            id=f"f:{i}", scanner="aak", severity="high", title="x",
            node_id="n:1",
        ))

    count = store.findings.mark_stale_for_node("n:1")
    assert count == 3
    assert all(f.status == "stale" for f in store.findings.list(node_id="n:1"))


# ------------------------------------------------------------
# Chains
# ------------------------------------------------------------

def test_chain_add_and_read(store: GraphStore) -> None:
    from aicorellator.graph import Chain

    store.chains.add(Chain(
        id="chain:1",
        chain_nodes=["n:1", "n:2"],
        chain_edges=["e:1"],
        verdict="exploitable",
        prompt_version="v1.0.0",
        confidence=0.8,
        validated=True,
    ))

    c = store.chains.get("chain:1")
    assert c.verdict == "exploitable"
    assert c.chain_nodes == ["n:1", "n:2"]
    assert c.validated is True
    # The row was written with no `validated_kind`, which is the column
    # being NULL — what a chain stored before A.4 looks like. The read
    # path names the level it was actually judged by.
    assert c.validated_kind == "existence"
    assert c.validation_notes == []

def test_chain_length_property(store: GraphStore) -> None:
    """Chain.length derives from chain_nodes, not stored."""
    from aicorellator.graph import Chain

    store.chains.add(Chain(
        id="c:1",
        chain_nodes=["a", "b", "c", "d"],   # 4 nodes
        chain_edges=["e1", "e2", "e3"],     # 3 edges
        verdict="exploitable",
        prompt_version="v1.0.0",
        confidence=0.9,
        validated=True,
    ))

    chain = store.chains.get("c:1")
    assert chain.length == 3   # 4 nodes - 1 = 3 hops


def test_chain_length_empty(store: GraphStore) -> None:
    """Empty chain → length 0, no negative."""
    from aicorellator.graph import Chain

    store.chains.add(Chain(
        id="c:empty",
        chain_nodes=[],
        chain_edges=[],
        verdict="uncertain",
        prompt_version="v1.0.0",
    ))

    chain = store.chains.get("c:empty")
    assert chain.length == 0

def test_chain_list_actionable(store: GraphStore) -> None:
    from aicorellator.graph import Chain

    store.chains.add(Chain(
        id="c:high", chain_nodes=[], chain_edges=[],
        verdict="exploitable", prompt_version="v1",
        confidence=0.9, validated=True,
    ))
    store.chains.add(Chain(
        id="c:low", chain_nodes=[], chain_edges=[],
        verdict="exploitable", prompt_version="v1",
        confidence=0.3, validated=True,
    ))
    store.chains.add(Chain(
        id="c:hallucination", chain_nodes=[], chain_edges=[],
        verdict="exploitable", prompt_version="v1",
        confidence=0.9, validated=False,   # ← rejected by Layer 3
    ))

    actionable = store.chains.list_actionable(min_confidence=0.5)
    ids = {c.id for c in actionable}
    assert ids == {"c:high"}


# ------------------------------------------------------------
# Reports
# ------------------------------------------------------------

def test_report_current_uniqueness(store: GraphStore) -> None:
    import sqlite3
    from aicorellator.graph import Report

    store.reports.add(Report(
        id="r:1", host_id="localhost", bundle_dir="current",
        tier="full", is_current=True,
    ))

    # Second current — partial unique index rejects
    with pytest.raises(sqlite3.IntegrityError):
        store.reports.add(Report(
            id="r:2", host_id="localhost", bundle_dir="current",
            tier="full", is_current=True,
        ))


def test_report_archive_current(store: GraphStore) -> None:
    from aicorellator.graph import Report

    store.reports.add(Report(
        id="r:1", host_id="localhost", bundle_dir="current",
        tier="full", is_current=True,
    ))
    store.reports.archive_current("localhost", "20261010_120000")

    old = store.reports.get("r:1")
    assert old.is_current is False
    assert old.bundle_dir == "20261010_120000"
    assert store.reports.get_current("localhost") is None

def test_node_add_persists_capabilities(store: GraphStore) -> None:
    store.nodes.add(Node(
        id="n:1", kind="tool", name="t", host_id="localhost",
        capabilities=["executes_code"],
    ))
    assert store.nodes.get("n:1").capabilities == ["executes_code"]


def test_node_upsert_unions_capabilities(store: GraphStore) -> None:
    store.nodes.add(Node(
        id="n:1", kind="tool", name="t", host_id="localhost",
        capabilities=["executes_code"],
    ))
    store.nodes.upsert(Node(
        id="n:1", kind="tool", name="t", host_id="localhost",
        capabilities=["reads_filesystem"],
    ))
    assert sorted(store.nodes.get("n:1").capabilities) == [
        "executes_code", "reads_filesystem",
    ]


def test_edge_add_persists_capabilities(store: GraphStore) -> None:
    store.nodes.add(Node(id="n:1", kind="agent", name="a", host_id="localhost"))
    store.nodes.add(Node(id="n:2", kind="tool", name="t", host_id="localhost"))
    store.edges.add(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
        capabilities=["data_flows_forward"],
    ))
    assert store.edges.get("e:1").capabilities == ["data_flows_forward"]


def test_edge_roundtrips_flow_kind_and_data_flow_confirmed(
    store: GraphStore,
) -> None:
    """Edge.flow_kind and Edge.data_flow_confirmed survive add/get (ADR-0015)."""
    store.nodes.add(Node(id="n:1", kind="tool", name="t", host_id="localhost"))
    store.nodes.add(Node(id="n:2", kind="sink", name="s", host_id="localhost"))
    store.edges.add(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
        flow_kind="data", data_flow_confirmed="parameter_match",
    ))

    fetched = store.edges.get("e:1")
    assert fetched.flow_kind == "data"
    assert fetched.data_flow_confirmed == "parameter_match"


def test_edge_flow_kind_defaults_to_control(store: GraphStore) -> None:
    """An edge without an explicit flow_kind defaults to 'control' (ADR-0015)."""
    store.nodes.add(Node(id="n:1", kind="agent", name="a", host_id="localhost"))
    store.nodes.add(Node(id="n:2", kind="sink", name="s", host_id="localhost"))
    store.edges.add(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
    ))

    fetched = store.edges.get("e:1")
    assert fetched.flow_kind == "control"
    assert fetched.data_flow_confirmed is None


def test_edge_upsert_upgrades_flow_kind(store: GraphStore) -> None:
    """A later 'data' classification wins over the 'control' default."""
    store.nodes.add(Node(id="n:1", kind="tool", name="t", host_id="localhost"))
    store.nodes.add(Node(id="n:2", kind="sink", name="s", host_id="localhost"))
    store.edges.add(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
        flow_kind="data", data_flow_confirmed="local_var_match",
    ))

    fetched = store.edges.get("e:1")
    assert fetched.flow_kind == "data"
    assert fetched.data_flow_confirmed == "local_var_match"


def test_node_observed_by_defaults_to_ast(store: GraphStore) -> None:
    """Every Layer 1a node says who saw it: `["ast"]` (ADR-0016)."""
    store.nodes.add(Node(
        id="n:1", kind="tool", name="t", host_id="localhost",
    ))

    assert store.nodes.get("n:1").observed_by == ["ast"]


def test_node_observed_by_roundtrips(store: GraphStore) -> None:
    """A runtime-confirmed node keeps both observers (ADR-0016)."""
    store.nodes.add(Node(
        id="n:1", kind="agent", name="a", host_id="localhost",
        observed_by=["ast", "runtime"],
    ))

    assert store.nodes.get("n:1").observed_by == ["ast", "runtime"]


def test_node_upsert_unions_observed_by(store: GraphStore) -> None:
    """Layer 2 adds itself to the set; it never replaces it (ADR-0016)."""
    store.nodes.add(Node(
        id="n:1", kind="agent", name="a", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="n:1", kind="agent", name="a", host_id="localhost",
        observed_by=["ast", "runtime"],
    ))

    assert store.nodes.get("n:1").observed_by == ["ast", "runtime"]


def test_edge_observed_by_defaults_to_ast(store: GraphStore) -> None:
    """Every AST edge is `["ast"]`, the column's schema default."""
    store.nodes.add(Node(
        id="n:1", kind="agent", name="a", host_id="localhost",
    ))
    store.nodes.add(Node(
        id="n:2", kind="sink", name="s", host_id="localhost",
    ))
    store.edges.add(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
    ))

    assert store.edges.get("e:1").observed_by == ["ast"]


def test_edge_observed_by_roundtrips(store: GraphStore) -> None:
    """A runtime observation survives add/get (ADR-0016)."""
    store.nodes.add(Node(id="n:1", kind="tool", name="t", host_id="localhost"))
    store.nodes.add(Node(id="n:2", kind="sink", name="s", host_id="localhost"))
    store.edges.add(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
        observed_by=["ast", "runtime"],
    ))

    assert store.edges.get("e:1").observed_by == ["ast", "runtime"]


def test_edge_upsert_unions_observed_by(store: GraphStore) -> None:
    """The set accumulates: `ast` + `runtime`, deduplicated and sorted."""
    store.nodes.add(Node(
        id="n:1", kind="agent", name="a", host_id="localhost",
    ))
    store.nodes.add(Node(
        id="n:2", kind="sink", name="s", host_id="localhost",
    ))
    store.edges.add(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
        observed_by=["ast", "runtime"],
    ))

    assert store.edges.get("e:1").observed_by == ["ast", "runtime"]


def test_edge_upsert_unions_capabilities(store: GraphStore) -> None:
    store.nodes.add(Node(id="n:1", kind="agent", name="a", host_id="localhost"))
    store.nodes.add(Node(id="n:2", kind="tool", name="t", host_id="localhost"))
    store.edges.add(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
        capabilities=["data_flows_forward"],
    ))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
        capabilities=["control_flows_forward"],
    ))
    assert sorted(store.edges.get("e:1").capabilities) == [
        "control_flows_forward", "data_flows_forward",
    ]