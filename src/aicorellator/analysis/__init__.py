"""Chain evaluation ensemble (Phase 3).

Three layers per ADR-0005 and ADR-0006:
  - Layer 1: deterministic enumeration (QueryLayer.find_chains)
  - Layer 2: LLM evaluation (voters + judge)
  - Layer 3: deterministic validation (QueryLayer.is_chain_valid)
"""

from .config_loader import EnsembleConfig, ConfigError, load_config
from .evaluator import evaluate_chains
from .models import (
    EvaluationResult,
    JudgeResponse,
    VoterConfig,
    VoterResponse,
)

__all__ = [
    "EnsembleConfig",
    "ConfigError",
    "load_config",
    "evaluate_chains",
    "EvaluationResult",
    "JudgeResponse",
    "VoterConfig",
    "VoterResponse",
]