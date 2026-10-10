"""Shared fixtures for Phase 0 smoke tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from aicorellator import GraphStore
from aicorellator.graph import Edge, Finding, Node


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A fresh SQLite file per test."""
    return tmp_path / "test.db"


@pytest.fixture
def store(db_path: Path):
    """A fresh GraphStore, closed after the test."""
    s = GraphStore(db_path)
    yield s
    s.close()


@pytest.fixture
def sample_graph(store: GraphStore) -> dict[str, str]:
    """Build a minimal entry → agent → sink graph.

    Returns a dict of ids for use in assertions.

        entry  --uses_model-->  agent  --has_access_to-->  sink
    """
    ids = {
        "entry": "entry_point:" + "e" * 16,
        "agent": "agent:" + "a" * 16,
        "sink":  "sink:" + "s" * 16,
        "e1":    "edge:" + "1" * 16,
        "e2":    "edge:" + "2" * 16,
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
        id=ids["sink"], kind="sink", name="shell",
        host_id="localhost",
    ))

    store.edges.upsert(Edge(
        id=ids["e1"], source_id=ids["entry"], target_id=ids["agent"],
        kind="uses_model", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id=ids["e2"], source_id=ids["agent"], target_id=ids["sink"],
        kind="has_access_to", host_id="localhost",
    ))

    return ids