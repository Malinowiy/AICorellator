"""Unit tests for the judge — mocked LLM client."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from aicorellator.analysis.client import LLMResponse
from aicorellator.analysis.config_loader import load_config
from aicorellator.analysis.judge import evaluate
from aicorellator.analysis.models import VoterConfig, VoterResponse
from aicorellator.scan_config import write_default_config

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


def _capture_request(judge, **kwargs):
    """Run the judge and capture the LLMRequest it built."""
    captured: dict = {}

    def _fake_complete(base_url, request, api_key="not-needed"):
        captured["temperature"] = request.temperature
        captured["max_tokens"] = request.max_tokens
        return _response(GOOD_JUDGE)

    with patch("aicorellator.analysis.judge.complete", side_effect=_fake_complete):
        evaluate(
            "chain:test", [], [],
            voter_responses=[_voter_response()],
            judge=judge,
            **kwargs,
        )
    return captured


def test_judge_honors_per_model_overrides() -> None:
    """Per-judge temperature/max_tokens override the config defaults."""
    judge = VoterConfig(
        name="judge-1",
        base_url="http://localhost:1234/v1",
        role="judge",
        timeout=900,
        temperature=0.6,
        max_tokens=16000,
    )
    captured = _capture_request(judge, temperature=0.0, max_tokens=2000)
    assert captured["temperature"] == 0.6
    assert captured["max_tokens"] == 16000


def test_judge_uses_defaults_without_override() -> None:
    captured = _capture_request(_judge(), temperature=0.3, max_tokens=1234)
    assert captured["temperature"] == 0.3
    assert captured["max_tokens"] == 1234


def test_the_shipped_judge_runs_at_the_configs_zero(tmp_path: Path) -> None:
    """B5A — the template's judge reaches the model at 0.0.

    `evaluate` is unchanged by B.5: a judge with no temperature of its
    own takes the config's, and the template now names 0.0 there. The
    config is read through `load_config`, so what is pinned is the file
    a user edits, not a literal restated in the test.
    """
    path = tmp_path / "config" / "config.yaml"
    write_default_config(path)
    config = load_config(path, mode="custom")

    captured = _capture_request(
        config.judge, temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
    assert captured["temperature"] == 0.0
    # A judge that names its own temperature still wins (ADR-0005).
    assert _capture_request(
        VoterConfig(
            name="judge-1", base_url="http://localhost:1234/v1",
            role="judge", timeout=900, temperature=0.6,
        ),
        temperature=0.0,
    )["temperature"] == 0.6