"""Unit tests for voters — mocked LLM client, no real HTTP calls."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aicorellator.analysis.client import LLMResponse
from aicorellator.analysis.models import VoterConfig
from aicorellator.analysis.voters import evaluate


def _voter(name: str, timeout: int = 60) -> VoterConfig:
    return VoterConfig(
        name=name,
        base_url="http://localhost:1234/v1",
        role="voter",
        timeout=timeout,
        reasoning_effort="none",
    )


def _fake_response(text: str) -> LLMResponse:
    return LLMResponse(text=text, model="test", finish_reason="stop")


GOOD_JSON = """
{
  "chain_id": "chain:test",
  "model": "test",
  "prompt_version": "v1.1.0",
  "exploitable": true,
  "confidence": 0.85,
  "reasoning": "test reasoning",
  "evidence": ["edge:1", "edge:2"]
}
"""


def test_evaluate_no_voters() -> None:
    result = evaluate("chain:test", [], [], voters=[])
    assert result == []


def test_evaluate_single_voter() -> None:
    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=_fake_response(GOOD_JSON),
    ):
        responses = evaluate(
            "chain:test", [], [],
            voters=[_voter("m1")],
            parallel=False,
        )
    assert len(responses) == 1
    assert responses[0].exploitable is True
    assert responses[0].confidence == 0.85
    assert responses[0].timeout is False
    assert responses[0].evidence == ["edge:1", "edge:2"]


def test_evaluate_sequential_order_preserved() -> None:
    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=_fake_response(GOOD_JSON),
    ):
        voters = [_voter("m1"), _voter("m2"), _voter("m3")]
        responses = evaluate(
            "chain:test", [], [], voters=voters, parallel=False,
        )
    assert [r.model for r in responses] == ["m1", "m2", "m3"]


def test_evaluate_parallel_order_preserved() -> None:
    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=_fake_response(GOOD_JSON),
    ):
        voters = [_voter("m1"), _voter("m2"), _voter("m3")]
        responses = evaluate(
            "chain:test", [], [], voters=voters, parallel=True,
        )
    assert [r.model for r in responses] == ["m1", "m2", "m3"]


def test_voter_timeout_marked() -> None:
    with patch("aicorellator.analysis.voters.complete", return_value=None):
        responses = evaluate(
            "chain:test", [], [], voters=[_voter("m1")], parallel=False,
        )
    assert len(responses) == 1
    assert responses[0].timeout is True
    assert responses[0].confidence == 0.0


def test_voter_bad_json_marked_timeout() -> None:
    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=_fake_response("this is not json"),
    ):
        responses = evaluate(
            "chain:test", [], [], voters=[_voter("m1")], parallel=False,
        )
    assert responses[0].timeout is True
    assert "unparseable" in responses[0].reasoning


def test_voter_missing_confidence_defaults_to_zero() -> None:
    text = '{"chain_id": "chain:test", "exploitable": true, "reasoning": "r"}'
    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=_fake_response(text),
    ):
        responses = evaluate(
            "chain:test", [], [], voters=[_voter("m1")], parallel=False,
        )
    assert responses[0].confidence == 0.0
    assert responses[0].exploitable is True


def test_voter_confidence_clamped() -> None:
    text = '{"chain_id": "chain:test", "exploitable": true, "confidence": 1.5, "reasoning": "r"}'
    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=_fake_response(text),
    ):
        responses = evaluate(
            "chain:test", [], [], voters=[_voter("m1")], parallel=False,
        )
    assert responses[0].confidence == 1.0


def test_voter_wrong_chain_id_noted_in_reasoning() -> None:
    text = '{"chain_id": "chain:other", "exploitable": true, "confidence": 0.5, "reasoning": "r"}'
    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=_fake_response(text),
    ):
        responses = evaluate(
            "chain:test", [], [], voters=[_voter("m1")], parallel=False,
        )
    assert "chain:other" in responses[0].reasoning


def test_voter_markdown_fenced_json() -> None:
    fenced = f"```json\n{GOOD_JSON}\n```"
    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=_fake_response(fenced),
    ):
        responses = evaluate(
            "chain:test", [], [], voters=[_voter("m1")], parallel=False,
        )
    assert responses[0].exploitable is True
    assert responses[0].confidence == 0.85