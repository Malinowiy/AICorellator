"""TaskState repository — queue counters, per host per kind.

`scan_done` is defined as "all tasks in terminal state
(done | failed | timeout)", not "active_count = 0".
"""

from __future__ import annotations

import sqlite3

from ..models import TaskCounter, utcnow


class TaskStateRepository:
    """Counters for the enrichment queue."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --------------------------------------------------------
    # Task lifecycle
    # --------------------------------------------------------

    def task_started(self, host_id: str, kind: str) -> None:
        """A task entered the queue."""
        self._conn.execute(
            """
            UPDATE task_state
            SET active_count = active_count + 1,
                updated_at = :ts
            WHERE host_id = :host_id AND kind = :kind
            """,
            {"host_id": host_id, "kind": kind, "ts": utcnow()},
        )

    def task_done(self, host_id: str, kind: str) -> None:
        """A task completed successfully."""
        self._conn.execute(
            """
            UPDATE task_state
            SET active_count = MAX(0, active_count - 1),
                done_count   = done_count + 1,
                last_completed_at = :ts,
                updated_at   = :ts
            WHERE host_id = :host_id AND kind = :kind
            """,
            {"host_id": host_id, "kind": kind, "ts": utcnow()},
        )

    def task_failed(self, host_id: str, kind: str) -> None:
        """A task failed terminally (after retries)."""
        self._conn.execute(
            """
            UPDATE task_state
            SET active_count = MAX(0, active_count - 1),
                failed_count = failed_count + 1,
                last_completed_at = :ts,
                updated_at   = :ts
            WHERE host_id = :host_id AND kind = :kind
            """,
            {"host_id": host_id, "kind": kind, "ts": utcnow()},
        )

    def task_timeout(self, host_id: str, kind: str) -> None:
        """A task exceeded its timeout."""
        self._conn.execute(
            """
            UPDATE task_state
            SET active_count  = MAX(0, active_count - 1),
                timeout_count = timeout_count + 1,
                last_completed_at = :ts,
                updated_at    = :ts
            WHERE host_id = :host_id AND kind = :kind
            """,
            {"host_id": host_id, "kind": kind, "ts": utcnow()},
        )

    def mark_cycle_complete(self, host_id: str, kind: str) -> None:
        """Dynamic worker finished one monitoring cycle."""
        self._conn.execute(
            """
            UPDATE task_state
            SET last_completed_at = :ts,
                updated_at = :ts
            WHERE host_id = :host_id AND kind = :kind
            """,
            {"host_id": host_id, "kind": kind, "ts": utcnow()},
        )

    def reset(self, host_id: str, kind: str) -> None:
        """Reset counters. Used at the start of a fresh pipeline run."""
        self._conn.execute(
            """
            UPDATE task_state
            SET active_count = 0,
                done_count = 0,
                failed_count = 0,
                timeout_count = 0,
                updated_at = :ts
            WHERE host_id = :host_id AND kind = :kind
            """,
            {"host_id": host_id, "kind": kind, "ts": utcnow()},
        )

    # --------------------------------------------------------
    # Read
    # --------------------------------------------------------

    def get(self, host_id: str, kind: str) -> TaskCounter | None:
        row = self._conn.execute(
            "SELECT * FROM task_state WHERE host_id = :host_id AND kind = :kind",
            {"host_id": host_id, "kind": kind},
        ).fetchone()
        return TaskCounter.from_row(row) if row else None

    def list(self, host_id: str | None = None) -> list[TaskCounter]:
        if host_id is None:
            rows = self._conn.execute(
                "SELECT * FROM task_state ORDER BY host_id, kind"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM task_state WHERE host_id = :host_id ORDER BY kind",
                {"host_id": host_id},
            ).fetchall()
        return [TaskCounter.from_row(r) for r in rows]

    def is_scan_done(self, host_id: str) -> bool:
        """True if all static tasks reached a terminal state.

        Terminal = done | failed | timeout. Active tasks pending → False.
        """
        row = self._conn.execute(
            """
            SELECT active_count FROM task_state
            WHERE host_id = :host_id AND kind = 'static'
            """,
            {"host_id": host_id},
        ).fetchone()
        if row is None:
            return False
        return row["active_count"] == 0

    def is_dynamic_alive(self, host_id: str) -> bool:
        """True if the dynamic worker has completed at least one cycle."""
        row = self._conn.execute(
            """
            SELECT last_completed_at FROM task_state
            WHERE host_id = :host_id AND kind = 'dynamic'
            """,
            {"host_id": host_id},
        ).fetchone()
        return row is not None and row["last_completed_at"] is not None