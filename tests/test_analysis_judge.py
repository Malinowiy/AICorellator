"""Unit tests for the judge — mocked LLM client."""

from __future__ import annotations

from unittest.mock import patch

from aicorellator.analysis.client import LLMResponse
from aicorellator.analysis.judge import evaluate
from aicorellator.analysis.models import VoterConfig, VoterResponse

import pytest

def _judge() -> VoterConfig:
    return VoterConfig(
        name="judge-1",
        base_url="http://localhost:1234/v1",
        role="judge",
        timeout=900,
    )


def _response(text: str) -> LLMResponse:
    return LLMResponse(text=text, model="test", finish_reason="stop")


def _voter_response(model: str = "v1") -> VoterResponse:
    return VoterResponse(
        chain_id="chain:test", model=model, prompt_version="v1.1.0",
        exploitable=True, confidence=0.8, reasoning="r", evidence=[],
    )


GOOD_JUDGE = """
{
  "chain_id": "chain:test",
  "model": "judge-1",
  "prompt_version": "v1.1.0",
  "verdict": "exploitable",
  "confidence": 0.9,
  "reasoning": "final reasoning"
}
"""


def test_judge_no_response_returns_none() -> None:
    with patch("aicorellator.analysis.judge.complete", return_value=None):
        result = evaluate(
            "chain:test", [], [], voter_responses=[],
            judge=_judge(),
        )
    assert result is None


def test_judge_bad_json_returns_none() -> None:
    with patch(
        "aicorellator.analysis.judge.complete",
        return_value=_response("not json"),
    ):
        result = evaluate(
            "chain:test", [], [], voter_responses=[],
            judge=_judge(),
        )
    assert result is None


def test_judge_parses_verdict() -> None:
    with patch(
        "aicorellator.analysis.judge.complete",
        return_value=_response(GOOD_JUDGE),
    ):
        result = evaluate(
            "chain:test", [], [],
            voter_responses=[_voter_response()],
            judge=_judge(),
        )
    assert result is not None
    assert result.verdict == "exploitable"
    assert result.confidence == 0.9
    assert result.reasoning == "final reasoning"


def test_judge_unknown_verdict_mapped_to_uncertain() -> None:
    text = GOOD_JUDGE.replace("exploitable", "wizard")
    with patch(
        "aicorellator.analysis.judge.complete",
        return_value=_response(text),
    ):
        result = evaluate(
            "chain:test", [], [],
            voter_responses=[_voter_response()],
            judge=_judge(),
        )
    assert result is not None
    assert result.verdict == "uncertain"


def test_judge_confidence_clamped() -> None:
    text = GOOD_JUDGE.replace("0.9", "2.0")
    with patch(
        "aicorellator.analysis.judge.complete",
        return_value=_response(text),
    ):
        result = evaluate(
            "chain:test", [], [],
            voter_responses=[_voter_response()],
            judge=_judge(),
        )
    assert result is not None
    assert result.confidence == 1.0