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
    candidate_edge_ids = set(candidate.edges)
    return Chain(
        id=candidate.chain_id,
        chain_nodes=list(candidate.nodes),
        chain_edges=list(candidate.edges),
        verdict=verdict,
        prompt_version=PROMPT_VERSION,
        confidence=confidence,
        reasoning=reasoning,
        voter_outputs=[
            _voter_response_dict(r, candidate_edge_ids) for r in voter_responses
        ],
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
    # Capabilities are a first-class signal (ADR-0009): the LLM must be
    # able to distinguish a read-only tool from one that executes code.
    return {
        "id": node.id,
        "kind": node.kind,
        "name": node.name,
        "capabilities": list(node.capabilities),
        "confidence": node.confidence,
        "attributes": node.attributes,
    }


def _edge_to_dict(store: GraphStore, edge_id: str) -> dict:
    edge = store.edges.get(edge_id)
    if edge is None:
        return {"id": edge_id, "kind": "unknown", "flow_kind": "unknown"}
    # Edge capabilities encode transfer semantics (ADR-0009); precision
    # records how the edge was resolved (ADR-0013/0014); flow_kind
    # separates "X calls Y" from "data from X reaches Y" (ADR-0015).
    # The schema makes flow_kind NOT NULL, so every edge the LLM sees
    # carries one — a missing value would be a construction bug.
    d = {
        "id": edge.id,
        "kind": edge.kind,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "flow_kind": edge.flow_kind,
        "capabilities": list(edge.capabilities),
        "precision": edge.precision,
        "attributes": edge.attributes,
    }
    # data_flow_confirmed is populated only for has_access_to (ADR-0015).
    # Omitted when absent rather than sent as null: a null would read as
    # a fourth value and invite the LLM to interpret it.
    if edge.data_flow_confirmed is not None:
        d["data_flow_confirmed"] = edge.data_flow_confirmed
    return d


def _voter_response_dict(r, candidate_edge_ids: set[str]) -> dict:
    """Convert a VoterResponse to a JSON-safe dict for chains.voter_outputs.

    ADR-0005 requires every edge cited in ``evidence`` to exist in the
    chain. Instead of silently trusting the model, each cited edge is
    classified as known/unknown relative to the candidate chain so the
    provenance record exposes hallucinated edge IDs.
    """
    d = asdict(r)
    d.pop("raw", None)
    unknown = [eid for eid in r.evidence if eid not in candidate_edge_ids]
    d["evidence_valid"] = not unknown
    d["evidence_unknown"] = unknown
    if unknown:
        log.warning(
            "voter %s cited %d edge(s) not in the chain: %s",
            r.model, len(unknown), unknown,
        )
    return d


def _merge_reasoning(responses: list) -> str:
    parts = []
    for r in responses:
        if r.timeout:
            parts.append(f"[{r.model}] timeout")
        else:
            parts.append(f"[{r.model}] {r.reasoning}")
    return "\n\n".join(parts)