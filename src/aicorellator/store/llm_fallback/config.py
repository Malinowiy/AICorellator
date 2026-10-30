"""Fallback configuration — the `llm_fallback_model` section (ADR-0023).

Not scan policy. `scan_config.py` owns `llm_fallback.mode`, which decides
*whether* the fallback runs; this module owns the model it runs with. It
mirrors `parsing.llm_classifier.config`: one section, its own loader, its
own error, because a model you did not configure is worse than one that
refuses to load, and a caller must be able to tell *which* section of the
file it got wrong.

    llm_fallback_model:
      base_url: "http://localhost:1234/v1"
      model: "qwen/qwen3-4b-2507"
      temperature: 0.0
      max_tokens: 400
      timeout: 120
      response_format: "schema"     # schema | object | none

The section is **separate** from `llm_classifier_model` and from
`evaluation.models` (ADR-0023 §2). Its default model is the same one the
classifier uses — one model, two prompts — but the section is separate so
a user can point the fallback at a smaller or fine-tuned model without
touching the classifier's. Read only when the fallback actually runs
(`mode: auto` or `required`): a missing section is not an error for a
scan that never calls the LLM, and is one for a scan that does.

`temperature` defaults to 0.0. A store identity is a stable answer, not a
sampled one (the reason B.5 made the judge deterministic), and the cache
is keyed on the prompt and the model, not on a seed. An explicit
`temperature` in the section is honored as written.

`response_format` is how the answer is constrained on the wire — `schema`
is JSON-schema-constrained decoding, `object` is OpenAI's legacy
`json_object`, `none` is a plain prompt. As in D.2, the value is a
starting point and not a promise: `runner.py` downgrades at runtime if
the server rejects the form it was given.

Unknown keys are ignored, like everywhere else in the config. A
*documented* key with the wrong type is an error, never a silent
fallback. A `model` that names an embedding or reranker is rejected:
`patterns.EXCLUDED_MODEL_SUBSTRINGS` is the check the ensemble loader
already applies, and a non-chat model here cannot answer at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from ...patterns import EXCLUDED_MODEL_SUBSTRINGS


log = logging.getLogger(__name__)


SECTION = "llm_fallback_model"

DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "qwen/qwen3-4b-2507"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 400
DEFAULT_TIMEOUT = 120
DEFAULT_RESPONSE_FORMAT = "schema"

VALID_RESPONSE_FORMATS = frozenset({"schema", "object", "none"})


class FallbackConfigError(Exception):
    """Raised when `llm_fallback_model` is missing or malformed."""


@dataclass
class FallbackConfig:
    """The model the fallback calls, and how it calls it.

    Defaults mirror the template's `llm_fallback_model` block. `timeout`
    is per site — 120s is generous for one small completion and keeps a
    hung endpoint from stalling a scan. `max_tokens` is 400: the answer is
    one identity hint, a confidence and a sentence of reasoning.
    """

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: int = DEFAULT_TIMEOUT
    response_format: str = DEFAULT_RESPONSE_FORMAT


def default_fallback_config() -> FallbackConfig:
    """The model config a scan uses when the section says nothing."""
    return FallbackConfig()


def load_fallback_config(path: str | Path) -> FallbackConfig:
    """Load the fallback's model from the config file.

    Args:
        path: path to the YAML config.

    Raises:
        FallbackConfigError: the file is unreadable, malformed, or has no
            usable `llm_fallback_model` section. A missing section is an
            error here and not when the scan policy loads: this function
            is called only when the fallback runs.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise FallbackConfigError(f"cannot read {path}: {e}") from e

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise FallbackConfigError(f"invalid YAML in {path}: {e}") from e

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise FallbackConfigError(
            f"config root must be a mapping, got {type(data).__name__}"
        )

    section = data.get(SECTION)
    if section is None:
        raise FallbackConfigError(
            f"config has no '{SECTION}' section — the store-identity LLM "
            f"fallback cannot run without a model (set llm_fallback.mode "
            f"to 'off', or add the section)"
        )
    if not isinstance(section, dict):
        raise FallbackConfigError(
            f"'{SECTION}' must be a mapping, got {type(section).__name__}"
        )

    return FallbackConfig(
        base_url=_string(section, "base_url", DEFAULT_BASE_URL),
        model=_model(section),
        temperature=_number(section, "temperature", DEFAULT_TEMPERATURE),
        max_tokens=_integer(section, "max_tokens", DEFAULT_MAX_TOKENS),
        timeout=_integer(section, "timeout", DEFAULT_TIMEOUT),
        response_format=_enum(
            section, "response_format",
            VALID_RESPONSE_FORMATS, DEFAULT_RESPONSE_FORMAT,
        ),
    )


# ============================================================
# Value helpers
# ============================================================

def _string(section: dict, key: str, default: str) -> str:
    """A non-empty string, or `default`."""
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise FallbackConfigError(
            f"'{SECTION}.{key}' must be a non-empty string, got {value!r}"
        )
    return value.strip()


def _model(section: dict) -> str:
    """`model`, required, and not a non-chat model.

    `EXCLUDED_MODEL_SUBSTRINGS` is the ensemble loader's rule
    (`analysis.config_loader`): an embedding or reranker model cannot
    answer a prompt, and a config that names one would fail on the first
    call instead of at load time.
    """
    name = _string(section, "model", "") if "model" in section else ""
    if not name:
        raise FallbackConfigError(
            f"'{SECTION}.model' is required and must be a non-empty string"
        )
    lowered = name.lower()
    matched = [sub for sub in EXCLUDED_MODEL_SUBSTRINGS if sub in lowered]
    if matched:
        raise FallbackConfigError(
            f"'{SECTION}.model' {name!r} looks like a non-chat model "
            f"(matched {matched!r})"
        )
    return name


def _number(section: dict, key: str, default: float) -> float:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FallbackConfigError(
            f"'{SECTION}.{key}' must be a number, got {value!r}"
        )
    return float(value)


def _integer(section: dict, key: str, default: int) -> int:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise FallbackConfigError(
            f"'{SECTION}.{key}' must be an integer, got {value!r}"
        )
    if value <= 0:
        raise FallbackConfigError(
            f"'{SECTION}.{key}' must be positive, got {value!r}"
        )
    return value


def _enum(
    section: dict,
    key: str,
    valid: frozenset[str],
    default: str,
) -> str:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, str) or value not in valid:
        raise FallbackConfigError(
            f"'{SECTION}.{key}' must be one of "
            f"{', '.join(sorted(valid))}, got {value!r}"
        )
    return value


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_RESPONSE_FORMAT",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT",
    "SECTION",
    "VALID_RESPONSE_FORMATS",
    "FallbackConfig",
    "FallbackConfigError",
    "default_fallback_config",
    "load_fallback_config",
]
