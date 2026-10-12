"""Unit tests for the ensemble config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicorellator.analysis.config_loader import (
    ConfigError,
    EnsembleConfig,
    load_config,
)


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(content, encoding="utf-8")
    return path


BASE_CONFIG = """
default_mode: "custom"

defaults:
  base_url: "http://localhost:1234/v1"
  temperature: 0.0
  max_tokens: 2000
  timeout: 600
  reasoning_effort: "none"

models:
  - name: "qwen/qwen3-4b"
    role: "fast"
    max_tokens: 2000
    timeout: 300

  - name: "qwen3.6-35b"
    role: "solo"
    reasoning_effort: "medium"
    temperature: 0.6
    max_tokens: 16000
    timeout: 900

  - name: "qwen/qwen3-4b"
    role: "voter"

  - name: "google/gemma-4-12b"
    role: "voter"

  - name: "nvidia/nemotron-3-nano-4b"
    role: "voter"

  - name: "qwen3.6-35b"
    role: "judge"
    reasoning_effort: "medium"
    max_tokens: 16000
    timeout: 1800
"""


def test_load_config_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.yaml")


def test_load_config_invalid_yaml(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "this is : not : valid")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_load_config_root_not_mapping(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "- a\n- b\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(path)


def test_load_config_missing_models(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "default_mode: fast\n")
    with pytest.raises(ConfigError, match="'models'"):
        load_config(path)


def test_load_config_unknown_role(tmp_path: Path) -> None:
    path = _write_config(tmp_path, """
models:
  - name: "m"
    role: "wizard"
""")
    with pytest.raises(ConfigError, match="unknown role"):
        load_config(path)


def test_load_config_unknown_mode(tmp_path: Path) -> None:
    path = _write_config(tmp_path, BASE_CONFIG)
    with pytest.raises(ConfigError, match="unknown mode"):
        load_config(path, mode="wizard")


def test_fast_mode_selects_fast_role(tmp_path: Path) -> None:
    path = _write_config(tmp_path, BASE_CONFIG)
    cfg = load_config(path, mode="fast")
    assert len(cfg.voters) == 1
    assert cfg.voters[0].role == "fast"
    assert cfg.judge is None
    assert cfg.is_parallel_safe is True


def test_solo_mode_selects_solo_role(tmp_path: Path) -> None:
    path = _write_config(tmp_path, BASE_CONFIG)
    cfg = load_config(path, mode="solo")
    assert len(cfg.voters) == 1
    assert cfg.voters[0].role == "solo"
    assert cfg.voters[0].reasoning_effort == "medium"
    assert cfg.judge is None
    assert cfg.is_parallel_safe is True


def test_full_mode_selects_voters_and_judge(tmp_path: Path) -> None:
    path = _write_config(tmp_path, BASE_CONFIG)
    cfg = load_config(path, mode="full")
    assert len(cfg.voters) == 3
    assert all(v.role == "voter" for v in cfg.voters)
    assert cfg.judge is not None
    assert cfg.judge.role == "judge"
    assert cfg.is_parallel_safe is False


def test_custom_mode_same_as_full(tmp_path: Path) -> None:
    path = _write_config(tmp_path, BASE_CONFIG)
    cfg = load_config(path, mode="custom")
    assert len(cfg.voters) == 3
    assert cfg.judge is not None


def test_default_mode_used_when_not_specified(tmp_path: Path) -> None:
    path = _write_config(tmp_path, BASE_CONFIG)
    cfg = load_config(path)
    assert cfg.mode == "custom"  # from default_mode in BASE_CONFIG


def test_per_model_overrides(tmp_path: Path) -> None:
    path = _write_config(tmp_path, BASE_CONFIG)
    cfg = load_config(path, mode="solo")
    solo = cfg.voters[0]
    assert solo.temperature == 0.6
    assert solo.max_tokens == 16000
    assert solo.timeout == 900


def test_defaults_applied_when_no_override(tmp_path: Path) -> None:
    path = _write_config(tmp_path, BASE_CONFIG)
    cfg = load_config(path, mode="fast")
    fast = cfg.voters[0]
    assert fast.temperature is None     # uses config default
    assert fast.max_tokens == 2000      # from model
    assert fast.reasoning_effort == "none"  # from defaults


def test_excluded_model_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, """
models:
  - name: "jina-embeddings-v5"
    role: "voter"
""")
    with pytest.raises(ConfigError, match="non-chat"):
        load_config(path)


def test_multiple_judges_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, """
models:
  - name: "a"
    role: "voter"
  - name: "j1"
    role: "judge"
  - name: "j2"
    role: "judge"
""")
    with pytest.raises(ConfigError, match="at most one judge"):
        load_config(path)


def test_no_voters_in_full_mode_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, """
models:
  - name: "j"
    role: "judge"
""")
    with pytest.raises(ConfigError, match="at least one model"):
        load_config(path, mode="full")