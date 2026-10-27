"""Chain groups repository — CRUD over the `chain_groups` table.

A chain group is a set of chains that meet through an
`untrusted_source` (ADR-0010): one side writes into the channel, the
other reads from it on its way to a sink. Neither side is dangerous
alone.

No domain logic here. Scoring, ranking and selection live in the
Query Layer (`QueryLayer.prioritize_chain_groups`, ADR-0012); this
class only moves rows.

`add`/`upsert` split the row by owner (ADR-0012 §3):

  - 1.3c owns `name`, the id sets, the anchor, `score`,
    `score_components`, `priority`, `status`. `upsert` refreshes
    exactly those, every time.
  - 1.3d/1.3e own the evaluation columns (`verdict`, `confidence`,
    `reasoning`, `validated`, and A.4's `validated_kind`). `upsert`
    writes them only when the incoming record carries a verdict, so
    re-running prioritization over a graph cannot discard a verdict
    that was already paid for, and 1.3e's post-evaluation write can
    land on a row 1.3c created.
  - the group-level `lethal_trifecta` column has no writer yet: 1.3d
    wrote the per-*node* attribute, and nothing projects it onto the
    group row. `upsert` leaves it as `add` found it.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..models import ChainGroupRecord


class ChainGroupsRepository:
    """Thin wrapper over the `chain_groups` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --------------------------------------------------------
    # Write
    # --------------------------------------------------------

    def add(self, group: ChainGroupRecord) -> None:
        """Insert a group. Raises IntegrityError on duplicate id."""
        self._conn.execute(
            """
            INSERT INTO chain_groups (
                id, name, untrusted_source_id, write_chain_ids,
                read_chain_ids, lethal_trifecta, score, score_components,
                verdict, confidence, reasoning, validated, validated_kind,
                priority, status, created_at, updated_at
            ) VALUES (
                :id, :name, :untrusted_source_id, :write_chain_ids,
                :read_chain_ids, :lethal_trifecta, :score, :score_components,
                :verdict, :confidence, :reasoning, :validated,
                :validated_kind, :priority, :status, :created_at, :updated_at
            )
            """,
            group.to_row(),
        )

    def upsert(self, group: ChainGroupRecord) -> None:
        """Insert or refresh a group by id.

        On conflict, two kinds of columns are treated differently:

          - `name`, the id sets, the anchor, `score`, `score_components`,
            `priority`, `status` are always refreshed — they are 1.3c's,
            and a re-run of prioritization must win.
          - `verdict`, `confidence`, `reasoning`, `validated`,
            `validated_kind` are written only when the incoming record
            carries a verdict (`verdict is not None`). 1.3c's
            `to_record()` rows have none, so a second prioritization
            run (or a delta rescan) cannot erase a verdict that was
            already paid for; 1.3e's evaluated row does have one, so
            its write lands.
            `validated` travels with the verdict rather than on its own:
            a row with a verdict has been through Layer 3, and the flag
            says whether it survived; `validated_kind` (A.4) travels
            with it for the same reason — it is the label of that same
            check.

        `created_at` is left alone too: it is when the group was first
        seen.
        """
        existing = self.get(group.id)
        if existing is None:
            self.add(group)
            return

        assignments = [
            "name = :name",
            "untrusted_source_id = :untrusted_source_id",
            "write_chain_ids = :write_chain_ids",
            "read_chain_ids = :read_chain_ids",
            "score = :score",
            "score_components = :score_components",
            "priority = :priority",
            "status = :status",
        ]
        params: dict[str, Any] = {
            "id": group.id,
            "name": group.name,
            "untrusted_source_id": group.untrusted_source_id,
            "write_chain_ids": json.dumps(group.write_chain_ids),
            "read_chain_ids": json.dumps(group.read_chain_ids),
            "score": group.score,
            "score_components": json.dumps(group.score_components),
            "priority": group.priority,
            "status": group.status,
        }

        if group.verdict is not None:
            assignments += [
                "verdict = :verdict",
                "confidence = :confidence",
                "reasoning = :reasoning",
                "validated = :validated",
                "validated_kind = :validated_kind",
            ]
            params["verdict"] = group.verdict
            params["confidence"] = group.confidence
            params["reasoning"] = group.reasoning
            params["validated"] = 1 if group.validated else 0
            params["validated_kind"] = group.validated_kind

        self._conn.execute(
            f"UPDATE chain_groups SET {', '.join(assignments)} WHERE id = :id",
            params,
        )

    def mark_stale(self, group_id: str) -> None:
        """Set status = 'stale'. Idempotent."""
        self._conn.execute(
            "UPDATE chain_groups SET status = 'stale' WHERE id = :id",
            {"id": group_id},
        )

    # --------------------------------------------------------
    # Read
    # --------------------------------------------------------

    def get(self, group_id: str) -> ChainGroupRecord | None:
        row = self._conn.execute(
            "SELECT * FROM chain_groups WHERE id = :id", {"id": group_id}
        ).fetchone()
        return ChainGroupRecord.from_row(row) if row else None

    def list(
        self,
        status: str | None = None,
        priority: str | None = None,
        min_score: float | None = None,
        limit: int | None = None,
    ) -> list[ChainGroupRecord]:
        """Groups, best first.

        Ordered by `score DESC, id` — the same total order
        prioritization ranks with, so a caller that re-reads the table
        sees the sequence that was selected.
        """
        query = "SELECT * FROM chain_groups WHERE 1=1"
        params: dict[str, Any] = {}

        if status is not None:
            query += " AND status = :status"
            params["status"] = status
        if priority is not None:
            query += " AND priority = :priority"
            params["priority"] = priority
        if min_score is not None:
            query += " AND score >= :min_score"
            params["min_score"] = min_score

        query += " ORDER BY score DESC, id"
        if limit is not None:
            query += " LIMIT :limit"
            params["limit"] = limit

        rows = self._conn.execute(query, params).fetchall()
        return [ChainGroupRecord.from_row(r) for r in rows]

    def list_by_source(
        self,
        source_id: str,
        status: str | None = None,
    ) -> list[ChainGroupRecord]:
        """Groups anchored on one untrusted source, best first."""
        query = (
            "SELECT * FROM chain_groups WHERE untrusted_source_id = :source"
        )
        params: dict[str, Any] = {"source": source_id}
        if status is not None:
            query += " AND status = :status"
            params["status"] = status
        query += " ORDER BY score DESC, id"

        rows = self._conn.execute(query, params).fetchall()
        return [ChainGroupRecord.from_row(r) for r in rows]

    def count(
        self,
        status: str | None = None,
        source_id: str | None = None,
    ) -> int:
        query = "SELECT COUNT(*) AS n FROM chain_groups WHERE 1=1"
        params: dict[str, Any] = {}
        if status is not None:
            query += " AND status = :status"
            params["status"] = status
        if source_id is not None:
            query += " AND untrusted_source_id = :source"
            params["source"] = source_id
        return self._conn.execute(query, params).fetchone()["n"]
