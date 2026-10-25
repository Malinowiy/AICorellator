"""Edges repository — CRUD over the `edges` table."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..models import Edge


class EdgesRepository:
    """Thin wrapper over the `edges` table.

    All methods take a sqlite3.Connection. Transactions are managed
    by GraphStore, not by this class.

    Note on host_id semantics: host_id is the "local" host for this
    edge — where the relationship was observed. For cross-host edges
    it may differ from either endpoint's host_id.
    See src/aicorellator/graph/schema.sql.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --------------------------------------------------------
    # Write
    # --------------------------------------------------------

    def add(self, edge: Edge) -> None:
        """Insert an edge. Raises sqlite3.IntegrityError on duplicate
        (source_id, target_id, kind, host_id) or missing endpoint."""
        self._conn.execute(
            """
            INSERT INTO edges (
                id, source_id, target_id, kind, host_id, precision,
                flow_kind, data_flow_confirmed,
                attributes, capabilities, observed_by, confidence, status,
                created_at, updated_at
            ) VALUES (
                :id, :source_id, :target_id, :kind, :host_id, :precision,
                :flow_kind, :data_flow_confirmed,
                :attributes, :capabilities, :observed_by, :confidence, :status,
                :created_at, :updated_at
            )
            """,
            edge.to_row(),
        )

    def upsert(self, edge: Edge) -> None:
        """Insert or update an edge by id.

        On conflict:
          - attributes are MERGED.
          - capabilities are UNIONED.
          - observed_by is UNIONED: who confirmed the edge accumulates
            across sources (ADR-0016).
          - precision is upgraded: 'compiler-verified' wins over
            'syntactic'.
          - flow_kind is upgraded: 'data'/'structural' win over the
            default 'control' (ADR-0015).
          - data_flow_confirmed is upgraded: a non-NULL value wins over
            NULL (framework detectors may enrich it).
          - confidence takes the max.
          - status is overwritten.
        """
        existing = self.get(edge.id)
        if existing is None:
            self.add(edge)
            return

        merged_attrs = {**existing.attributes, **edge.attributes}
        merged_caps = sorted(set(existing.capabilities) | set(edge.capabilities))
        merged_observed = sorted(set(existing.observed_by) | set(edge.observed_by))
        merged_confidence = max(existing.confidence, edge.confidence)
        merged_precision = (
            "compiler-verified"
            if "compiler-verified" in (existing.precision, edge.precision)
            else "syntactic"
        )
        # Prefer the more specific flow kind. 'control' is the default;
        # 'data' and 'structural' are explicit classifications.
        merged_flow_kind = edge.flow_kind
        if merged_flow_kind == "control" and existing.flow_kind in (
            "data", "structural",
        ):
            merged_flow_kind = existing.flow_kind
        merged_data_flow = (
            edge.data_flow_confirmed
            if edge.data_flow_confirmed is not None
            else existing.data_flow_confirmed
        )

        self._conn.execute(
            """
            UPDATE edges SET
                source_id    = :source_id,
                target_id    = :target_id,
                kind         = :kind,
                host_id      = :host_id,
                precision    = :precision,
                flow_kind    = :flow_kind,
                data_flow_confirmed = :data_flow_confirmed,
                attributes   = :attributes,
                capabilities = :capabilities,
                observed_by  = :observed_by,
                confidence   = :confidence,
                status       = :status
            WHERE id = :id
            """,
            {
                "id": edge.id,
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "kind": edge.kind,
                "host_id": edge.host_id,
                "precision": merged_precision,
                "flow_kind": merged_flow_kind,
                "data_flow_confirmed": merged_data_flow,
                "attributes": json.dumps(merged_attrs),
                "capabilities": json.dumps(merged_caps),
                "observed_by": json.dumps(merged_observed),
                "confidence": merged_confidence,
                "status": edge.status,
            },
        )

    def update_confidence(self, edge_id: str, delta: float) -> None:
        self._conn.execute(
            """
            UPDATE edges SET confidence = MAX(0.0, MIN(1.0, confidence + :delta))
            WHERE id = :id
            """,
            {"id": edge_id, "delta": delta},
        )

    def mark_stale(self, edge_id: str) -> None:
        self._conn.execute(
            "UPDATE edges SET status = 'stale' WHERE id = :id",
            {"id": edge_id},
        )

    def delete(self, edge_id: str) -> None:
        self._conn.execute("DELETE FROM edges WHERE id = :id", {"id": edge_id})

    # --------------------------------------------------------
    # Read
    # --------------------------------------------------------

    def get(self, edge_id: str) -> Edge | None:
        row = self._conn.execute(
            "SELECT * FROM edges WHERE id = :id", {"id": edge_id}
        ).fetchone()
        return Edge.from_row(row) if row else None

    def list(
        self,
        source_id: str | None = None,
        target_id: str | None = None,
        kind: str | None = None,
        host_id: str | None = None,
        precision: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[Edge]:
        query = "SELECT * FROM edges WHERE 1=1"
        params: dict[str, Any] = {}

        if source_id is not None:
            query += " AND source_id = :source_id"
            params["source_id"] = source_id
        if target_id is not None:
            query += " AND target_id = :target_id"
            params["target_id"] = target_id
        if kind is not None:
            query += " AND kind = :kind"
            params["kind"] = kind
        if host_id is not None:
            query += " AND host_id = :host_id"
            params["host_id"] = host_id
        if precision is not None:
            query += " AND precision = :precision"
            params["precision"] = precision
        if status is not None:
            query += " AND status = :status"
            params["status"] = status

        query += " ORDER BY id"
        if limit is not None:
            query += " LIMIT :limit"
            params["limit"] = limit

        rows = self._conn.execute(query, params).fetchall()
        return [Edge.from_row(r) for r in rows]

    def count(self, kind: str | None = None) -> int:
        if kind is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM edges").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM edges WHERE kind = :kind",
                {"kind": kind},
            ).fetchone()
        return row["n"]

    def exists(self, edge_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM edges WHERE id = :id LIMIT 1", {"id": edge_id}
        ).fetchone()
        return row is not None

    def existing_ids(self, edge_ids: list[str]) -> list[str]:
        """Return the subset of edge_ids that exist in the table.

        Used by Layer 3 validation (ADR-0006). Preserves input order.
        SQLite has a limit on the number of parameters per query
        (default 999), so we chunk.
        """
        if not edge_ids:
            return []

        found: set[str] = set()
        chunk_size = 900
        for i in range(0, len(edge_ids), chunk_size):
            chunk = edge_ids[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT id FROM edges WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            found.update(r["id"] for r in rows)

        return [eid for eid in edge_ids if eid in found]