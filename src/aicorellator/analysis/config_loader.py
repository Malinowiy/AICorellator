"""Config loader for the chain evaluation ensemble.

The set of voters and judges is explicit, not auto-discovered.
Auto-discovery leads to surprises: new models appear in LMStudio,
voters change between runs, results become non-reproducible.

This reads the `evaluation` section of `config/config.yaml`: one
file per workspace, sections per subsystem (ADR-0016). The scan
policy lives in the same file and is read by `scan_config.py`; the
sections are independent, so neither module sees the other's keys.

Config schema (YAML, inside `evaluation:`):

    evaluation:
      mode: "fast"                # fast | solo | full | custom

      defaults:
        base_url: "http://localhost:1234/v1"
        temperature: 0.0
        max_tokens: 2000
        timeout: 600
        reasoning_effort: "none"

      models:
        - name: "qwen/qwen3-4b-2507"
          role: "fast"            # fast | solo | voter | judge
          max_tokens: 2000
          timeout: 300

        - name: "qwen3.6-35b-a3b"
          role: "solo"
          reasoning_effort: "medium"
          temperature: 0.6
          max_tokens: 16000
          timeout: 900

        - name: "qwen/qwen3-4b-2507"
          role: "voter"

        - name: "google/gemma-4-12b"
          role: "voter"

        - name: "nvidia/nemotron-3-nano-4b"
          role: "voter"

        - name: "qwen3.6-35b-a3b"
          role: "judge"
          reasoning_effort: "medium"
          temperature: 0.6
          max_tokens: 16000
          timeout: 1800

Roles:
  fast  — used when mode=fast. One voter, no judge.
  solo  — used when mode=solo. One voter, solo prompt, reasoning.
  voter — used when mode=full. Voters in the ensemble.
  judge — used when mode=full. Final verdict.

A model may appear multiple times with different roles and
different parameters (e.g. qwen3.6 as both solo and judge).

Mode determination:
  mode=fast   → role="fast"  only
  mode=solo   → role="solo"  only
  mode=full   → all role="voter" + one role="judge"
  mode=custom → same as full (no preset filtering)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..patterns import EXCLUDED_MODEL_SUBSTRINGS
from .models import VoterConfig


log = logging.getLogger(__name__)


DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_TIMEOUT = 600
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 2000
DEFAULT_MODE = "custom"

VALID_MODES = frozenset({"fast", "solo", "full", "custom"})
VALID_ROLES = frozenset({"fast", "solo", "voter", "judge"})


class ConfigError(Exception):
    """Raised when the ensemble config is missing or malformed."""


@dataclass
class EnsembleConfig:
    """Voter and judge configuration."""

    voters: list[VoterConfig] = field(default_factory=list)
    judge: VoterConfig | None = None
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    mode: str = DEFAULT_MODE

    @property
    def is_parallel_safe(self) -> bool:
        """True if parallel voter execution is safe for this config.

        Only fast mode and solo mode (single voter, no judge) are
        safe with LMStudio's default Auto-Evict behavior, which
        would unload a model mid-request when another model is
        loaded.
        """
        return len(self.voters) == 1 and self.judge is None


def load_config(
    path: str | Path,
    mode: str | None = None,
) -> EnsembleConfig:
    """Load an ensemble config from a YAML file.

    Args:
        path: path to the YAML config.
        mode: overrides `evaluation.mode` from the config file.
              Valid values: "fast", "solo", "full", "custom".

    Raises:
        FileNotFoundError if the path does not exist.
        ConfigError if the config is malformed or references an
        excluded model.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e

    if not isinstance(data, dict):
        raise ConfigError(
            f"config root must be a mapping, got {type(data).__name__}"
        )

    # --- The ensemble's own section ---
    evaluation = data.get("evaluation")
    if evaluation is None:
        raise ConfigError("config must define an 'evaluation' section")
    if not isinstance(evaluation, dict):
        raise ConfigError("evaluation must be a mapping")

    # --- Defaults ---
    defaults = evaluation.get("defaults", {}) or {}
    if not isinstance(defaults, dict):
        raise ConfigError("'defaults' must be a mapping")

    temperature = float(defaults.get("temperature", DEFAULT_TEMPERATURE))
    max_tokens = int(defaults.get("max_tokens", DEFAULT_MAX_TOKENS))
    default_timeout = int(defaults.get("timeout", DEFAULT_TIMEOUT))
    default_base_url = str(defaults.get("base_url", DEFAULT_BASE_URL))
    default_reasoning = defaults.get("reasoning_effort")  # str | None

    # --- Models ---
    models = evaluation.get("models")
    if not isinstance(models, list) or not models:
        raise ConfigError("config must define a non-empty 'models' list")

    # Parse all entries into VoterConfig objects.
    parsed: list[VoterConfig] = []
    for i, entry in enumerate(models):
        cfg = _parse_model_entry(
            entry, i, default_base_url, default_timeout,
            default_reasoning, defaults,
        )
        parsed.append(cfg)

    # --- Determine effective mode ---
    if mode is None:
        mode = str(evaluation.get("mode", DEFAULT_MODE))
    if mode not in VALID_MODES:
        raise ConfigError(
            f"unknown mode {mode!r} (expected one of {sorted(VALID_MODES)})"
        )

    # --- Filter by role, according to mode ---
    if mode == "fast":
        voters = [c for c in parsed if c.role == "fast"]
        if not voters:
            raise ConfigError(
                "mode=fast requires at least one model with role='fast'"
            )
        judge = None

    elif mode == "solo":
        voters = [c for c in parsed if c.role == "solo"]
        if not voters:
            raise ConfigError(
                "mode=solo requires at least one model with role='solo'"
            )
        judge = None

    elif mode in ("full", "custom"):
        voters = [c for c in parsed if c.role == "voter"]
        judges = [c for c in parsed if c.role == "judge"]
        if not voters:
            raise ConfigError(
                f"mode={mode} requires at least one model with role='voter'"
            )
        if len(judges) > 1:
            raise ConfigError(
                f"mode={mode} allows at most one judge, "
                f"got {len(judges)}"
            )
        judge = judges[0] if judges else None
        if mode == "full" and judge is None:
            log.warning(
                "mode=full has %d voters but no judge; "
                "final verdict will be aggregated from voters",
                len(voters),
            )

    else:
        # Should not be reachable given VALID_MODES check above.
        raise ConfigError(f"unhandled mode: {mode!r}")

    return EnsembleConfig(
        voters=voters,
        judge=judge,
        temperature=temperature,
        max_tokens=max_tokens,
        mode=mode,
    )


