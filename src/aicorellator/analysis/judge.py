"""Judge — final verdict from an LLM that sees all voters.

The judge receives the chain and all voter responses. It issues
a single verdict, which becomes the final one for the chain
(per ADR-0005, "voters propose, judge decides").

If the judge times out or fails, the caller must fall back to
aggregation (see evaluator.py).
"""

from __future__ import annotations

import logging

from .client import (
    LLMRequest,
    LLMResponseError,
    complete,
    parse_json_response,
)
from .models import JudgeResponse, VoterConfig, VoterResponse
from .prompts import (
    JUDGE_SYSTEM_PROMPT,
    PROMPT_VERSION,
    build_judge_prompt,
)


log = logging.getLogger(__name__)


VALID_VERDICTS = frozenset({"exploitable", "not_exploitable", "uncertain"})


def evaluate(
    chain_id: str,
    chain_nodes: list[dict],
    chain_edges: list[dict],
    voter_responses: list[VoterResponse],
    judge: VoterConfig,
    temperature: float = 0.0,
    max_tokens: int = 2000,
) -> JudgeResponse | None:
    """Run the judge on one chain. Returns None on timeout / parse failure."""
    user_prompt = build_judge_prompt(
        chain_id,
        chain_nodes,
        chain_edges,
        [_voter_to_dict(r) for r in voter_responses],
    )

    request = LLMRequest(
        model=judge.model_id or judge.name,
        system=JUDGE_SYSTEM_PROMPT,
        user=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=judge.timeout,
        reasoning_effort=judge.reasoning_effort,
    )

    response = complete(judge.base_url, request, api_key=judge.api_key)
    if response is None:
        log.warning("judge %s: no response (timeout or transport error)", judge.name)
        return None

    try:
        parsed = parse_json_response(response.text)
    except LLMResponseError as e:
        log.warning("judge %s: bad JSON — %s", judge.name, e)
        return None

    verdict = str(parsed.get("verdict", "uncertain"))
    if verdict not in VALID_VERDICTS:
        log.warning("judge returned unknown verdict %r; mapping to uncertain", verdict)
        verdict = "uncertain"

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return JudgeResponse(
        chain_id=chain_id,
        model=judge.name,
        prompt_version=PROMPT_VERSION,
        verdict=verdict,
        confidence=confidence,
        reasoning=str(parsed.get("reasoning", "")),
        raw=response.text,
    )


def _voter_to_dict(v: VoterResponse) -> dict:
    """Convert a VoterResponse to a JSON-friendly dict for the judge prompt."""
    return {
        "model": v.model,
        "exploitable": v.exploitable,
        "confidence": v.confidence,
        "reasoning": v.reasoning,
        "evidence": v.evidence,
        "timeout": v.timeout,
    }