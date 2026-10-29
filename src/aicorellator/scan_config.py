"""Config loader — the scan-side sections of `config/config.yaml`.

Which layers run, and how, is policy, not a CLI flag (ADR-0016,
§"Scan policy is `scan.yaml`, not flags"; the section predates the
merge into one file). The policy is a file, `config/config.yaml`,
created from a template on first run (with a warning), editable by
the user, versionable in git. It is user input, not state:
`--clean` does not touch it.

One file, sections per subsystem. This module owns the scan side
(see OVERVIEW.md, §"Scan policy: `scan.yaml`"):

    detectors:
      enabled: []                 # [] = all built-in detectors
      disabled: []

    llm_classifier:
      mode: "off"                 # off | auto | required
      config: null                # reserved; the model is below

    runtime:
      mode: "off"                 # off | python
      test_command: null

    unclassified:
      action: "ask"               # ask | skip | llm | error

    logs:
      keep_archives: 5

    store:
      confidence_threshold: 0.7   # strong/weak boundary (ADR-0021)

A detector of your own is not a path in this file: it is a file in
`framework_detectors/` plus a line in `registry.py`, the same
interface as the built-ins (ADR-0016, §"Classification layers"). So
there is nothing to configure for it here.

The other two sections belong to other subsystems and are read
elsewhere: `evaluation` (the ensemble config, from
`analysis/config_loader.py`) and `llm_classifier_model` (the
classifier's own model, BACKLOG D.2). This module ignores both —
they are not scan policy.

Every key is optional and has a default, so a partial (or empty)
file loads. Unknown keys are ignored, like elsewhere in the config.
A misspelled *value* of a documented key is an error, never a silent
fallback: a policy that is not understood is worse than a policy
that refuses to load.

Only `detectors` and `llm_classifier` are honored today; the `runtime`
section is parsed and carried for the layer that will read it (BACKLOG
A.3+). A policy that asks for a layer which does not exist yet is warned
about, not rejected: the file is the record of intent, and silence would
read as "it ran".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml


log = logging.getLogger(__name__)

DEFAULT_CONFIG_FILENAME = "config.yaml"

DEFAULT_LLM_CLASSIFIER_MODE = "off"
DEFAULT_RUNTIME_MODE = "off"
DEFAULT_UNCLASSIFIED_ACTION = "ask"
DEFAULT_LOG_ARCHIVES = 5

# The strong/weak boundary for store-joined chain groups (ADR-0021 §9).
# It mirrors `graph.query.DEFAULT_STORE_CONFIDENCE_THRESHOLD`, which is
# the value `find_chain_groups` uses when a caller passes none: the
# policy default and the mechanism's default are the same number, and a
# test pins them together (the convention `NODE_KINDS` /
# `VALID_NODE_KINDS` uses).
DEFAULT_STORE_CONFIDENCE_THRESHOLD = 0.7

VALID_LLM_CLASSIFIER_MODES = frozenset({"off", "auto", "required"})
VALID_RUNTIME_MODES = frozenset({"off", "python"})
VALID_UNCLASSIFIED_ACTIONS = frozenset({"ask", "skip", "llm", "error"})


class ScanConfigError(Exception):
    """Raised when the scan policy is missing or malformed."""


@dataclass
class DetectorsConfig:
    """Which framework detectors run.

    `enabled: []` means every detector in the registry; `disabled`
    is subtracted from that. A detector of your own is a file in
    `framework_detectors/` plus an entry in `registry.py` — the same
    interface as the built-ins — so there is nothing to configure
    here (ADR-0016, §"Classification layers").
    """

    enabled: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)


@dataclass
class LLMClassifierConfig:
    """Layer 1b (LLM classification of unclassified symbols).

    `mode` is `off` (never called), `auto` (called only for symbols
    the deterministic layers could not classify) or `required`
    (every unclassified symbol must be resolved or the scan fails).
    `off` is the default: the layer is opt-in because it is expensive
    (ADR-0016), and a scan that reaches a model endpoint by default is
    a scan nobody can run offline.

    `config` is a reserved path override, so it defaults to `None`:
    the classifier's model is `llm_classifier_model`, a section of
    its own (BACKLOG D.2).
    """

    mode: str = DEFAULT_LLM_CLASSIFIER_MODE
    config: str | None = None


@dataclass
class RuntimeConfig:
    """Layer 2 (runtime analysis).

    `mode` is `off` or `python`. `test_command` is the command that
    exercises the target while tracing; `None` means the scan does
    not run one.
    """

    mode: str = DEFAULT_RUNTIME_MODE
    test_command: str | None = None


@dataclass
class UnclassifiedConfig:
    """What a scan does with symbols no layer could classify."""

    action: str = DEFAULT_UNCLASSIFIED_ACTION


@dataclass
class LogsConfig:
    """Log retention."""

    keep_archives: int = DEFAULT_LOG_ARCHIVES


@dataclass
class StoreConfig:
    """Store-joined chain groups (ADR-0021).

    `confidence_threshold` is the boundary between a **strong** group
    (in the default enumeration) and a **weak** one (in the graph, out
    of the default; `include_weak=True` asks for it). Below 0.3 there is
    no link at all, and that floor is a mechanism constant rather than a
    setting — it is the base of ADR-0021 §8's combination, not a policy
    choice.

    It is not an `evaluation:` key. The ensemble config says *which
    models judge*, this says *what the graph is allowed to call a
    store*, and it is read by the enumerator's caller
    (`find_chain_groups(store_confidence_threshold=...)`), which is not
    the ensemble.
    """

    confidence_threshold: float = DEFAULT_STORE_CONFIDENCE_THRESHOLD


@dataclass
class ScanConfig:
    """The scan policy — the scan-side sections of `config.yaml`.

    What a scan run is allowed to do: which detectors, which
    additional layers, what to do with unclassified symbols, how
    many log archives to keep.
    """

    detectors: DetectorsConfig = field(default_factory=DetectorsConfig)
    llm_classifier: LLMClassifierConfig = field(
        default_factory=LLMClassifierConfig
    )
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    unclassified: UnclassifiedConfig = field(
        default_factory=UnclassifiedConfig
    )
    logs: LogsConfig = field(default_factory=LogsConfig)
    store: StoreConfig = field(default_factory=StoreConfig)

    def resolve_detectors(self, known: list[str]) -> list[str]:
        """Detector names to run, given the registry's `known` names.

        `enabled: []` means all of `known`; `disabled` is subtracted
        from the result. Names that are not registered are reported
        and dropped: a typo in the policy should not pass unnoticed.
        """
        enabled = self.detectors.enabled or list(known)
        disabled = set(self.detectors.disabled)

        unknown = {
            name
            for name in (*enabled, *self.detectors.disabled)
            if name not in known
        }
        for name in sorted(unknown):
            log.warning(
                "scan policy: unknown framework detector %r", name
            )

        return [
            name
            for name in enabled
            if name in known and name not in disabled
        ]


def default_scan_config() -> ScanConfig:
    """The policy a scan runs with when `config.yaml` says nothing."""
    return ScanConfig()


def default_config_path(workspace_root: Path | None = None) -> Path:
    """Return the default path for the config file."""
    if workspace_root is not None:
        return workspace_root / "config" / DEFAULT_CONFIG_FILENAME
    return Path("config") / DEFAULT_CONFIG_FILENAME


def load_scan_config(path: str | Path) -> ScanConfig:
    """Load the scan-side policy from a YAML config file.

    `evaluation` and `llm_classifier_model` are read by other
    subsystems and are ignored here.

    Args:
        path: path to the YAML config.

    Raises:
        FileNotFoundError if the path does not exist.
        ScanConfigError if the config is malformed or holds a value
        outside the documented set.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ScanConfigError(f"invalid YAML in {path}: {e}") from e

    if data is None:
        return default_scan_config()
    if not isinstance(data, dict):
        raise ScanConfigError(
            f"config root must be a mapping, "
            f"got {type(data).__name__}"
        )

    config = _parse_scan_config(data)
    _warn_unimplemented_layers(config)
    return config


