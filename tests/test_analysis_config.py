"""Unit tests for the ensemble config loader (`evaluation:`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicorellator.analysis.config_loader import (
    ConfigError,
    EnsembleConfig,
    load_config,
)
from aicorellator.scan_config import write_default_config


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _models_config(models: str) -> str:
    """A config whose `evaluation.models` is the given YAML block.

    The block must already be indented by four spaces.
    """
    return "evaluation:\n  models:\n" + models


BASE_CONFIG = """
evaluation:
  mode: "custom"

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


def test_load_config_missing_evaluation(tmp_path: Path) -> None:
    """The ensemble lives in `evaluation:` — a bare file is not one."""
    path = _write_config(tmp_path, "detectors:\n  enabled: []\n")
    with pytest.raises(ConfigError, match="evaluation"):
        load_config(path)


def test_load_config_evaluation_not_mapping(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "evaluation: 5\n")
    with pytest.raises(ConfigError, match="evaluation must be a mapping"):
        load_config(path)


def test_load_config_rejects_the_old_top_level_shape(
    tmp_path: Path,
) -> None:
    """No migration: the pre-merge `models.yaml` shape is not read."""
    path = _write_config(tmp_path, """
default_mode: "fast"

models:
  - name: "qwen/qwen3-4b"
    role: "fast"
""")
    with pytest.raises(ConfigError, match="evaluation"):
        load_config(path)


def test_load_config_ignores_the_scan_sections(tmp_path: Path) -> None:
    """`detectors`, `runtime`, ... are not evaluation policy."""
    path = _write_config(tmp_path, """
detectors:
  disabled: ["langchain"]
  enabled: "not even a list"

runtime:
  mode: "python"

logs:
  keep_archives: 0

evaluation:
  mode: "fast"
  models:
    - name: "qwen/qwen3-4b"
      role: "fast"
""")
    cfg = load_config(path)
    assert cfg.mode == "fast"
    assert len(cfg.voters) == 1


def test_load_config_missing_models(tmp_path: Path) -> None:
    path = _write_config(tmp_path, 'evaluation:\n  mode: "fast"\n')
    with pytest.raises(ConfigError, match="'models'"):
        load_config(path)


def test_load_config_unknown_role(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _models_config(
        '    - name: "m"\n'
        '      role: "wizard"\n'
    ))
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


def test_mode_comes_from_evaluation_when_not_specified(
    tmp_path: Path,
) -> None:
    path = _write_config(tmp_path, BASE_CONFIG)
    cfg = load_config(path)
    assert cfg.mode == "custom"  # from evaluation.mode in BASE_CONFIG


def test_mode_argument_overrides_evaluation(tmp_path: Path) -> None:
    """`--mode` is gone; the argument still wins for programmatic use."""
    path = _write_config(tmp_path, BASE_CONFIG)
    cfg = load_config(path, mode="solo")
    assert cfg.mode == "solo"
    assert cfg.voters[0].role == "solo"


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


def test_defaults_are_read_from_the_evaluation_section(
    tmp_path: Path,
) -> None:
    """A top-level `defaults` is not evaluation policy anymore."""
    path = _write_config(tmp_path, """
defaults:
  base_url: "http://ignored:1/v1"
  reasoning_effort: "medium"

evaluation:
  mode: "fast"
  defaults:
    base_url: "http://used:2/v1"
  models:
    - name: "qwen/qwen3-4b"
      role: "fast"
""")
    cfg = load_config(path)
    assert cfg.voters[0].base_url == "http://used:2/v1"
    assert cfg.voters[0].reasoning_effort is None


def test_excluded_model_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _models_config(
        '    - name: "jina-embeddings-v5"\n'
        '      role: "voter"\n'
    ))
    with pytest.raises(ConfigError, match="non-chat"):
        load_config(path)


def test_multiple_judges_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _models_config(
        '    - name: "a"\n'
        '      role: "voter"\n'
        '    - name: "j1"\n'
        '      role: "judge"\n'
        '    - name: "j2"\n'
        '      role: "judge"\n'
    ))
    with pytest.raises(ConfigError, match="at most one judge"):
        load_config(path)


def test_no_voters_in_full_mode_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _models_config(
        '    - name: "j"\n'
        '      role: "judge"\n'
    ))
    with pytest.raises(ConfigError, match="at least one model"):
        load_config(path, mode="full")


def test_the_workspace_template_is_a_valid_ensemble_config(
    tmp_path: Path,
) -> None:
    """One template, two readers: `evaluation:` parses here too."""
    path = tmp_path / "config" / "config.yaml"
    write_default_config(path)

    cfg = load_config(path)
    assert isinstance(cfg, EnsembleConfig)
    assert cfg.mode == "fast"
    assert len(cfg.voters) == 1
    assert cfg.voters[0].role == "fast"
    assert cfg.judge is None
