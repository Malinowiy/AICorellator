"""Reports repository — CRUD over the `reports` table.

The report itself is a bundle directory on disk. This table stores
only metadata. The `is_current` flag is enforced by a partial
unique index — only one current report per host.
"""

from __future__ import annotations

import sqlite3

from ..models import Report, utcnow


class ReportsRepository:
    """Thin wrapper over the `reports` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --------------------------------------------------------
    # Write
    # --------------------------------------------------------

    def add(self, report: Report) -> None:
        """Insert a report record.

        Raises IntegrityError if is_current = True and another
        current report already exists for the same host. Callers
        must archive the current report first.
        """
        self._conn.execute(
            """
            INSERT INTO reports (
                id, host_id, bundle_dir, tier, is_current,
                generated_at, metadata
            ) VALUES (
                :id, :host_id, :bundle_dir, :tier, :is_current,
                :generated_at, :metadata
            )
            """,
            report.to_row(),
        )

    def archive_current(self, host_id: str, timestamp: str) -> None:
        """Archive the current report for a host.

        Renames bundle_dir from 'current' to '<timestamp>' and
        clears is_current. Called before inserting a new current.
        Raises KeyError if no current report exists.
        """
        current = self.get_current(host_id)
        if current is None:
            raise KeyError(f"no current report for host: {host_id}")

        self._conn.execute(
            """
            UPDATE reports
            SET bundle_dir = :bundle_dir, is_current = 0
            WHERE id = :id
            """,
            {"id": current.id, "bundle_dir": timestamp},
        )

    def set_current(self, report_id: str) -> None:
        """Mark a report as current. Caller must have archived the
        previous current report for the same host."""
        self._conn.execute(
            "UPDATE reports SET is_current = 1 WHERE id = :id",
            {"id": report_id},
        )

    def delete(self, report_id: str) -> None:
        self._conn.execute("DELETE FROM reports WHERE id = :id", {"id": report_id})

    # --------------------------------------------------------
    # Read
    # --------------------------------------------------------

    def get(self, report_id: str) -> Report | None:
        row = self._conn.execute(
            "SELECT * FROM reports WHERE id = :id", {"id": report_id}
        ).fetchone()
        return Report.from_row(row) if row else None

    def get_current(self, host_id: str) -> Report | None:
        row = self._conn.execute(
            "SELECT * FROM reports WHERE host_id = :host_id AND is_current = 1",
            {"host_id": host_id},
        ).fetchone()
        return Report.from_row(row) if row else None

    def get_by_bundle(self, host_id: str, bundle_dir: str) -> Report | None:
        row = self._conn.execute(
            "SELECT * FROM reports "
            "WHERE host_id = :host_id AND bundle_dir = :bundle_dir",
            {"host_id": host_id, "bundle_dir": bundle_dir},
        ).fetchone()
        return Report.from_row(row) if row else None

    def list(
        self,
        host_id: str | None = None,
        tier: str | None = None,
        limit: int | None = None,
    ) -> list[Report]:
        query = "SELECT * FROM reports WHERE 1=1"
        params: dict = {}

        if host_id is not None:
            query += " AND host_id = :host_id"
            params["host_id"] = host_id
        if tier is not None:
            query += " AND tier = :tier"
            params["tier"] = tier

        query += " ORDER BY generated_at DESC"
        if limit is not None:
            query += " LIMIT :limit"
            params["limit"] = limit

        rows = self._conn.execute(query, params).fetchall()
        return [Report.from_row(r) for r in rows]

    def count(self, host_id: str | None = None) -> int:
        if host_id is None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM reports"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM reports WHERE host_id = :host_id",
                {"host_id": host_id},
            ).fetchone()
        return row["n"]