def _warn_unimplemented_layers(config: ScanConfig) -> None:
    """Warn when the policy asks for a layer that does not exist yet.

    The policy is authoritative — it is *read* today and *honored* as
    the layers land (BACKLOG A.3+) — but a setting that changes
    nothing must not look like a setting that took effect.
    """
    if config.runtime.mode != DEFAULT_RUNTIME_MODE:
        log.warning(
            "scan policy: runtime.mode=%r is set, but Layer 2 "
            "(runtime analysis) is not implemented yet — the scan "
            "runs AST-only",
            config.runtime.mode,
        )


def _parse_scan_config(data: dict) -> ScanConfig:
    """Build a ScanConfig from a parsed YAML mapping."""
    detectors = _section(data, "detectors")
    llm = _section(data, "llm_classifier")
    runtime = _section(data, "runtime")
    unclassified = _section(data, "unclassified")
    logs = _section(data, "logs")
    store = _section(data, "store")

    return ScanConfig(
        detectors=DetectorsConfig(
            enabled=_name_list(detectors, "enabled", "detectors"),
            disabled=_name_list(detectors, "disabled", "detectors"),
        ),
        llm_classifier=LLMClassifierConfig(
            mode=_enum(
                llm, "mode", VALID_LLM_CLASSIFIER_MODES,
                "llm_classifier", DEFAULT_LLM_CLASSIFIER_MODE,
            ),
            config=_optional_string(llm, "config", "llm_classifier"),
        ),
        runtime=RuntimeConfig(
            mode=_enum(
                runtime, "mode", VALID_RUNTIME_MODES,
                "runtime", DEFAULT_RUNTIME_MODE,
            ),
            test_command=_optional_string(runtime, "test_command", "runtime"),
        ),
        unclassified=UnclassifiedConfig(
            action=_enum(
                unclassified, "action", VALID_UNCLASSIFIED_ACTIONS,
                "unclassified", DEFAULT_UNCLASSIFIED_ACTION,
            ),
        ),
        logs=LogsConfig(
            keep_archives=_integer(
                logs, "keep_archives", "logs", DEFAULT_LOG_ARCHIVES,
            ),
        ),
        store=StoreConfig(
            confidence_threshold=_ratio(
                store, "confidence_threshold", "store",
                DEFAULT_STORE_CONFIDENCE_THRESHOLD,
            ),
        ),
    )


