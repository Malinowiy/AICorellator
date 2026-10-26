"""Tests for the workspace layout: `ensure()`, `clean()`, `config/`.

`config/` is user input, not state: `clean()` deletes the database,
reports, cache and logs and leaves `config/` alone (ADR-0016,
§"Scan policy is `scan.yaml`, not flags").
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from aicorellator.analysis.config_loader import load_config
from aicorellator.scan_config import load_scan_config
from aicorellator.workspace import Workspace, open_workspace


def _state_files(ws: Workspace) -> list[Path]:
    """Files that `clean()` is expected to delete, all created."""
    ws.reports_dir.mkdir(parents=True, exist_ok=True)
    ws.current_report_dir.mkdir(parents=True, exist_ok=True)
    ws.tree_sitter_cache_dir.mkdir(parents=True, exist_ok=True)
    ws.logs_dir.mkdir(parents=True, exist_ok=True)

    paths = [
        ws.db_path,
        ws.db_path.with_name(ws.db_path.name + "-wal"),
        ws.db_path.with_name(ws.db_path.name + "-shm"),
        ws.current_report_dir / "report.md",
        ws.tree_sitter_cache_dir / "envelope.bin",
        ws.log_file,
    ]
    for path in paths:
        path.write_text("state\n", encoding="utf-8")
    return paths


def test_open_workspace_creates_the_layout(tmp_path: Path) -> None:
    ws = open_workspace(root=tmp_path / "ws")
    assert ws.exists()
    assert ws.reports_dir.is_dir()
    assert ws.cache_dir.is_dir()
    assert ws.tree_sitter_cache_dir.is_dir()
    assert ws.logs_dir.is_dir()
    assert ws.config_dir.is_dir()


def test_open_workspace_writes_one_template(tmp_path: Path) -> None:
    ws = open_workspace(root=tmp_path / "ws")
    assert ws.config_path.is_file()
    assert ws.config_path == ws.config_dir / "config.yaml"
    # The template is a valid policy *and* a valid ensemble config:
    # one file, sections per subsystem.
    policy = load_scan_config(ws.config_path)
    assert policy.detectors.enabled == []
    assert policy.llm_classifier.mode == "auto"
    assert policy.runtime.mode == "off"
    assert policy.logs.keep_archives == 5

    ensemble = load_config(ws.config_path)
    assert ensemble.mode == "fast"
    assert len(ensemble.voters) == 1


def test_ensure_warns_once_for_the_template(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        ws = open_workspace(root=tmp_path / "ws")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(ws.config_path) in caplog.text


def test_ensure_is_quiet_when_the_template_exists(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = open_workspace(root=tmp_path / "ws")
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        ws.ensure()
    assert caplog.records == []


def test_legacy_config_files_are_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """v0.1 is early alpha: old files are ignored, not migrated."""
    root = tmp_path / "ws"
    config_dir = root / "config"
    config_dir.mkdir(parents=True)

    models = config_dir / "models.yaml"
    models.write_text("default_mode: fast\n", encoding="utf-8")
    scan = config_dir / "scan.yaml"
    scan.write_text(
        "detectors:\n  disabled: [langchain]\n", encoding="utf-8"
    )

    with caplog.at_level(logging.WARNING):
        ws = open_workspace(root=root)

    # The merged file is created; the legacy files are left alone.
    assert ws.config_path.is_file()
    assert models.read_text(encoding="utf-8") == "default_mode: fast\n"
    assert scan.is_file()

    # One warning, about `config.yaml` — nothing about the old files.
    assert len(caplog.records) == 1
    assert "models.yaml" not in caplog.text
    assert "scan.yaml" not in caplog.text

    # And nothing is read out of them: `scan.yaml` disables a
    # detector, the merged file does not.
    assert load_scan_config(ws.config_path).detectors.disabled == []


def test_clean_keeps_config_and_deletes_state(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = open_workspace(root=tmp_path / "ws")

    # The user has edited their config: it is not state.
    ws.config_path.write_text(
        "logs:\n  keep_archives: 0\n", encoding="utf-8"
    )
    config_before = ws.config_path.read_text(encoding="utf-8")

    state = _state_files(ws)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        ws.clean()
    clean_warnings = list(caplog.records)

    for path in state:
        assert not path.exists(), f"{path} survived clean()"

    # config/ survives, contents included.
    assert ws.config_path.is_file()
    assert ws.config_path.read_text(encoding="utf-8") == config_before
    assert load_scan_config(ws.config_path).logs.keep_archives == 0

    # No template was rewritten, so nothing was warned about.
    assert clean_warnings == []

    # The layout is back, ready for the next scan.
    assert ws.reports_dir.is_dir()
    assert ws.cache_dir.is_dir()
    assert ws.logs_dir.is_dir()


def test_clean_recreates_a_deleted_config_dir(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`clean()` is the only step that may write the template."""
    ws = open_workspace(root=tmp_path / "ws")
    ws.config_path.unlink()
    ws.config_dir.rmdir()

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        ws.clean()

    assert ws.config_path.is_file()
    assert len(caplog.records) == 1


def test_clean_is_idempotent(tmp_path: Path) -> None:
    ws = open_workspace(root=tmp_path / "ws")
    _state_files(ws)
    ws.clean()
    ws.clean()
    assert not ws.db_path.exists()
    assert ws.config_path.is_file()


def test_open_workspace_keeps_existing_state(tmp_path: Path) -> None:
    """Opening a workspace is not `--clean`; state stays."""
    root = tmp_path / "ws"
    ws = open_workspace(root=root)
    ws.db_path.write_text("db\n", encoding="utf-8")

    reopened = open_workspace(root=root)
    assert reopened.db_path.read_text(encoding="utf-8") == "db\n"
    assert reopened.config_path == ws.config_path


def test_open_workspace_does_not_take_clean(tmp_path: Path) -> None:
    """`--clean` is an explicit `ws.clean()` call, not a parameter."""
    with pytest.raises(TypeError):
        open_workspace(root=tmp_path / "ws", clean=True)  # type: ignore


def test_workspace_root_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    ws = open_workspace()
    assert ws.root == tmp_path / "aicorellator_data"
    assert ws.root.is_dir()
