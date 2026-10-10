"""Findings repository — CRUD over the `findings` table.

A finding is a scanner-reported weakness (AAK, LLM-scanner,
MCP-scanner) attached to EITHER a node OR an edge — never both,
never neither. Findings are INPUTS to chain evaluation, not chains
themselves.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..models import Finding


class FindingsRepository:
    """Thin wrapper over the `findings` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

    @staticmethod
    def _validate_target(finding: Finding) -> None:
        """Enforce node XOR edge.

        The schema has a CHECK constraint that enforces the same
        invariant, but validating here gives a clear error message
        instead of a raw IntegrityError.
        """
        has_node = finding.node_id is not None
        has_edge = finding.edge_id is not None

        if has_node and has_edge:
            raise ValueError(
                f"finding {finding.id!r}: cannot attach to both "
                f"node ({finding.node_id!r}) and edge ({finding.edge_id!r})"
            )
        if not has_node and not has_edge:
            raise ValueError(
                f"finding {finding.id!r}: must attach to either "
                f"a node or an edge"
            )

    # --------------------------------------------------------
    # Write
    # --------------------------------------------------------

    def add(self, finding: Finding) -> None:
        """Insert a finding.

        Raises:
            ValueError if the finding is not attached to exactly
            one of node_id / edge_id.
            sqlite3.IntegrityError on duplicate id or missing FK.
        """
        self._validate_target(finding)
        self._conn.execute(
            """
            INSERT INTO findings (
                id, node_id, edge_id, scanner, rule_id,
                severity, title, description, evidence,
                confidence, status, created_at, updated_at
            ) VALUES (
                :id, :node_id, :edge_id, :scanner, :rule_id,
                :severity, :title, :description, :evidence,
                :confidence, :status, :created_at, :updated_at
            )
            """,
            finding.to_row(),
        )

    def upsert(self, finding: Finding) -> None:
        """Insert or update a finding by id.

        Findings are typically immutable once recorded — a scanner
        either reported a weakness or it did not. Upsert is provided
        for the case where a scanner re-runs and returns an updated
        record for the same rule_id on the same target.

        Attributes merged: evidence (dict merge), confidence (max).
        """
        self._validate_target(finding)

        existing = self.get(finding.id)
        if existing is None:
            self.add(finding)
            return

        merged_evidence = {**existing.evidence, **finding.evidence}
        merged_confidence = max(existing.confidence, finding.confidence)

        self._conn.execute(
            """
            UPDATE findings SET
                scanner      = :scanner,
                rule_id      = :rule_id,
                severity     = :severity,
                title        = :title,
                description  = :description,
                evidence     = :evidence,
                confidence   = :confidence,
                status       = :status
            WHERE id = :id
            """,
            {
                "id": finding.id,
                "scanner": finding.scanner,
                "rule_id": finding.rule_id,
                "severity": finding.severity,
                "title": finding.title,
                "description": finding.description,
                "evidence": json.dumps(merged_evidence),
                "confidence": merged_confidence,
                "status": finding.status,
            },
        )

    def mark_stale(self, finding_id: str) -> None:
        self._conn.execute(
            "UPDATE findings SET status = 'stale' WHERE id = :id",
            {"id": finding_id},
        )

    def mark_stale_for_node(self, node_id: str) -> int:
        """Mark all findings attached to a node as stale.

        Used when a node's content_hash changes — findings attached
        to the previous version are no longer valid. Returns count.
        """
        cur = self._conn.execute(
            "UPDATE findings SET status = 'stale' "
            "WHERE node_id = :node_id AND status = 'active'",
            {"node_id": node_id},
        )
        return cur.rowcount

    def mark_stale_for_edge(self, edge_id: str) -> int:
        cur = self._conn.execute(
            "UPDATE findings SET status = 'stale' "
            "WHERE edge_id = :edge_id AND status = 'active'",
            {"edge_id": edge_id},
        )
        return cur.rowcount

    def delete(self, finding_id: str) -> None:
        self._conn.execute("DELETE FROM findings WHERE id = :id", {"id": finding_id})

    # --------------------------------------------------------
    # Read
    # --------------------------------------------------------

    def get(self, finding_id: str) -> Finding | None:
        row = self._conn.execute(
            "SELECT * FROM findings WHERE id = :id", {"id": finding_id}
        ).fetchone()
        return Finding.from_row(row) if row else None

    def list(
        self,
        node_id: str | None = None,
        edge_id: str | None = None,
        scanner: str | None = None,
        severity: str | None = None,
        rule_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[Finding]:
        query = "SELECT * FROM findings WHERE 1=1"
        params: dict[str, Any] = {}

        if node_id is not None:
            query += " AND node_id = :node_id"
            params["node_id"] = node_id
        if edge_id is not None:
            query += " AND edge_id = :edge_id"
            params["edge_id"] = edge_id
        if scanner is not None:
            query += " AND scanner = :scanner"
            params["scanner"] = scanner
        if severity is not None:
            query += " AND severity = :severity"
            params["severity"] = severity
        if rule_id is not None:
            query += " AND rule_id = :rule_id"
            params["rule_id"] = rule_id
        if status is not None:
            query += " AND status = :status"
            params["status"] = status

        query += " ORDER BY created_at DESC, id"
        if limit is not None:
            query += " LIMIT :limit"
            params["limit"] = limit

        rows = self._conn.execute(query, params).fetchall()
        return [Finding.from_row(r) for r in rows]

    def count(
        self,
        severity: str | None = None,
        status: str | None = None,
    ) -> int:
        query = "SELECT COUNT(*) AS n FROM findings WHERE 1=1"
        params: dict[str, Any] = {}
        if severity is not None:
            query += " AND severity = :severity"
            params["severity"] = severity
        if status is not None:
            query += " AND status = :status"
            params["status"] = status
        return self._conn.execute(query, params).fetchone()["n"]

    def summary(self) -> dict[str, int]:
        """Counts by severity, active only. For report headers."""
        rows = self._conn.execute(
            """
            SELECT severity, COUNT(*) AS n
            FROM findings
            WHERE status = 'active'
            GROUP BY severity
            """
        ).fetchall()
        result = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for r in rows:
            result[r["severity"]] = r["n"]
        result["total"] = sum(result.values())
        return result