def _section(data: dict, key: str) -> dict:
    """Return a mapping-valued section, or {} if it is absent."""
    section = data.get(key)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ScanConfigError(f"'{key}' must be a mapping")
    return section


def _name_list(section: dict, key: str, where: str) -> list[str]:
    """Read a list of non-empty string names."""
    value = section.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ScanConfigError(
            f"'{where}.{key}' must be a list of names, "
            f"got {type(value).__name__}"
        )
    names = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ScanConfigError(
                f"'{where}.{key}' must hold non-empty strings, "
                f"got {item!r}"
            )
        names.append(item.strip())
    return names


def _enum(
    section: dict,
    key: str,
    allowed: frozenset[str],
    where: str,
    default: str,
) -> str:
    """Read an enumerated value."""
    value = section.get(key, default)
    if value not in allowed:
        raise ScanConfigError(
            f"'{where}.{key}' must be one of {sorted(allowed)}, "
            f"got {value!r}"
        )
    return value


def _string(section: dict, key: str, where: str, default: str) -> str:
    """Read a non-empty string value."""
    value = section.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ScanConfigError(
            f"'{where}.{key}' must be a non-empty string, got {value!r}"
        )
    return value.strip()


def _optional_string(section: dict, key: str, where: str) -> str | None:
    """Read a string value that may be absent, null or empty."""
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ScanConfigError(
            f"'{where}.{key}' must be a string or null, "
            f"got {type(value).__name__}"
        )
    return value.strip() or None


def _ratio(
    section: dict, key: str, where: str, default: float
) -> float:
    """Read a number between 0.0 and 1.0 (booleans are not numbers here).

    A confidence outside the range is a typo, not a policy: the field is
    a probability, and a threshold of 1.5 would silently select nothing
    while looking like it selected everything strict.
    """
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScanConfigError(
            f"'{where}.{key}' must be a number between 0.0 and 1.0, "
            f"got {value!r}"
        )
    if not 0.0 <= float(value) <= 1.0:
        raise ScanConfigError(
            f"'{where}.{key}' must be between 0.0 and 1.0, got {value!r}"
        )
    return float(value)


