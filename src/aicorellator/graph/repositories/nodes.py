"""Nodes repository — CRUD over the `nodes` table."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..models import Node


class NodesRepository:
    """Thin wrapper over the `nodes` table.

    All methods take a sqlite3.Connection. Transactions are managed
    by GraphStore, not by this class.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --------------------------------------------------------
    # Write
    # --------------------------------------------------------

    def add(self, node: Node) -> None:
        """Insert a node. Raises sqlite3.IntegrityError on duplicate id."""
        row = node.to_row()
        self._conn.execute(
            """
            INSERT INTO nodes (
                id, kind, name, host_id, attributes,
                confidence, content_hash, status,
                created_at, updated_at
            ) VALUES (
                :id, :kind, :name, :host_id, :attributes,
                :confidence, :content_hash, :status,
                :created_at, :updated_at
            )
            """,
            row,
        )

    def upsert(self, node: Node) -> None:
        """Insert or update a node by id.

        On conflict, attributes are MERGED (not replaced): new keys
        are added, existing keys are overwritten. confidence takes
        the max of old and new. content_hash, status, kind, name,
        host_id are overwritten.
        """
        existing = self.get(node.id)
        if existing is None:
            self.add(node)
            return

        merged_attrs = {**existing.attributes, **node.attributes}
        merged_confidence = max(existing.confidence, node.confidence)

        self._conn.execute(
            """
            UPDATE nodes SET
                kind         = :kind,
                name         = :name,
                host_id      = :host_id,
                attributes   = :attributes,
                confidence   = :confidence,
                content_hash = :content_hash,
                status       = :status
            WHERE id = :id
            """,
            {
                "id": node.id,
                "kind": node.kind,
                "name": node.name,
                "host_id": node.host_id,
                "attributes": json.dumps(merged_attrs),
                "confidence": merged_confidence,
                "content_hash": node.content_hash,
                "status": node.status,
            },
        )

    def update_attr(self, node_id: str, key: str, value: Any) -> None:
        """Set a single attribute key on a node. Creates key if missing."""
        node = self.get(node_id)
        if node is None:
            raise KeyError(f"node not found: {node_id}")
        node.attributes[key] = value
        self._conn.execute(
            "UPDATE nodes SET attributes = :attributes WHERE id = :id",
            {"id": node_id, "attributes": json.dumps(node.attributes)},
        )

    def update_confidence(self, node_id: str, delta: float) -> None:
        """Apply a delta to a node's confidence, clamped to [0.0, 1.0]."""
        self._conn.execute(
            """
            UPDATE nodes SET confidence = MAX(0.0, MIN(1.0, confidence + :delta))
            WHERE id = :id
            """,
            {"id": node_id, "delta": delta},
        )

    def mark_stale(self, node_id: str) -> None:
        """Set status = 'stale'. Idempotent."""
        self._conn.execute(
            "UPDATE nodes SET status = 'stale' WHERE id = :id",
            {"id": node_id},
        )

    def mark_active(self, node_id: str) -> None:
        """Set status = 'active' and reset confidence to 1.0."""
        self._conn.execute(
            "UPDATE nodes SET status = 'active', confidence = 1.0 WHERE id = :id",
            {"id": node_id},
        )

    # --------------------------------------------------------
    # Read
    # --------------------------------------------------------

    def get(self, node_id: str) -> Node | None:
        row = self._conn.execute(
            "SELECT * FROM nodes WHERE id = :id", {"id": node_id}
        ).fetchone()
        return Node.from_row(row) if row else None

    def list(
        self,
        kind: str | None = None,
        host_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[Node]:
        query = "SELECT * FROM nodes WHERE 1=1"
        params: dict[str, Any] = {}

        if kind is not None:
            query += " AND kind = :kind"
            params["kind"] = kind
        if host_id is not None:
            query += " AND host_id = :host_id"
            params["host_id"] = host_id
        if status is not None:
            query += " AND status = :status"
            params["status"] = status

        query += " ORDER BY id"
        if limit is not None:
            query += " LIMIT :limit"
            params["limit"] = limit

        rows = self._conn.execute(query, params).fetchall()
        return [Node.from_row(r) for r in rows]

    def count(self, kind: str | None = None) -> int:
        if kind is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM nodes WHERE kind = :kind",
                {"kind": kind},
            ).fetchone()
        return row["n"]

    def exists(self, node_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM nodes WHERE id = :id LIMIT 1", {"id": node_id}
        ).fetchone()
        return row is not None