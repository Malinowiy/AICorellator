"""Evaluator — orchestrate chain evaluation and persist results.

Pipeline per chain (ADR-0005 + ADR-0006):

    1. Enumerate candidate (already done by QueryLayer.find_chains)
    2. Voters evaluate (parallel or sequential)
    3. Judge issues final verdict (only in full/custom mode)
    4. Layer 3 deterministic validation (validate_edges)
    5. Persist to chains table
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from ..graph.models import Chain
from ..graph.store import GraphStore
from ..graph.query import CandidateChain
from . import aggregation, judge as judge_mod, voters as voters_mod
from .config_loader import EnsembleConfig
from .prompts import PROMPT_VERSION


log = logging.getLogger(__name__)


def evaluate_chains(
    store: GraphStore,
    candidates: list[CandidateChain],
    config: EnsembleConfig,
    host_id: str = "localhost",
    progress: bool = True,
    parallel: bool | None = None,
) -> dict[str, int]:
    """Evaluate a list of candidate chains and persist results.

    Args:
        store: GraphStore with nodes/edges already loaded.
        candidates: from store.query.find_chains().
        config: EnsembleConfig (voters + optional judge + mode).
        host_id: current host.
        progress: print progress to stdout.
        parallel: if None, auto-derive from config.is_parallel_safe.

    Returns:
        Stats dict.
    """
    if parallel is None:
        parallel = config.is_parallel_safe

    log.info(
        "evaluating %d chains: mode=%s voters=%d judge=%s parallel=%s",
        len(candidates), config.mode, len(config.voters),
        "yes" if config.judge else "no", parallel,
    )

    stats = {
        "evaluated": 0,
        "validated": 0,
        "exploitable": 0,
        "not_exploitable": 0,
        "uncertain": 0,
        "errors": 0,
    }

    total = len(candidates)
    for i, candidate in enumerate(candidates, start=1):
        if progress:
            print(f"  [{i}/{total}] {candidate.chain_id}", flush=True)

        try:
            chain = _evaluate_one(store, candidate, config, host_id, parallel)
            store.chains.upsert(chain)
            stats["evaluated"] += 1
            if chain.validated:
                stats["validated"] += 1
            stats[chain.verdict] = stats.get(chain.verdict, 0) + 1
        except Exception:
            log.exception("evaluation failed for chain %s", candidate.chain_id)
            stats["errors"] += 1

    return stats


def _evaluate_one(
    store: GraphStore,
    candidate: CandidateChain,
    config: EnsembleConfig,
    host_id: str,
    parallel: bool,
) -> Chain:
    """Evaluate one candidate chain, return a Chain ready to persist."""
    node_dicts = [_node_to_dict(store, nid) for nid in candidate.nodes]
    edge_dicts = [_edge_to_dict(store, eid) for eid in candidate.edges]

    # 1. Voters (prompt depends on config.mode).
    voter_responses = voters_mod.evaluate(
        chain_id=candidate.chain_id,
        chain_nodes=node_dicts,
        chain_edges=edge_dicts,
        voters=config.voters,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        parallel=parallel,
        mode=config.mode,
    )

    # 2. Judge (only if configured — never in fast or solo).
    judge_response = None
    if config.judge is not None:
        judge_response = judge_mod.evaluate(
            chain_id=candidate.chain_id,
            chain_nodes=node_dicts,
            chain_edges=edge_dicts,
            voter_responses=voter_responses,
            judge=config.judge,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )

    # 3. Final verdict + confidence.
    if judge_response is not None:
        verdict = judge_response.verdict
        confidence = judge_response.confidence
        reasoning = judge_response.reasoning
    else:
        verdict, confidence = aggregation.aggregate(voter_responses, config.voters)
        reasoning = _merge_reasoning(voter_responses)

    # 4. Layer 3 deterministic validation.
    validated = store.query.is_chain_valid(
        chain_nodes=candidate.nodes,
        chain_edges=candidate.edges,
    )

    # 5. Persist.
    return Chain(
        id=candidate.chain_id,
        chain_nodes=list(candidate.nodes),
        chain_edges=list(candidate.edges),
        verdict=verdict,
        prompt_version=PROMPT_VERSION,
        confidence=confidence,
        reasoning=reasoning,
        voter_outputs=[_voter_response_dict(r) for r in voter_responses],
        validated=validated,
        status="active",
    )


# ============================================================
# Serialization helpers
# ============================================================

def _node_to_dict(store: GraphStore, node_id: str) -> dict:
    node = store.nodes.get(node_id)
    if node is None:
        return {"id": node_id, "kind": "unknown", "name": node_id}
    return {
        "id": node.id,
        "kind": node.kind,
        "name": node.name,
        "attributes": node.attributes,
    }


def _edge_to_dict(store: GraphStore, edge_id: str) -> dict:
    edge = store.edges.get(edge_id)
    if edge is None:
        return {"id": edge_id, "kind": "unknown"}
    return {
        "id": edge.id,
        "kind": edge.kind,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "attributes": edge.attributes,
    }


def _voter_response_dict(r) -> dict:
    """Convert a VoterResponse to a JSON-safe dict for chains.voter_outputs."""
    d = asdict(r)
    d.pop("raw", None)
    return d


def _merge_reasoning(responses: list) -> str:
    parts = []
    for r in responses:
        if r.timeout:
            parts.append(f"[{r.model}] timeout")
        else:
            parts.append(f"[{r.model}] {r.reasoning}")
    return "\n\n".join(parts)