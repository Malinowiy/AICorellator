"""GraphStore — the public API over the SQLite graph.

Facade that:
  - opens the connection and applies the schema
  - assembles repositories and the query layer
  - manages transactions
  - provides bootstrap for the local host

Usage:
    with GraphStore("aicorellator.db") as store:
        store.nodes.upsert(node)
        store.edges.upsert(edge)
        chains = store.query.find_chains()
"""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path
from types import TracebackType

from .query import QueryLayer
from .repositories.chains import ChainsRepository
from .repositories.edges import EdgesRepository
from .repositories.findings import FindingsRepository
from .repositories.hosts import HostsRepository
from .repositories.nodes import NodesRepository
from .repositories.reports import ReportsRepository
from .repositories.state import StateRepository
from .repositories.task_state import TaskStateRepository


SCHEMA_RESOURCE = "schema.sql"
DEFAULT_DB_PATH = "aicorellator.db"


class GraphStore:
    """SQLite-backed live graph.

    Not thread-safe. Each thread should have its own GraphStore,
    or serialize access externally. SQLite WAL mode allows
    concurrent reads from multiple connections.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._apply_schema()

        # Repositories
        self.hosts = HostsRepository(self._conn)
        self.nodes = NodesRepository(self._conn)
        self.edges = EdgesRepository(self._conn)
        self.findings = FindingsRepository(self._conn)
        self.chains = ChainsRepository(self._conn)
        self.reports = ReportsRepository(self._conn)
        self.state = StateRepository(self._conn)
        self.task_state = TaskStateRepository(self._conn)

        # Query Layer
        self.query = QueryLayer(self._conn)

    # --------------------------------------------------------
    # Schema
    # --------------------------------------------------------

    def _apply_schema(self) -> None:
        """Apply schema.sql. Idempotent — uses CREATE TABLE IF NOT EXISTS."""
        schema_path = files("aicorellator.graph").joinpath(SCHEMA_RESOURCE)
        schema_sql = schema_path.read_text(encoding="utf-8")
        self._conn.executescript(schema_sql)
        self._conn.commit()

    # --------------------------------------------------------
    # Transactions
    # --------------------------------------------------------

    def begin(self) -> None:
        self._conn.execute("BEGIN")

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    # --------------------------------------------------------
    # Maintenance
    # --------------------------------------------------------

    def cleanup(self, host_id: str = "localhost") -> dict[str, int]:
        """Run the cleanup policy.

        Deletes nodes, findings, and chains older than their
        respective thresholds. Thresholds come from state keys:
          cleanup_stale_days
          cleanup_findings_days
          cleanup_chains_days

        Returns counts of deleted rows per category.
        Report bundle cleanup (disk) is a Phase 3 concern.
        """
        stale_days = self.state.get_int(host_id, "cleanup_stale_days")
        findings_days = self.state.get_int(host_id, "cleanup_findings_days")
        chains_days = self.state.get_int(host_id, "cleanup_chains_days")

        cur = self._conn.execute(
            """
            DELETE FROM nodes
            WHERE status = 'stale'
              AND updated_at < datetime('now', :offset)
            """,
            {"offset": f"-{stale_days} days"},
        )
        nodes_deleted = cur.rowcount

        cur = self._conn.execute(
            """
            DELETE FROM findings
            WHERE status = 'stale'
              AND updated_at < datetime('now', :offset)
            """,
            {"offset": f"-{findings_days} days"},
        )
        findings_deleted = cur.rowcount

        cur = self._conn.execute(
            """
            DELETE FROM chains
            WHERE status = 'stale'
              AND updated_at < datetime('now', :offset)
            """,
            {"offset": f"-{chains_days} days"},
        )
        chains_deleted = cur.rowcount

        self._conn.commit()

        return {
            "nodes_deleted": nodes_deleted,
            "findings_deleted": findings_deleted,
            "chains_deleted": chains_deleted,
        }

    def integrity_check(self) -> bool:
        row = self._conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] == "ok"

    def vacuum(self) -> None:
        self._conn.execute("VACUUM")

    def close(self) -> None:
        self._conn.close()

    # --------------------------------------------------------
    # Context manager
    # --------------------------------------------------------

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()