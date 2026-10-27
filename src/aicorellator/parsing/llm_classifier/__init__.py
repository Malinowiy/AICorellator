"""LLM classifier — Layer 1b (ADR-0016, BACKLOG D.2).

The optional, non-deterministic classification layer. It runs after the
deterministic layers and before `normalize()`, and it exists for projects
whose SDK no framework detector covers. Opt-in: `llm_classifier.mode` in
`config.yaml` is `off` by default.

Exports:

  ClassifierConfig      — the model the layer calls
                          (`config.yaml: llm_classifier_model`)
  load_classifier_config— read that section
  Classifier            — one file's unclassified symbols → nodes
  classify_file         — the same, for a caller with no scan to hang a
                          `Classifier` on
  UnclassifiedSymbol    — the classifier's input for one symbol
  SymbolClassification  — its validated answer
  parse_answer          — the validation, on its own
  ClassifierCache       — the SQLite cache, keyed by (content, prompt, model)
  content_hash          — the key's content half
  run_classifier        — the runner `run_scan.py` calls
  ClassifierError       — the layer cannot run, or a required policy was
                          not satisfied
  ClassifierConfigError — the model section is missing or malformed

`prompt.py` holds the prompt itself, with its own `PROMPT_VERSION`; it is
not re-exported here, because nothing outside the package sends it.
"""

from .cache import (
    CACHE_FILENAME,
    CachedClassification,
    ClassifierCache,
    content_hash,
)
from .classifier import (
    Classifier,
    ClassifierError,
    SymbolClassification,
    UnclassifiedSymbol,
    classify_file,
    parse_answer,
)
from .config import (
    ClassifierConfig,
    ClassifierConfigError,
    default_classifier_config,
    load_classifier_config,
)
from .prompt import PROMPT_VERSION
from .runner import run_classifier

__all__ = [
    "CACHE_FILENAME",
    "CachedClassification",
    "Classifier",
    "ClassifierCache",
    "ClassifierConfig",
    "ClassifierConfigError",
    "ClassifierError",
    "PROMPT_VERSION",
    "SymbolClassification",
    "UnclassifiedSymbol",
    "classify_file",
    "content_hash",
    "default_classifier_config",
    "load_classifier_config",
    "parse_answer",
    "run_classifier",
]
