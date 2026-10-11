"""Confidence aggregation — weighted mean across voters (ADR-0005).

Rules:
  - Voters with timeout=True are excluded from the mean.
  - If all voters timed out, result is "uncertain" with 0.0.
  - Weighted mean: sum(conf_i * weight_i) / sum(weight_i).
  - Majority vote for exploitable: sum of weights where exploitable
    is True vs False. Ties go to "uncertain".
"""

from __future__ import annotations

import logging

from .models import VoterConfig, VoterResponse


log = logging.getLogger(__name__)


def aggregate(
    responses: list[VoterResponse],
    voters: list[VoterConfig],
) -> tuple[str, float]:
    """Aggregate voter responses into (verdict, confidence).

    Args:
        responses: voter responses, same order as voters list
        voters: configs for weights

    Returns:
        (verdict, confidence) where verdict is one of
        "exploitable" | "not_exploitable" | "uncertain".
    """
    # Build a weight lookup by voter name.
    weights: dict[str, float] = {v.name: v.weight for v in voters}

    # Filter out timeouts.
    valid = [r for r in responses if not r.timeout]
    if not valid:
        log.warning("all voters timed out for chain %s", responses[0].chain_id if responses else "?")
        return "uncertain", 0.0

    # Weighted confidence mean.
    total_weight = 0.0
    weighted_confidence = 0.0
    for r in valid:
        w = weights.get(r.model, 1.0)
        weighted_confidence += r.confidence * w
        total_weight += w

    confidence = weighted_confidence / total_weight if total_weight > 0 else 0.0

    # Weighted vote on exploitable.
    yes_weight = 0.0
    no_weight = 0.0
    for r in valid:
        w = weights.get(r.model, 1.0)
        if r.exploitable:
            yes_weight += w
        else:
            no_weight += w

    if yes_weight > no_weight:
        verdict = "exploitable"
    elif no_weight > yes_weight:
        verdict = "not_exploitable"
    else:
        verdict = "uncertain"

    return verdict, confidence