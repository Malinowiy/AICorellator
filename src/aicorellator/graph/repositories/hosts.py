"""Hosts repository — CRUD over the `hosts` table.

In Phase 0 there is exactly one row: 'localhost'. In Phase 3 this
table holds every registered Agent host.
"""

from __future__ import annotations

import sqlite3

from ..models import Host, utcnow


class HostsRepository:
    """Thin wrapper over the `hosts` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --------------------------------------------------------
    # Write
    # --------------------------------------------------------

    def register(self, host: Host) -> None:
        """Insert a host. Raises IntegrityError on duplicate id."""
        self._conn.execute(
            """
            INSERT INTO hosts (
                id, hostname, ip, platform, agent_version,
                last_heartbeat, status, created_at
            ) VALUES (
                :id, :hostname, :ip, :platform, :agent_version,
                :last_heartbeat, :status, :created_at
            )
            """,
            host.__dict__,
        )

    def upsert(self, host: Host) -> None:
        """Insert or update a host by id."""
        existing = self.get(host.id)
        if existing is None:
            self.register(host)
            return

        self._conn.execute(
            """
            UPDATE hosts SET
                hostname       = :hostname,
                ip             = :ip,
                platform       = :platform,
                agent_version  = :agent_version,
                last_heartbeat = :last_heartbeat,
                status         = :status
            WHERE id = :id
            """,
            {
                "id": host.id,
                "hostname": host.hostname,
                "ip": host.ip,
                "platform": host.platform,
                "agent_version": host.agent_version,
                "last_heartbeat": host.last_heartbeat,
                "status": host.status,
            },
        )

    def update_heartbeat(self, host_id: str) -> None:
        """Set last_heartbeat to now. Called by Agent on every heartbeat."""
        self._conn.execute(
            "UPDATE hosts SET last_heartbeat = :ts, status = 'active' WHERE id = :id",
            {"id": host_id, "ts": utcnow()},
        )

    def mark_unreachable(self, host_id: str) -> None:
        self._conn.execute(
            "UPDATE hosts SET status = 'unreachable' WHERE id = :id",
            {"id": host_id},
        )

    # --------------------------------------------------------
    # Read
    # --------------------------------------------------------

    def get(self, host_id: str) -> Host | None:
        row = self._conn.execute(
            "SELECT * FROM hosts WHERE id = :id", {"id": host_id}
        ).fetchone()
        return Host.from_row(row) if row else None

    def list(self, status: str | None = None) -> list[Host]:
        query = "SELECT * FROM hosts WHERE 1=1"
        params: dict = {}
        if status is not None:
            query += " AND status = :status"
            params["status"] = status
        query += " ORDER BY id"
        rows = self._conn.execute(query, params).fetchall()
        return [Host.from_row(r) for r in rows]

    def exists(self, host_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM hosts WHERE id = :id LIMIT 1", {"id": host_id}
        ).fetchone()
        return row is not None