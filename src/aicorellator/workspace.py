"""Workspace — the working directory layout.

A workspace holds all AICorellator state for a single project:
SQLite DB, reports, cache, logs, config.

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
        config/
            config.yaml

`config/` is user input, not state: `clean()` deletes the
database, reports, cache and logs, and leaves `config/` alone
(ADR-0016, §"Scan policy is `scan.yaml`, not flags"). To reset the
config too, remove the workspace directory.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from .scan_config import DEFAULT_CONFIG_FILENAME, write_default_config


log = logging.getLogger(__name__)

DEFAULT_DIRNAME = "aicorellator_data"
DB_FILENAME = "aicorellator.db"

#: State deleted by `clean()`. `config/` is user input and stays.
STATE_DIRNAMES = ("reports", "cache", "logs")

#: SQLite side files written next to the database.
DB_SIDECAR_SUFFIXES = ("-wal", "-shm")


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
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def config_path(self) -> Path:
        return self.config_dir / DEFAULT_CONFIG_FILENAME

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "aicorellator.log"

    def ensure(self) -> None:
        """Create all required subdirectories if missing.

        Also writes the template `config.yaml` when it does not
        exist, warning once: it is user input, and a template only
        approximates what the user actually wants.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(exist_ok=True)
        self.cache_dir.mkdir(exist_ok=True)
        self.tree_sitter_cache_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)
        self.config_dir.mkdir(exist_ok=True)

        if not self.config_path.exists():
            write_default_config(self.config_path)
            log.warning(
                "wrote a template config to %s — review the detectors "
                "and models against your own setup",
                self.config_path,
            )

    def clean(self) -> None:
        """Delete workspace state, then recreate the layout.

        State is the database (with its WAL side files), reports,
        cache and logs. `config/` is user input: it is not touched,
        so a scan keeps its `config.yaml` across `--clean`
        (ADR-0016). `rm -rf <workspace>/` is the reset-everything
        path.
        """
        self.db_path.unlink(missing_ok=True)
        for suffix in DB_SIDECAR_SUFFIXES:
            side = self.db_path.with_name(self.db_path.name + suffix)
            side.unlink(missing_ok=True)
        for name in STATE_DIRNAMES:
            shutil.rmtree(self.root / name, ignore_errors=True)
        self.ensure()

    def exists(self) -> bool:
        return self.root.exists()


def open_workspace(root: str | Path | None = None) -> Workspace:
    """Open or create a workspace.

    Args:
        root: path to workspace root. If None, defaults to
              <cwd>/aicorellator_data/

    The layout is created if needed (including template config
    files). Deleting state is a separate, explicit step: callers
    that want a clean run do `ws.clean()` themselves, after they
    have read the policy they intend to run with.
    """
    if root is None:
        root = Path.cwd() / DEFAULT_DIRNAME
    ws = Workspace(root=Path(root))
    ws.ensure()
    return ws