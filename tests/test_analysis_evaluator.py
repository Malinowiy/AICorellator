"""Unit tests for the evaluator — mocked voters and judge."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aicorellator.analysis.config_loader import EnsembleConfig
from aicorellator.analysis.evaluator import evaluate_chains
from aicorellator.analysis.models import (
    JudgeResponse,
    VoterConfig,
    VoterResponse,
)
from aicorellator.graph.query import CandidateChain


def _candidate(chain_id: str = "chain:test") -> CandidateChain:
    return CandidateChain(
        chain_id=chain_id,
        nodes=["n:1", "n:2"],
        edges=["e:1"],
        length=1,
    )


def _config(voters_count: int = 1, with_judge: bool = False) -> EnsembleConfig:
    voters = [
        VoterConfig(name=f"v{i}", base_url="x", role="voter")
        for i in range(voters_count)
    ]
    judge = None
    if with_judge:
        judge = VoterConfig(name="j", base_url="x", role="judge")
    return EnsembleConfig(
        voters=voters,
        judge=judge,
        mode="full" if with_judge else "fast",
    )


def _voter_response(model: str, exploitable: bool = True, confidence: float = 0.8):
    return VoterResponse(
        chain_id="chain:test", model=model, prompt_version="v1.1.0",
        exploitable=exploitable, confidence=confidence,
        reasoning="r", evidence=[],
    )


def test_evaluate_single_chain_fast(store) -> None:
    candidates = [_candidate()]
    config = _config(voters_count=1, with_judge=False)

    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=None,  # timeout
    ):
        stats = evaluate_chains(
            store, candidates, config, progress=False,
        )

    assert stats["evaluated"] == 1
    assert stats["errors"] == 0
    # All voters timed out → uncertain
    assert stats["uncertain"] == 1


def test_evaluate_chains_persisted(store) -> None:
    """Evaluated chains are written to the store."""
    candidates = [_candidate()]
    config = _config(voters_count=1)

    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=None,
    ):
        evaluate_chains(store, candidates, config, progress=False)

    assert store.chains.count() == 1
    chain = store.chains.get("chain:test")
    assert chain is not None


def test_evaluate_multiple_chains(store) -> None:
    candidates = [_candidate("chain:a"), _candidate("chain:b")]
    config = _config(voters_count=1)

    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=None,
    ):
        stats = evaluate_chains(
            store, candidates, config, progress=False,
        )

    assert stats["evaluated"] == 2
    assert store.chains.count() == 2


def test_evaluate_errors_counted(store) -> None:
    candidates = [_candidate()]
    config = _config(voters_count=1)

    with patch(
        "aicorellator.analysis.voters.evaluate",
        side_effect=RuntimeError("boom"),
    ):
        stats = evaluate_chains(
            store, candidates, config, progress=False,
        )

    assert stats["evaluated"] == 0
    assert stats["errors"] == 1