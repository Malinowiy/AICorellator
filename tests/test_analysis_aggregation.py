"""Unit tests for confidence aggregation."""

from __future__ import annotations

from aicorellator.analysis.aggregation import aggregate
from aicorellator.analysis.models import VoterConfig, VoterResponse

import pytest

def _voter(name: str, weight: float = 1.0) -> VoterConfig:
    return VoterConfig(
        name=name, base_url="x", role="voter", weight=weight,
    )


def _response(
    model: str,
    exploitable: bool = True,
    confidence: float = 0.8,
    timeout: bool = False,
) -> VoterResponse:
    return VoterResponse(
        chain_id="chain:test",
        model=model,
        prompt_version="v1.1.0",
        exploitable=exploitable,
        confidence=confidence,
        reasoning="r",
        timeout=timeout,
    )


def test_aggregate_all_exploitable() -> None:
    voters = [_voter("a"), _voter("b")]
    responses = [_response("a", True, 0.9), _response("b", True, 0.7)]
    verdict, conf = aggregate(responses, voters)
    assert verdict == "exploitable"
    assert conf == pytest.approx(0.8)


def test_aggregate_all_not_exploitable() -> None:
    voters = [_voter("a"), _voter("b")]
    responses = [_response("a", False, 0.9), _response("b", False, 0.7)]
    verdict, conf = aggregate(responses, voters)
    assert verdict == "not_exploitable"
    assert conf == pytest.approx(0.8)


def test_aggregate_tie_goes_uncertain() -> None:
    voters = [_voter("a"), _voter("b")]
    responses = [_response("a", True, 0.5), _response("b", False, 0.5)]
    verdict, conf = aggregate(responses, voters)
    assert verdict == "uncertain"


def test_aggregate_weighted_mean() -> None:
    voters = [_voter("a", 3.0), _voter("b", 1.0)]
    responses = [_response("a", True, 0.9), _response("b", True, 0.5)]
    verdict, conf = aggregate(responses, voters)
    # (0.9*3 + 0.5*1) / 4 = 3.2 / 4 = 0.8
    assert conf == pytest.approx(0.8)


def test_aggregate_excludes_timeouts() -> None:
    voters = [_voter("a"), _voter("b")]
    responses = [
        _response("a", True, 0.9),
        _response("b", True, 0.0, timeout=True),
    ]
    verdict, conf = aggregate(responses, voters)
    assert verdict == "exploitable"
    assert conf == pytest.approx(0.9)


def test_aggregate_all_timeouts_uncertain() -> None:
    voters = [_voter("a"), _voter("b")]
    responses = [
        _response("a", False, 0.0, timeout=True),
        _response("b", False, 0.0, timeout=True),
    ]
    verdict, conf = aggregate(responses, voters)
    assert verdict == "uncertain"
    assert conf == 0.0


def test_aggregate_single_voter() -> None:
    voters = [_voter("a")]
    responses = [_response("a", True, 0.95)]
    verdict, conf = aggregate(responses, voters)
    assert verdict == "exploitable"
    assert conf == pytest.approx(0.95)