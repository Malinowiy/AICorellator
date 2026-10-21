"""Smoke tests: schema applies, constraints enforce invariants."""

from __future__ import annotations

import sqlite3

import pytest

from aicorellator import GraphStore
from aicorellator.graph import Edge, Finding, Node


def test_schema_applies_cleanly(store: GraphStore) -> None:
    """All expected tables exist after GraphStore opens."""
    rows = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    tables = {r["name"] for r in rows}
    expected = {
        "schema_version", "hosts", "nodes", "edges",
        "findings", "chains", "state", "task_state", "reports",
    }
    assert expected <= tables


def test_localhost_bootstrapped(store: GraphStore) -> None:
    """Phase 0 always has exactly one host."""
    hosts = store.hosts.list()
    assert len(hosts) == 1
    assert hosts[0].id == "localhost"
    assert hosts[0].platform == "standalone"


def test_state_bootstrapped(store: GraphStore) -> None:
    """All documented state keys are present for localhost."""
    state = store.state.all("localhost")
    expected_keys = {
        "phase1_done", "scan_done", "report_fresh", "last_report_at",
        "dynamic_enabled", "cleanup_stale_days", "cleanup_findings_days",
        "cleanup_chains_days", "cleanup_reports_days",
    }
    assert set(state.keys()) == expected_keys


def test_task_state_bootstrapped(store: GraphStore) -> None:
    """Two rows per host: static and dynamic."""
    rows = store.task_state.list("localhost")
    kinds = {r.kind for r in rows}
    assert kinds == {"static", "dynamic"}


def test_node_kind_check(store: GraphStore) -> None:
    """Invalid node kind is rejected."""
    with pytest.raises(sqlite3.IntegrityError):
        store.nodes.add(Node(
            id="x:1", kind="not-a-kind", name="x", host_id="localhost",
        ))


def test_untrusted_source_node_kind_is_accepted(store: GraphStore) -> None:
    """The 1.3a kind is in the CHECK constraint (ADR-0010)."""
    store.nodes.add(Node(
        id="untrusted_source:1",
        kind="untrusted_source",
        name="rag_corpus:retriever.invoke",
        host_id="localhost",
        attributes={"source_type": "rag_corpus", "source_file": "app.py"},
    ))

    fetched = store.nodes.get("untrusted_source:1")
    assert fetched is not None
    assert fetched.kind == "untrusted_source"
    assert fetched.attributes["source_type"] == "rag_corpus"


def test_edge_self_loop_rejected(store: GraphStore) -> None:
    """source_id == target_id is rejected by CHECK."""
    store.nodes.upsert(Node(
        id="n:1", kind="agent", name="x", host_id="localhost",
    ))
    with pytest.raises(sqlite3.IntegrityError):
        store.edges.add(Edge(
            id="e:1", source_id="n:1", target_id="n:1",
            kind="uses_model", host_id="localhost",
        ))


def test_finding_exclusivity_check(store: GraphStore) -> None:
    """Finding attached to both node and edge is rejected."""
    store.nodes.upsert(Node(
        id="n:1", kind="agent", name="x", host_id="localhost",
    ))

    # Both — schema rejects
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            """
            INSERT INTO findings (id, node_id, edge_id, scanner, severity, title)
            VALUES ('f:1', 'n:1', 'e:1', 'aak', 'high', 'x')
            """
        )

    # Neither — schema rejects
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            """
            INSERT INTO findings (id, node_id, edge_id, scanner, severity, title)
            VALUES ('f:2', NULL, NULL, 'aak', 'high', 'x')
            """
        )


def test_edge_flow_kind_check(store: GraphStore) -> None:
    """Invalid flow_kind is rejected by the CHECK constraint (ADR-0015)."""
    store.nodes.upsert(Node(
        id="n:1", kind="agent", name="x", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="n:2", kind="sink", name="s", host_id="localhost",
    ))
    with pytest.raises(sqlite3.IntegrityError):
        store.edges.add(Edge(
            id="e:1", source_id="n:1", target_id="n:2",
            kind="has_access_to", host_id="localhost",
            flow_kind="not-a-flow-kind",
        ))


def test_edge_data_flow_confirmed_check(store: GraphStore) -> None:
    """Invalid data_flow_confirmed is rejected by CHECK; NULL is allowed."""
    store.nodes.upsert(Node(
        id="n:1", kind="agent", name="x", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="n:2", kind="sink", name="s", host_id="localhost",
    ))
    with pytest.raises(sqlite3.IntegrityError):
        store.edges.add(Edge(
            id="e:1", source_id="n:1", target_id="n:2",
            kind="has_access_to", host_id="localhost",
            data_flow_confirmed="not-a-value",
        ))
    # NULL is the default — no error.
    store.edges.add(Edge(
        id="e:2", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
    ))


def test_confidence_clamped(store: GraphStore) -> None:
    """confidence outside [0, 1] is rejected."""
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            """
            INSERT INTO nodes (id, kind, name, host_id, confidence)
            VALUES ('n:bad', 'agent', 'x', 'localhost', 1.5)
            """
        )