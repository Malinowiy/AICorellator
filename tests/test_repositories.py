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