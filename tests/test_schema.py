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


def test_edge_kind_check(store: GraphStore) -> None:
    """`writes_to` is in the CHECK constraint; an unknown kind is not.

    The 1.3a.1 writer edge (ADR-0010) is the seventh kind. It is
    `control` with no data-flow verdict: `data_flow_confirmed` is
    populated only by Layer 2 (ADR-0016).
    """
    store.nodes.upsert(Node(
        id="s:1", kind="mcp_server", name="knowledge", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="u:1", kind="untrusted_source",
        name="mcp_tool_description:client.list_tools", host_id="localhost",
    ))

    store.edges.add(Edge(
        id="e:1", source_id="s:1", target_id="u:1",
        kind="writes_to", host_id="localhost",
    ))

    fetched = store.edges.get("e:1")
    assert fetched is not None
    assert fetched.kind == "writes_to"
    assert fetched.flow_kind == "control"
    assert fetched.data_flow_confirmed is None

    with pytest.raises(sqlite3.IntegrityError):
        store.edges.add(Edge(
            id="e:2", source_id="s:1", target_id="u:1",
            kind="not-a-kind", host_id="localhost",
        ))


def test_store_node_kind_is_accepted(store: GraphStore) -> None:
    """S1 — `store` is in the CHECK constraint (ADR-0021 §1).

    The node is identified by `(family, identity_hint)` in `attributes`
    and named `<family>:<identity_hint>`; it carries no confidence of
    its own, which is why the row's `confidence` stays at its default.
    """
    store.nodes.add(Node(
        id="store:1",
        kind="store",
        name="fs:/uploads/",
        host_id="localhost",
        attributes={"family": "fs", "identity_hint": "/uploads/"},
    ))

    fetched = store.nodes.get("store:1")
    assert fetched is not None
    assert fetched.kind == "store"
    assert fetched.attributes["family"] == "fs"
    assert fetched.attributes["identity_hint"] == "/uploads/"


def test_reads_from_edge_kind_is_accepted(store: GraphStore) -> None:
    """S2 — `reads_from` is in the CHECK constraint (ADR-0021 §3).

    Direction is the store's: `store → untrusted_source`.
    """
    store.nodes.upsert(Node(
        id="s:1", kind="store", name="fs:/uploads/", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="u:1", kind="untrusted_source",
        name="user_upload:f.read", host_id="localhost",
    ))

    store.edges.add(Edge(
        id="e:1", source_id="s:1", target_id="u:1",
        kind="reads_from", host_id="localhost",
        flow_kind="structural", identity_confidence=0.5,
    ))

    fetched = store.edges.get("e:1")
    assert fetched.kind == "reads_from"
    assert fetched.flow_kind == "structural"
    assert fetched.identity_confidence == 0.5


def test_identity_confidence_check(store: GraphStore) -> None:
    """S3 — the column is nullable and bounded to `[0.0, 1.0]`.

    One read site per case: `edges` is unique on
    `(source_id, target_id, kind, host_id)`, so the cases differ in
    *which* source reads from the store rather than in the row's id.
    """
    store.nodes.upsert(Node(
        id="s:1", kind="store", name="fs:/uploads/", host_id="localhost",
    ))
    for i in range(1, 6):
        store.nodes.upsert(Node(
            id=f"u:{i}", kind="untrusted_source",
            name=f"user_upload:f.read{i}", host_id="localhost",
        ))

    def _link(edge_id: str, target: str, value: float | None) -> None:
        store.edges.add(Edge(
            id=edge_id, source_id="s:1", target_id=target,
            kind="reads_from", host_id="localhost",
            identity_confidence=value,
        ))

    # NULL is the value every kind but the two store links carries.
    _link("e:null", "u:1", None)
    assert store.edges.get("e:null").identity_confidence is None

    # The bounds themselves are accepted.
    _link("e:low", "u:2", 0.0)
    _link("e:high", "u:3", 1.0)
    assert store.edges.get("e:low").identity_confidence == 0.0
    assert store.edges.get("e:high").identity_confidence == 1.0

    # Outside them the CHECK refuses the row.
    with pytest.raises(sqlite3.IntegrityError):
        _link("e:under", "u:4", -0.1)
    with pytest.raises(sqlite3.IntegrityError):
        _link("e:over", "u:5", 1.1)


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


