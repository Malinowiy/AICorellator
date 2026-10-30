"""Store-identity LLM fallback — Layer 1b's sibling for the store gap.

ADR-0023 decides an opt-in LLM fallback for the store identity sites no
deterministic producer could name (ADR-0021 §5's silent gap). It runs
after the resolver's families and the upload attribution pass, in the
store resolver stage, and it fills the gap or leaves it: a `store` with
`identity_source='llm'` and its `writes_to` / `reads_from` edges at
`identity_confidence=0.2`, weak by §9's floor and visible with
`include_weak=True`.

Exports:

  FallbackConfig        — the model the layer calls
                          (`config.yaml: llm_fallback_model`)
  load_fallback_config  — read that section
  FallbackConfigError   — the section is missing or malformed
  FallbackError         — `mode: required` and a site stayed unresolved
  FamilySite            — one site, with the family that reported it
  StoreIdentity         — a validated answer (a hint, or `null`)
  parse_answer          — the validation, on its own
  FallbackCache         — the SQLite cache (D.2's file, its own table)
  CachedIdentity        — one stored answer
  identity_hash         — the key's content half
  run                   — the runner the store resolver stage calls
  PROMPT_VERSION        — this prompt's own version (`store_identity:v1.0.0`)

The mode is `scan_config.llm_fallback.mode` (`off` by default): this
package knows *how* to ask, `scan_config.py` knows *whether* to. The
prompt itself is not re-exported, because nothing outside the package
sends it.
"""

from .cache import (
    CACHE_FILENAME,
    CachedIdentity,
    FallbackCache,
    identity_hash,
)
from .config import (
    FallbackConfig,
    FallbackConfigError,
    default_fallback_config,
    load_fallback_config,
)
from .prompt import PROMPT_VERSION
from .runner import (
    FallbackError,
    FamilySite,
    StoreIdentity,
    parse_answer,
    run,
)

__all__ = [
    "CACHE_FILENAME",
    "PROMPT_VERSION",
    "CachedIdentity",
    "FallbackCache",
    "FallbackConfig",
    "FallbackConfigError",
    "FallbackError",
    "FamilySite",
    "StoreIdentity",
    "default_fallback_config",
    "identity_hash",
    "load_fallback_config",
    "parse_answer",
    "run",
]
