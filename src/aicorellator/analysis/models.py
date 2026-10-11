"""Data models for chain evaluation (Phase 3).

Mirrors the structures defined in ADR-0005 (ensemble) and
ADR-0006 (two-layer evaluation).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VoterConfig:
    """Configuration for one voter or judge model."""

    name: str                    # "qwen3.8-27b"
    base_url: str                # "http://localhost:1234/v1"
    role: str                    #  fast | solo | voter | judge
    weight: float = 1.0
    timeout: int = 600           # seconds
    api_key: str = "not-needed"  # LMStudio/Ollama don't require
    model_id: str = ""           # identifier sent to API; defaults to name
    reasoning_effort: str | None = None # optional reasoning effort hint for the model
    temperature: float | None = None    
    max_tokens: int | None = None        

@dataclass
class VoterResponse:
    """Raw response from one voter on one chain.

    Matches the JSON format from ADR-0005:

        {
          "chain_id": "...",
          "model": "...",
          "prompt_version": "...",
          "exploitable": true,
          "confidence": 0.85,
          "reasoning": "...",
          "evidence": ["edge_id_1", "edge_id_2"]
        }
    """

    chain_id: str
    model: str
    prompt_version: str
    exploitable: bool
    confidence: float
    reasoning: str
    evidence: list[str] = field(default_factory=list)
    # Set to True if the voter timed out or failed to respond.
    timeout: bool = False
    # Raw text, for debugging. Not persisted to DB.
    raw: str = ""


@dataclass
class JudgeResponse:
    """Response from the judge on one chain.

    Matches ADR-0005:

        {
          "chain_id": "...",
          "model": "...",
          "prompt_version": "...",
          "verdict": "exploitable",
          "confidence": 0.9,
          "reasoning": "..."
        }
    """

    chain_id: str
    model: str
    prompt_version: str
    verdict: str                 # exploitable | not_exploitable | uncertain
    confidence: float
    reasoning: str
    raw: str = ""


@dataclass
class EvaluationResult:
    """Aggregated result of evaluating one chain.

    This is what gets written to the `chains` table.
    """

    chain_id: str
    chain_nodes: list[str]
    chain_edges: list[str]
    verdict: str
    confidence: float
    reasoning: str
    voter_outputs: list[dict] = field(default_factory=list)
    prompt_version: str = ""
    validated: bool = False       # Layer 3 result