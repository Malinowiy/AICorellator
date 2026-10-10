"""Workspace — the working directory layout.

A workspace holds all AICorellator state for a single project:
SQLite DB, reports, cache, logs.

Layout:
    aicorellator_data/
        aicorellator.db
        reports/
            current/
            <timestamp>/
        cache/
            tree-sitter/
        logs/
            aicorellator.log
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DIRNAME = "aicorellator_data"
DB_FILENAME = "aicorellator.db"


@dataclass
class Workspace:
    root: Path

    @property
    def db_path(self) -> Path:
        return self.root / DB_FILENAME

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def current_report_dir(self) -> Path:
        return self.reports_dir / "current"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def tree_sitter_cache_dir(self) -> Path:
        return self.cache_dir / "tree-sitter"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "aicorellator.log"

    def ensure(self) -> None:
        """Create all required subdirectories if missing."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(exist_ok=True)
        self.cache_dir.mkdir(exist_ok=True)
        self.tree_sitter_cache_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)

    def clean(self) -> None:
        """Delete all contents of the workspace, then recreate."""
        if self.root.exists():
            shutil.rmtree(self.root)
        self.ensure()

    def exists(self) -> bool:
        return self.root.exists()


def open_workspace(
    root: str | Path | None = None,
    clean: bool = False,
) -> Workspace:
    """Open or create a workspace.

    Args:
        root: path to workspace root. If None, defaults to
              <cwd>/aicorellator_data/
        clean: if True, delete existing contents first.
    """
    if root is None:
        root = Path.cwd() / DEFAULT_DIRNAME
    ws = Workspace(root=Path(root))
    if clean:
        ws.clean()
    else:
        ws.ensure()
    return ws