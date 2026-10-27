"""Classifier configuration — the `llm_classifier_model` section (D.2).

Not scan policy. `scan_config.py` owns `llm_classifier.mode`, which decides
*whether* Layer 1b runs; this module owns the model it runs with, because
the classifier is a subsystem with its own model, its own timeout and its
own prompt (ADR-0016, §"Classification layers": a different model for a
different job). `analysis/config_loader.py` reads the `evaluation` section
for exactly the same reason.

    llm_classifier_model:
      base_url: "http://localhost:1234/v1"
      model: "qwen/qwen3-4b-2507"
      temperature: 0.0
      max_tokens: 800
      timeout: 120
      response_format: "schema"     # schema | object | none

Read only when the classifier actually runs (`mode: auto` or `required`).
A missing section is not an error for a scan that never calls the LLM, and
is one for a scan that does: asking for a layer you have not configured is
worse than not asking for it.

`response_format` is how the answer is constrained on the wire — `schema`
is JSON-schema-constrained decoding (LMStudio/llama.cpp/vLLM), `object` is
OpenAI's legacy `json_object`, `none` is a plain prompt. The value here is
a starting point, not a promise: `classifier.py` downgrades at runtime if
the server rejects the form it was given.

Unknown keys are ignored, like everywhere else in the config. A
*documented* key with the wrong type is an error, never a silent fallback:
a model you did not configure is worse than one that refuses to load. The
error type is this module's own, not `analysis.config_loader.ConfigError`
(whose docstring scopes it to the ensemble) and not
`scan_config.ScanConfigError` (whose docstring scopes it to the scan
policy) — one section, one error, so a caller can tell which section of
the file it got wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml


log = logging.getLogger(__name__)


SECTION = "llm_classifier_model"

DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "qwen/qwen3-4b-2507"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 800
DEFAULT_TIMEOUT = 120
DEFAULT_RESPONSE_FORMAT = "schema"

VALID_RESPONSE_FORMATS = frozenset({"schema", "object", "none"})


class ClassifierConfigError(Exception):
    """Raised when `llm_classifier_model` is missing or malformed."""


@dataclass
class ClassifierConfig:
    """The model the classifier calls, and how it calls it.

    `temperature` defaults to 0.0: classification must be reproducible, and
    the cache is keyed on the prompt and the model, not on a seed. `timeout`
    is per symbol — 120s is generous for one small completion, and it keeps
    a hung endpoint from stalling a scan for the evaluation timeout (600s+).
    `max_tokens` is 800: the answer is a kind, a capability list and one
    sentence of reasoning.
    """

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: int = DEFAULT_TIMEOUT
    response_format: str = DEFAULT_RESPONSE_FORMAT


def default_classifier_config() -> ClassifierConfig:
    """The model config a scan uses when the section says nothing."""
    return ClassifierConfig()


def load_classifier_config(path: str | Path) -> ClassifierConfig:
    """Load the classifier's model from the config file.

    Args:
        path: path to the YAML config — the same file as the scan policy
              (one file, sections per subsystem).

    Raises:
        FileNotFoundError if the path does not exist.
        ClassifierConfigError if the file is malformed, the section is
        absent or not a mapping, or a documented key holds a value of the
        wrong type.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ClassifierConfigError(f"invalid YAML in {path}: {e}") from e

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ClassifierConfigError(
            f"config root must be a mapping, got {type(data).__name__}"
        )

    section = data.get(SECTION)
    if section is None:
        raise ClassifierConfigError(
            f"config has no '{SECTION}' section — the LLM classifier "
            f"cannot run without a model (set llm_classifier.mode to "
            f"'off', or add the section)"
        )
    if not isinstance(section, dict):
        raise ClassifierConfigError(
            f"'{SECTION}' must be a mapping, got {type(section).__name__}"
        )

    return ClassifierConfig(
        base_url=_string(section, "base_url", DEFAULT_BASE_URL),
        model=_string(section, "model", None, required=True),
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

def _string(
    section: dict,
    key: str,
    default: str | None,
    required: bool = False,
) -> str:
    """A non-empty string, or `default`.

    `required=True` makes an absent key an error and drops the default —
    used for `model`, where absent and "" are the same mistake.
    """
    value = section.get(key)
    if value is None:
        if required or default is None:
            raise ClassifierConfigError(
                f"'{SECTION}.{key}' is required and must be a non-empty "
                f"string"
            )
        return default
    if not isinstance(value, str) or not value.strip():
        raise ClassifierConfigError(
            f"'{SECTION}.{key}' must be a non-empty string, got {value!r}"
        )
    return value.strip()


def _number(section: dict, key: str, default: float) -> float:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClassifierConfigError(
            f"'{SECTION}.{key}' must be a number, got {value!r}"
        )
    return float(value)


def _integer(section: dict, key: str, default: int) -> int:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ClassifierConfigError(
            f"'{SECTION}.{key}' must be an integer, got {value!r}"
        )
    if value <= 0:
        raise ClassifierConfigError(
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
        raise ClassifierConfigError(
            f"'{SECTION}.{key}' must be one of "
            f"{', '.join(sorted(valid))}, got {value!r}"
        )
    return value
