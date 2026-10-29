"""Voters — evaluation of one chain by one or more LLMs.

Each voter independently receives the chain and returns a verdict
with confidence and reasoning.

Three execution modes:
  - fast   : 1 voter, voter prompt, no reasoning
  - solo   : 1 voter, solo prompt (combined voter + judge), reasoning
  - full   : N voters, voter prompt, followed by a judge
  - custom : as configured

Two execution strategies:
  - parallel=True  (ThreadPoolExecutor): fast, requires LMStudio
    Auto-Evict disabled and multiple models loaded.
  - parallel=False (sequential): safe with LMStudio default settings.

On timeout: VoterResponse is returned with timeout=True and
exploitable=False, confidence=0.0.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from .client import (
    LLMRequest,
    LLMResponseError,
    complete,
    parse_json_response,
)
from .models import VoterConfig, VoterResponse
from .prompts import (
    PROMPT_VERSION,
    SOLO_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_solo_prompt,
    build_voter_prompt,
)


log = logging.getLogger(__name__)


def evaluate(
    chain_id: str,
    chain_nodes: list[dict],
    chain_edges: list[dict],
    voters: list[VoterConfig],
    temperature: float = 0.0,
    max_tokens: int = 2000,
    parallel: bool = False,
    mode: str = "custom",
    guardrails: list[dict] | None = None,
) -> list[VoterResponse]:
    """Evaluate one chain with all voters.

    Args:
        chain_id: canonical chain ID.
        chain_nodes: serialized nodes for the prompt.
        chain_edges: serialized edges for the prompt.
        voters: list of voter configs.
        temperature: LLM temperature (default, may be overridden per-voter).
        max_tokens: max response tokens (default, may be overridden per-voter).
        parallel: if True, run in parallel; else sequential.
        mode: execution mode ("fast", "solo", "full", "custom").
              Determines which prompt template is used.
        guardrails: guardrail nodes near the chain, from the evaluator
              (B.4b, ADR-0019). Passed straight through to the prompt
              builder — the voter layer does not read it, and an empty
              list is the normal case.

    Returns:
        list of VoterResponse, one per voter, in input order.
    """
    if not voters:
        return []

    # Choose prompt template based on mode.
    if mode == "solo":
        system_prompt = SOLO_SYSTEM_PROMPT
        user_prompt = build_solo_prompt(
            chain_id, chain_nodes, chain_edges, guardrails=guardrails,
        )
    else:
        system_prompt = SYSTEM_PROMPT
        user_prompt = build_voter_prompt(
            chain_id, chain_nodes, chain_edges, guardrails=guardrails,
        )

    if parallel:
        return _evaluate_parallel(
            voters, chain_id, system_prompt, user_prompt,
            temperature, max_tokens,
        )
    return _evaluate_sequential(
        voters, chain_id, system_prompt, user_prompt,
        temperature, max_tokens,
    )


def _evaluate_sequential(
    voters: list[VoterConfig],
    chain_id: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> list[VoterResponse]:
    """Run voters one after another. Safest mode for LMStudio."""
    results: list[VoterResponse] = []
    for voter in voters:
        log.info("voter %s: starting", voter.name)
        response = _evaluate_one(
            voter, chain_id, system_prompt, user_prompt,
            temperature, max_tokens,
        )
        results.append(response)
        if response.timeout:
            log.warning("voter %s: failed or timed out", voter.name)
        else:
            log.info(
                "voter %s: done (exploitable=%s, confidence=%.2f)",
                voter.name, response.exploitable, response.confidence,
            )
    return results


def _evaluate_parallel(
    voters: list[VoterConfig],
    chain_id: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> list[VoterResponse]:
    """Run voters in parallel. Requires LMStudio Parallel Requests."""
    results: list[VoterResponse | None] = [None] * len(voters)

    with ThreadPoolExecutor(max_workers=len(voters)) as pool:
        future_to_index = {
            pool.submit(
                _evaluate_one,
                voter, chain_id, system_prompt, user_prompt,
                temperature, max_tokens,
            ): i
            for i, voter in enumerate(voters)
        }

        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            voter = voters[idx]
            try:
                results[idx] = future.result()
            except Exception as e:
                log.exception("voter %s raised unexpectedly", voter.name)
                results[idx] = VoterResponse(
                    chain_id=chain_id,
                    model=voter.name,
                    prompt_version=PROMPT_VERSION,
                    exploitable=False,
                    confidence=0.0,
                    reasoning=f"voter raised exception: {e}",
                    evidence=[],
                    timeout=True,
                )

    return [r for r in results if r is not None]


def _evaluate_one(
    voter: VoterConfig,
    chain_id: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> VoterResponse:
    """Run one voter on one chain. Never raises."""
    # Per-voter overrides.
    effective_temperature = (
        voter.temperature if voter.temperature is not None else temperature
    )
    effective_max_tokens = (
        voter.max_tokens if voter.max_tokens is not None else max_tokens
    )

    request = LLMRequest(
        model=voter.model_id or voter.name,
        system=system_prompt,
        user=user_prompt,
        temperature=effective_temperature,
        max_tokens=effective_max_tokens,
        timeout=voter.timeout,
        reasoning_effort=voter.reasoning_effort,
    )

    response = complete(voter.base_url, request, api_key=voter.api_key)

    if response is None:
        log.warning("voter %s: no response (timeout or transport error)", voter.name)
        return VoterResponse(
            chain_id=chain_id,
            model=voter.name,
            prompt_version=PROMPT_VERSION,
            exploitable=False,
            confidence=0.0,
            reasoning="voter returned no response (timeout or transport error)",
            evidence=[],
            timeout=True,
        )

    try:
        parsed = parse_json_response(response.text)
    except LLMResponseError as e:
        log.warning("voter %s: bad JSON — %s", voter.name, e)
        return VoterResponse(
            chain_id=chain_id,
            model=voter.name,
            prompt_version=PROMPT_VERSION,
            exploitable=False,
            confidence=0.0,
            reasoning=f"voter returned unparseable output: {response.text[:200]!r}",
            evidence=[],
            timeout=True,
            raw=response.text,
        )

    return _build_voter_response(voter, chain_id, parsed, response.text)


def _build_voter_response(
    voter: VoterConfig,
    chain_id: str,
    parsed: dict,
    raw: str,
) -> VoterResponse:
    """Validate and normalize a parsed voter JSON response.

    The ``exploitable`` field must be a real JSON boolean. ``bool()``
    coercion is deliberately avoided: ``bool("false")`` is ``True`` in
    Python, so a string such as ``"false"`` would otherwise be recorded
    as a positive verdict. A non-boolean value is treated as an invalid
    response (excluded from aggregation), not as a ``False`` vote.
    """
    exploitable_raw = parsed.get("exploitable")
    if not isinstance(exploitable_raw, bool):
        log.warning(
            "voter %s: invalid 'exploitable' value %r (expected boolean)",
            voter.name, exploitable_raw,
        )
        return VoterResponse(
            chain_id=chain_id,
            model=voter.name,
            prompt_version=PROMPT_VERSION,
            exploitable=False,
            confidence=0.0,
            reasoning=(
                "voter returned an invalid 'exploitable' field "
                f"(expected true/false, got {exploitable_raw!r})"
            ),
            evidence=[],
            timeout=True,
            raw=raw,
        )

    exploitable = exploitable_raw
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reasoning = str(parsed.get("reasoning", ""))

    evidence_raw = parsed.get("evidence", [])
    if isinstance(evidence_raw, list):
        evidence = [str(e) for e in evidence_raw if isinstance(e, (str, int))]
    else:
        evidence = []

    if parsed.get("chain_id") != chain_id:
        reasoning += (
            f"\n[note: model reported chain_id={parsed.get('chain_id')!r}, "
            f"expected {chain_id!r}]"
        )

    return VoterResponse(
        chain_id=chain_id,
        model=voter.name,
        prompt_version=PROMPT_VERSION,
        exploitable=exploitable,
        confidence=confidence,
        reasoning=reasoning,
        evidence=evidence,
        timeout=False,
        raw=raw,
    )