"""Chains repository — CRUD over the `chains` table.

A chain is an evaluated compromise path produced by the correlator
(ADR-0005, ADR-0006). Candidate chains are NOT stored — only
evaluated ones.

No foreign keys on chain_nodes / chain_edges. Validation is Layer 3
app logic. Chains with validated = 0 may reference non-existent
nodes/edges — kept for provenance, not for reports.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..models import Chain


class ChainsRepository:
    """Thin wrapper over the `chains` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --------------------------------------------------------
    # Write
    # --------------------------------------------------------

    def add(self, chain: Chain) -> None:
        """Insert a chain. Raises IntegrityError on duplicate id."""
        self._conn.execute(
            """
            INSERT INTO chains (
                id, chain_nodes, chain_edges, verdict, confidence,
                reasoning, voter_outputs, prompt_version, validated,
                status, created_at, updated_at
            ) VALUES (
                :id, :chain_nodes, :chain_edges, :verdict, :confidence,
                :reasoning, :voter_outputs, :prompt_version, :validated,
                :status, :created_at, :updated_at
            )
            """,
            chain.to_row(),
        )

    def upsert(self, chain: Chain) -> None:
        """Insert or update a chain by id.

        Chains are typically written once per evaluation run. Upsert
        is provided for re-runs with the same chain_id — the record
        is overwritten with the latest verdict, voter outputs, and
        validation result.
        """
        existing = self.get(chain.id)
        if existing is None:
            self.add(chain)
            return

        self._conn.execute(
            """
            UPDATE chains SET
                chain_nodes    = :chain_nodes,
                chain_edges    = :chain_edges,
                verdict        = :verdict,
                confidence     = :confidence,
                reasoning      = :reasoning,
                voter_outputs  = :voter_outputs,
                prompt_version = :prompt_version,
                validated      = :validated,
                status         = :status
            WHERE id = :id
            """,
            {
                "id": chain.id,
                "chain_nodes": json.dumps(chain.chain_nodes),
                "chain_edges": json.dumps(chain.chain_edges),
                "verdict": chain.verdict,
                "confidence": chain.confidence,
                "reasoning": chain.reasoning,
                "voter_outputs": json.dumps(chain.voter_outputs),
                "prompt_version": chain.prompt_version,
                "validated": 1 if chain.validated else 0,
                "status": chain.status,
            },
        )

    def mark_stale(self, chain_id: str) -> None:
        self._conn.execute(
            "UPDATE chains SET status = 'stale' WHERE id = :id",
            {"id": chain_id},
        )

    def mark_stale_for_node(self, node_id: str) -> int:
        """Mark all chains containing a given node as stale.

        Chains are JSON arrays of node IDs. SQLite has no native
        array search, so we use json_each. Called when a node is
        invalidated — any chain through it is no longer trustworthy.
        """
        cur = self._conn.execute(
            """
            UPDATE chains
            SET status = 'stale'
            WHERE status = 'active'
              AND EXISTS (
                  SELECT 1 FROM json_each(chains.chain_nodes)
                  WHERE json_each.value = :node_id
              )
            """,
            {"node_id": node_id},
        )
        return cur.rowcount

    def delete(self, chain_id: str) -> None:
        self._conn.execute("DELETE FROM chains WHERE id = :id", {"id": chain_id})

    # --------------------------------------------------------
    # Read
    # --------------------------------------------------------

    def get(self, chain_id: str) -> Chain | None:
        row = self._conn.execute(
            "SELECT * FROM chains WHERE id = :id", {"id": chain_id}
        ).fetchone()
        return Chain.from_row(row) if row else None

    def list(
        self,
        verdict: str | None = None,
        status: str | None = None,
        validated: bool | None = None,
        min_confidence: float | None = None,
        prompt_version: str | None = None,
        limit: int | None = None,
    ) -> list[Chain]:
        query = "SELECT * FROM chains WHERE 1=1"
        params: dict[str, Any] = {}

        if verdict is not None:
            query += " AND verdict = :verdict"
            params["verdict"] = verdict
        if status is not None:
            query += " AND status = :status"
            params["status"] = status
        if validated is not None:
            query += " AND validated = :validated"
            params["validated"] = 1 if validated else 0
        if min_confidence is not None:
            query += " AND confidence >= :min_confidence"
            params["min_confidence"] = min_confidence
        if prompt_version is not None:
            query += " AND prompt_version = :prompt_version"
            params["prompt_version"] = prompt_version

        query += " ORDER BY confidence DESC, created_at DESC, id"
        if limit is not None:
            query += " LIMIT :limit"
            params["limit"] = limit

        rows = self._conn.execute(query, params).fetchall()
        return [Chain.from_row(r) for r in rows]

    def list_actionable(self, min_confidence: float = 0.5) -> list[Chain]:
        """Chains ready for a report: validated, exploitable, confident.

        This is the query that Reporting uses to build the report body.
        """
        rows = self._conn.execute(
            """
            SELECT * FROM chains
            WHERE status = 'active'
              AND validated = 1
              AND verdict = 'exploitable'
              AND confidence >= :min_confidence
            ORDER BY confidence DESC, created_at DESC
            """,
            {"min_confidence": min_confidence},
        ).fetchall()
        return [Chain.from_row(r) for r in rows]

    def count(
        self,
        verdict: str | None = None,
        status: str | None = None,
    ) -> int:
        query = "SELECT COUNT(*) AS n FROM chains WHERE 1=1"
        params: dict[str, Any] = {}
        if verdict is not None:
            query += " AND verdict = :verdict"
            params["verdict"] = verdict
        if status is not None:
            query += " AND status = :status"
            params["status"] = status
        return self._conn.execute(query, params).fetchone()["n"]

    def summary(self) -> dict[str, int]:
        """Counts by verdict for actionable chains only."""
        rows = self._conn.execute(
            """
            SELECT verdict, COUNT(*) AS n
            FROM chains
            WHERE status = 'active' AND validated = 1
            GROUP BY verdict
            """
        ).fetchall()
        result = {"exploitable": 0, "not_exploitable": 0, "uncertain": 0}
        for r in rows:
            result[r["verdict"]] = r["n"]
        result["total"] = sum(result.values())
        return result