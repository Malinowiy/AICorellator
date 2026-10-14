"""Unit tests for the evaluator — mocked voters and judge."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from aicorellator.analysis.client import LLMResponse
from aicorellator.analysis.config_loader import EnsembleConfig
from aicorellator.analysis.evaluator import (
    _edge_to_dict,
    _node_to_dict,
    evaluate_chains,
)
from aicorellator.analysis.models import (
    JudgeResponse,
    VoterConfig,
    VoterResponse,
)
from aicorellator.graph import Edge, Node
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


def test_node_to_dict_includes_capabilities(store) -> None:
    """Node capabilities reach the prompt payload (ADR-0009)."""
    store.nodes.upsert(Node(
        id="tool:1", kind="tool", name="execute_python_script",
        host_id="localhost", capabilities=["executes_code"],
    ))
    d = _node_to_dict(store, "tool:1")
    assert d["capabilities"] == ["executes_code"]
    assert "confidence" in d


def test_edge_to_dict_includes_capabilities_and_precision(store) -> None:
    """Edge capabilities and precision reach the prompt payload."""
    store.nodes.upsert(Node(id="n:1", kind="tool", name="t", host_id="localhost"))
    store.nodes.upsert(Node(id="n:2", kind="sink", name="s", host_id="localhost"))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2", kind="has_access_to",
        host_id="localhost", capabilities=["control_flows_forward"],
        precision="syntactic",
    ))
    d = _edge_to_dict(store, "e:1")
    assert d["capabilities"] == ["control_flows_forward"]
    assert d["precision"] == "syntactic"


def test_evaluator_flags_unknown_evidence(store) -> None:
    """Evidence edges not present in the chain are recorded as unknown."""
    candidates = [_candidate()]
    config = _config(voters_count=1)
    text = json.dumps({
        "chain_id": "chain:test",
        "exploitable": True,
        "confidence": 0.9,
        "reasoning": "r",
        "evidence": ["e:1", "edge:ghost"],
    })

    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=LLMResponse(text=text, model="test"),
    ):
        evaluate_chains(store, candidates, config, progress=False)

    chain = store.chains.get("chain:test")
    assert chain is not None
    vo = chain.voter_outputs[0]
    assert vo["evidence_valid"] is False
    assert vo["evidence_unknown"] == ["edge:ghost"]


def test_evaluator_accepts_known_evidence(store) -> None:
    """Evidence edges within the chain are marked valid."""
    candidates = [_candidate()]
    config = _config(voters_count=1)
    text = json.dumps({
        "chain_id": "chain:test",
        "exploitable": True,
        "confidence": 0.9,
        "reasoning": "r",
        "evidence": ["e:1"],
    })

    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=LLMResponse(text=text, model="test"),
    ):
        evaluate_chains(store, candidates, config, progress=False)

    chain = store.chains.get("chain:test")
    assert chain is not None
    vo = chain.voter_outputs[0]
    assert vo["evidence_valid"] is True
    assert vo["evidence_unknown"] == []