def _parse_model_entry(
    entry: object,
    index: int,
    default_base_url: str,
    default_timeout: int,
    default_reasoning: str | None,
    defaults: dict,
) -> VoterConfig:
    """Parse one model entry from the config into a VoterConfig.

    Raises ConfigError on malformed entries.
    """
    if not isinstance(entry, dict):
        raise ConfigError(f"models[{index}] must be a mapping")

    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"models[{index}] must have a non-empty 'name'")

    # Reject non-chat models (embeddings, rerankers, etc.).
    lowered = name.lower()
    if any(sub in lowered for sub in EXCLUDED_MODEL_SUBSTRINGS):
        raise ConfigError(
            f"models[{index}] {name!r} looks like a non-chat model "
            f"(matched one of {EXCLUDED_MODEL_SUBSTRINGS!r})"
        )

    role = entry.get("role", "voter")
    if role not in VALID_ROLES:
        raise ConfigError(
            f"models[{index}] has unknown role {role!r} "
            f"(expected one of {sorted(VALID_ROLES)})"
        )

    # Per-model overrides fall back to defaults.
    base_url = str(entry.get("base_url", default_base_url))
    timeout = int(entry.get("timeout", default_timeout))
    weight = float(entry.get("weight", 1.0))
    api_key = str(entry.get("api_key", "not-needed"))
    model_id = str(entry.get("model_id", name))

    # reasoning_effort: per-model override > defaults > None
    if "reasoning_effort" in entry:
        reasoning_effort = entry.get("reasoning_effort")
    else:
        reasoning_effort = default_reasoning

    # temperature: per-model override > None (use config default later)
    temperature_override = entry.get("temperature")
    if temperature_override is not None:
        temperature_override = float(temperature_override)

    # max_tokens: per-model override > None (use config default later)
    max_tokens_override = entry.get("max_tokens")
    if max_tokens_override is not None:
        max_tokens_override = int(max_tokens_override)

    return VoterConfig(
        name=name,
        base_url=base_url,
        role=role,
        weight=weight,
        timeout=timeout,
        api_key=api_key,
        model_id=model_id,
        reasoning_effort=reasoning_effort,
        temperature=temperature_override,
        max_tokens=max_tokens_override,
    )
