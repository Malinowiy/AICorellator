"""State repository — per-host pipeline flags.

Global flags are DERIVED by query, not stored. See C4_L3_Phase0.md.
"""

from __future__ import annotations

import sqlite3

from ..models import utcnow


# Documented key set — mirrors CHECK constraint in schema.sql.
# Adding a key requires a migration.
KNOWN_KEYS = frozenset({
    "phase1_done",
    "scan_done",
    "report_fresh",
    "last_report_at",
    "dynamic_enabled",
    "cleanup_stale_days",
    "cleanup_findings_days",
    "cleanup_chains_days",
    "cleanup_reports_days",
})

# Boolean flags: stored as 'true' / 'false' strings.
BOOLEAN_KEYS = frozenset({
    "phase1_done",
    "scan_done",
    "report_fresh",
    "dynamic_enabled",
})


class StateRepository:
    """Key-value flags, scoped per host."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --------------------------------------------------------
    # Write
    # --------------------------------------------------------

    def set(self, host_id: str, key: str, value: str | bool | None) -> None:
        """Set a flag. Raises ValueError on unknown key or bad type."""
        if key not in KNOWN_KEYS:
            raise ValueError(f"unknown state key: {key}")

        if key in BOOLEAN_KEYS:
            if not isinstance(value, bool):
                raise ValueError(
                    f"key '{key}' is boolean; got {type(value).__name__}"
                )
            value = "true" if value else "false"
        elif isinstance(value, bool):
            raise ValueError(
                f"key '{key}' is not boolean; got bool"
            )

        self._conn.execute(
            """
            INSERT INTO state (host_id, key, value, updated_at)
            VALUES (:host_id, :key, :value, :updated_at)
            ON CONFLICT (host_id, key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            {
                "host_id": host_id,
                "key": key,
                "value": value,
                "updated_at": utcnow(),
            },
        )

    def unset(self, host_id: str, key: str) -> None:
        """Set a flag to NULL. Used for last_report_at before first report."""
        if key not in KNOWN_KEYS:
            raise ValueError(f"unknown state key: {key}")
        self._conn.execute(
            "UPDATE state SET value = NULL, updated_at = :ts "
            "WHERE host_id = :host_id AND key = :key",
            {"host_id": host_id, "key": key, "ts": utcnow()},
        )

    # --------------------------------------------------------
    # Read
    # --------------------------------------------------------

    def get(self, host_id: str, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM state WHERE host_id = :host_id AND key = :key",
            {"host_id": host_id, "key": key},
        ).fetchone()
        return row["value"] if row else None

    def get_bool(self, host_id: str, key: str) -> bool:
        """Get a boolean flag. Raises ValueError if key is not boolean."""
        if key not in BOOLEAN_KEYS:
            raise ValueError(f"key '{key}' is not boolean")
        value = self.get(host_id, key)
        return value == "true"

    def get_int(self, host_id: str, key: str) -> int:
        """Get an integer value. Raises ValueError if key is not integer."""
        if key in BOOLEAN_KEYS or key == "last_report_at":
            raise ValueError(f"key '{key}' is not integer")
        value = self.get(host_id, key)
        if value is None:
            raise KeyError(f"state key not set: {key}")
        return int(value)

    def all(self, host_id: str) -> dict[str, str | None]:
        """All flags for a host."""
        rows = self._conn.execute(
            "SELECT key, value FROM state WHERE host_id = :host_id",
            {"host_id": host_id},
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    # --------------------------------------------------------
    # Derived global flags
    # --------------------------------------------------------

    def global_flag(self, key: str) -> bool:
        """True if every active host has this boolean key set to 'true'.

        Phase 0: only 'localhost'. Phase 3: aggregated across agents.
        """
        if key not in BOOLEAN_KEYS:
            raise ValueError(f"key '{key}' is not boolean")

        row = self._conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN s.value = 'true' THEN 1 ELSE 0 END) AS set_true
            FROM hosts h
            LEFT JOIN state s
              ON s.host_id = h.id AND s.key = :key
            WHERE h.status = 'active'
            """,
            {"key": key},
        ).fetchone()

        total = row["total"] or 0
        set_true = row["set_true"] or 0
        return total > 0 and total == set_true

    def global_int_min(self, key: str) -> int:
        """Minimum integer value across active hosts. Used for cleanup thresholds."""
        if key in BOOLEAN_KEYS or key == "last_report_at":
            raise ValueError(f"key '{key}' is not integer")
        row = self._conn.execute(
            """
            SELECT MIN(CAST(s.value AS INTEGER)) AS min_val
            FROM hosts h
            JOIN state s ON s.host_id = h.id AND s.key = :key
            WHERE h.status = 'active' AND s.value IS NOT NULL
            """,
            {"key": key},
        ).fetchone()
        if row["min_val"] is None:
            raise KeyError(f"state key not set on any active host: {key}")
        return row["min_val"]