def test_node_observed_by_defaults_to_ast_in_sql(store: GraphStore) -> None:
    """The column's SQL default is `["ast"]` (ADR-0016)."""
    store._conn.execute(
        """
        INSERT INTO nodes (id, kind, name, host_id)
        VALUES ('n:1', 'agent', 'x', 'localhost')
        """
    )
    row = store._conn.execute(
        "SELECT observed_by FROM nodes WHERE id = 'n:1'"
    ).fetchone()
    assert row["observed_by"] == '["ast"]'


def test_observed_by_must_be_json(store: GraphStore) -> None:
    """`json_valid(observed_by)` is a CHECK on both tables (ADR-0016)."""
    store.nodes.add(Node(
        id="n:1", kind="agent", name="x", host_id="localhost",
    ))
    store.nodes.add(Node(
        id="n:2", kind="sink", name="s", host_id="localhost",
    ))
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            """
            INSERT INTO nodes (id, kind, name, host_id, observed_by)
            VALUES ('n:3', 'agent', 'y', 'localhost', 'ast')
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            """
            INSERT INTO edges (id, source_id, target_id, kind, host_id,
                               observed_by)
            VALUES ('e:1', 'n:1', 'n:2', 'has_access_to', 'localhost', 'ast')
            """
        )


def test_edge_observed_by_survives_a_round_trip(store: GraphStore) -> None:
    """The JSON array comes back as a list, not a string (ADR-0016)."""
    store.nodes.add(Node(
        id="n:1", kind="agent", name="x", host_id="localhost",
    ))
    store.nodes.add(Node(
        id="n:2", kind="sink", name="s", host_id="localhost",
    ))
    store.edges.add(Edge(
        id="e:1", source_id="n:1", target_id="n:2",
        kind="has_access_to", host_id="localhost",
        observed_by=["ast", "runtime"],
    ))

    assert store.edges.get("e:1").observed_by == ["ast", "runtime"]


def test_confidence_clamped(store: GraphStore) -> None:
    """confidence outside [0, 1] is rejected."""
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            """
            INSERT INTO nodes (id, kind, name, host_id, confidence)
            VALUES ('n:bad', 'agent', 'x', 'localhost', 1.5)
            """
        )


def test_chain_groups_carries_the_provenance_columns(
    store: GraphStore,
) -> None:
    """B.7: the two columns a group verdict is traced by.

    `PRAGMA table_info` rather than a round-trip through the repository
    (`test_chain_groups_repository.py` covers that): what this pins is
    the table the schema builds — the columns are there, neither is
    nullable, and both default to the "not computed" value `chains`
    spells them with.
    """
    columns = {
        row["name"]: row
        for row in store._conn.execute("PRAGMA table_info(chain_groups)")
    }

    assert columns["prompt_version"]["notnull"] == 1
    assert columns["prompt_version"]["dflt_value"] == "''"
    assert columns["voter_outputs"]["notnull"] == 1
    assert columns["voter_outputs"]["dflt_value"] == "'[]'"


def test_chain_groups_voter_outputs_must_be_json(store: GraphStore) -> None:
    """`json_valid(voter_outputs)` is a CHECK, copied from `chains` (B.7)."""
    store.nodes.add(Node(
        id="untrusted_source:1",
        kind="untrusted_source",
        name="rag_corpus:catalogue.list",
        host_id="localhost",
        attributes={"source_type": "rag_corpus"},
    ))
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            """
            INSERT INTO chain_groups (id, untrusted_source_id, voter_outputs)
            VALUES ('chain_group:1', 'untrusted_source:1', 'not json')
            """
        )