def _integer(section: dict, key: str, where: str, default: int) -> int:
    """Read a non-negative integer (booleans are not integers here)."""
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ScanConfigError(
            f"'{where}.{key}' must be a non-negative integer, "
            f"got {value!r}"
        )
    return value


# ============================================================
# Template
# ============================================================

def write_default_config(path: str | Path) -> None:
    """Write the full template `config.yaml` to the given path.

    One file, every section: the scan policy, the evaluation
    ensemble and the classifier's model. This is the one place that
    knows the whole template.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DEFAULT_CONFIG_YAML, encoding="utf-8")


_DEFAULT_CONFIG_YAML = """\
# AICorellator config.
#
# One file, sections per subsystem. This file is user input, not
# state: `--clean` deletes the database, reports, cache and logs and
# leaves it alone. Edit it here, or point at another file with
# `--config`. See ADR-0016 and OVERVIEW.md, "Scan policy".
#
# To reset this file too, remove the workspace directory:
#   rm -rf aicorellator_data/

# --- Scan policy (run_scan.py) ---

# A detector of your own is not listed here: it is a file in
# framework_detectors/ plus a line in registry.py (ADR-0016).
detectors:
  enabled: []                 # [] = all built-in detectors
  disabled: []

# Layer 1b (the LLM classifier) is off by default: it is opt-in because
# it is expensive. "auto" classifies only the symbols no deterministic
# layer classified; "required" sends every symbol and fails the scan if
# any is left unclassified.
llm_classifier:
  mode: "off"                 # off | auto | required
  config: null                # reserved; the model is below

runtime:
  mode: "off"                 # off | python
  test_command: null

unclassified:
  action: "ask"               # ask | skip | llm | error

logs:
  keep_archives: 5

# Store-joined chain groups (ADR-0021). A group whose write and read
# links name one store with a combined confidence below this is "weak":
# it is real evidence, it stays in the graph, and it is left out of the
# default enumeration of chain groups. Below 0.3 there is no link at all
# and that floor is not configurable.
store:
  confidence_threshold: 0.7

# --- Evaluation ensemble (run_eval.py) ---
#
# Roles:
#   fast  — used in mode=fast (one voter, no reasoning)
#   solo  — used in mode=solo (one voter, reasoning, solo prompt)
#   voter — used in mode=full (ensemble voters)
#   judge — used in mode=full (final verdict)
#
# A model may appear multiple times with different roles and
# different parameters (e.g. qwen3.6 as both solo and judge).

evaluation:
  mode: "fast"                # fast | solo | full | custom

  defaults:
    base_url: "http://localhost:1234/v1"
    temperature: 0.0
    max_tokens: 2000
    timeout: 600
    reasoning_effort: "none"

  models:

    # --- FAST ---
    - name: "qwen/qwen3-4b-2507"
      role: "fast"
      max_tokens: 2000
      timeout: 300

    # --- SOLO ---
    - name: "qwen3.6-35b-a3b"
      role: "solo"
      reasoning_effort: "medium"
      temperature: 0.6
      max_tokens: 16000
      timeout: 900

    # --- VOTERS (for mode=full) ---
    - name: "qwen/qwen3-4b-2507"
      role: "voter"
      max_tokens: 2000
      timeout: 300

    - name: "google/gemma-4-12b"
      role: "voter"
      max_tokens: 4000
      timeout: 600

    - name: "nvidia/nemotron-3-nano-4b"
      role: "voter"
      max_tokens: 2000
      timeout: 300

    # --- JUDGE (for mode=full) ---
    - name: "qwen3.6-35b-a3b"
      role: "judge"
      reasoning_effort: "medium"
      temperature: 0.6
      max_tokens: 16000
      timeout: 1800

# --- LLM classifier model (Layer 1b) ---
#
# A different model for a different job, so it is not one of the
# evaluation models (ADR-0016, "Classification layers"). Read only when
# llm_classifier.mode is "auto" or "required": with the mode at its
# default "off", the classifier never runs and this section is never
# read.

llm_classifier_model:
  base_url: "http://localhost:1234/v1"
  model: "qwen/qwen3-4b-2507"
  temperature: 0.0
  max_tokens: 800
  timeout: 120
"""