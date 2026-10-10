"""Smoke tests: GraphStore facade — transactions, cleanup, integrity."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from aicorellator import GraphStore
from aicorellator.graph import Node


def test_context_manager_closes() -> None:
    """GraphStore works as a context manager and closes on exit."""
    path = tempfile.mktemp(suffix=".db")
    try:
        with GraphStore(path) as s:
            s.nodes.add(Node(id="n:1", kind="agent", name="A", host_id="localhost"))
        # After exit, connection should be closed
        with pytest.raises(sqlite3.ProgrammingError):
            s._conn.execute("SELECT 1")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_rollback_on_exception() -> None:
    """Exception inside `with` rolls back uncommitted changes."""
    path = tempfile.mktemp(suffix=".db")
    try:
        try:
            with GraphStore(path) as s:
                s.nodes.add(Node(id="n:1", kind="agent", name="A", host_id="localhost"))
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        # Reopen — node should not exist (transaction rolled back)
        with GraphStore(path) as s2:
            assert s2.nodes.get("n:1") is None
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_integrity_check(store: GraphStore) -> None:
    assert store.integrity_check() is True


def test_cleanup_removes_stale_nodes(store: GraphStore) -> None:
    """Cleanup deletes nodes marked stale and older than threshold."""
    store.nodes.add(Node(id="n:1", kind="agent", name="A", host_id="localhost"))
    store.nodes.mark_stale("n:1")

    # Manually age the node so it falls outside the retention window
    store._conn.execute(
        "UPDATE nodes SET updated_at = datetime('now', '-100 days') WHERE id = 'n:1'"
    )

    result = store.cleanup()
    assert result["nodes_deleted"] == 1
    assert store.nodes.get("n:1") is None


def test_cleanup_keeps_active_nodes(store: GraphStore) -> None:
    """Active nodes are never removed by cleanup."""
    store.nodes.add(Node(id="n:1", kind="agent", name="A", host_id="localhost"))
    store._conn.execute(
        "UPDATE nodes SET updated_at = datetime('now', '-100 days') WHERE id = 'n:1'"
    )
    result = store.cleanup()
    assert result["nodes_deleted"] == 0
    assert store.nodes.get("n:1") is not None