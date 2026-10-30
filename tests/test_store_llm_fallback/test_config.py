"""The `llm_fallback_model` section (ADR-0023 §2).

One section, its own loader, its own error — D.2's shape, for the same
reason: a model you did not configure is worse than one that refuses to
load, and the caller must be able to tell which section it got wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicorellator.store.llm_fallback import (
    FallbackConfig,
    FallbackConfigError,
    default_fallback_config,
    load_fallback_config,
)


FULL = """\
llm_fallback_model:
  base_url: "http://localhost:9999/v1"
  model: "test/fallback"
  temperature: 0.6
  max_tokens: 100
  timeout: 30
  response_format: "object"
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_match_the_template() -> None:
    config = default_fallback_config()
    assert config == FallbackConfig()
    assert config.temperature == 0.0
    assert config.max_tokens == 400
    assert config.timeout == 120
    assert config.response_format == "schema"


def test_valid_load(tmp_path: Path) -> None:
    config = load_fallback_config(_write(tmp_path, FULL))
    assert config == FallbackConfig(
        base_url="http://localhost:9999/v1",
        model="test/fallback",
        temperature=0.6,
        max_tokens=100,
        timeout=30,
        response_format="object",
    )


def test_missing_keys_take_the_defaults(tmp_path: Path) -> None:
    config = load_fallback_config(_write(
        tmp_path, 'llm_fallback_model:\n  model: "test/model"\n',
    ))
    assert config.base_url == FallbackConfig().base_url
    assert config.temperature == 0.0
    assert config.response_format == "schema"


def test_a_missing_section_is_an_error(tmp_path: Path) -> None:
    """The mode asked for the layer; the model is not optional."""
    with pytest.raises(FallbackConfigError, match="llm_fallback_model"):
        load_fallback_config(_write(tmp_path, "detectors:\n  enabled: []\n"))


def test_an_empty_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FallbackConfigError, match="llm_fallback_model"):
        load_fallback_config(_write(tmp_path, ""))


def test_a_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FallbackConfigError, match="cannot read"):
        load_fallback_config(tmp_path / "missing.yaml")


def test_malformed_yaml_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FallbackConfigError, match="invalid YAML"):
        load_fallback_config(_write(tmp_path, "llm_fallback_model: [\n"))


def test_a_non_mapping_section_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FallbackConfigError, match="must be a mapping"):
        load_fallback_config(_write(tmp_path, "llm_fallback_model: 7\n"))


def test_the_root_must_be_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(FallbackConfigError, match="root must be a mapping"):
        load_fallback_config(_write(tmp_path, "- a\n"))


def test_the_model_is_required(tmp_path: Path) -> None:
    with pytest.raises(FallbackConfigError, match="model"):
        load_fallback_config(_write(
            tmp_path, 'llm_fallback_model:\n  temperature: 0.0\n',
        ))


@pytest.mark.parametrize(
    "model",
    [
        "nomic-embed-text",
        "text-embedding-3-small",
        "bge-reranker-v2",
        "whisper-large",
    ],
)
def test_a_non_chat_model_is_rejected(tmp_path: Path, model: str) -> None:
    """`EXCLUDED_MODEL_SUBSTRINGS` — the ensemble loader's own rule."""
    with pytest.raises(FallbackConfigError, match="non-chat"):
        load_fallback_config(_write(
            tmp_path, f'llm_fallback_model:\n  model: "{model}"\n',
        ))


@pytest.mark.parametrize(
    ("content", "where"),
    [
        ('llm_fallback_model:\n  model: ""\n', "model"),
        ('llm_fallback_model:\n  model: 7\n', "model"),
        (
            'llm_fallback_model:\n  model: "m"\n  base_url: 7\n',
            "base_url",
        ),
        (
            'llm_fallback_model:\n  model: "m"\n  temperature: "hot"\n',
            "temperature",
        ),
        (
            'llm_fallback_model:\n  model: "m"\n  temperature: true\n',
            "temperature",
        ),
        (
            'llm_fallback_model:\n  model: "m"\n  max_tokens: 0\n',
            "max_tokens",
        ),
        (
            'llm_fallback_model:\n  model: "m"\n  max_tokens: 1.5\n',
            "max_tokens",
        ),
        (
            'llm_fallback_model:\n  model: "m"\n  timeout: -1\n',
            "timeout",
        ),
        (
            'llm_fallback_model:\n  model: "m"\n  response_format: "yaml"\n',
            "response_format",
        ),
    ],
)
def test_a_documented_key_with_the_wrong_type_is_an_error(
    tmp_path: Path, content: str, where: str,
) -> None:
    with pytest.raises(FallbackConfigError, match=where):
        load_fallback_config(_write(tmp_path, content))


@pytest.mark.parametrize("value", ["schema", "object", "none"])
def test_documented_response_formats_load(
    tmp_path: Path, value: str,
) -> None:
    config = load_fallback_config(_write(
        tmp_path,
        f'llm_fallback_model:\n  model: "m"\n'
        f'  response_format: "{value}"\n',
    ))
    assert config.response_format == value


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    """The config's rule everywhere else."""
    config = load_fallback_config(_write(
        tmp_path,
        'llm_fallback_model:\n  model: "m"\n  seed: 3\n  n_ctx: 4096\n',
    ))
    assert config.model == "m"


def test_the_model_is_stripped(tmp_path: Path) -> None:
    config = load_fallback_config(_write(
        tmp_path, 'llm_fallback_model:\n  model: "  m  "\n',
    ))
    assert config.model == "m"
