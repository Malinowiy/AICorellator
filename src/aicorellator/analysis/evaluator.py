"""Evaluator — orchestrate chain evaluation and persist results.

Pipeline per chain (ADR-0005 + ADR-0006):

    1. Enumerate candidate (already done by QueryLayer.find_chains)
    2. Voters evaluate (parallel or sequential)
    3. Judge issues final verdict (only in full/custom mode)
    4. Layer 3 deterministic validation: existence plus coherence
       (QueryLayer.validate_chain, A.4), then evidence presence —
       did any voter cite an edge of this chain (B.9)
    5. Persist to chains table

Pipeline per chain *group* (1.3e, ADR-0010 — indirect prompt injection):

    1. Enumerate and select (already done by find_chain_groups +
       prioritize_chain_groups)
    2. Voters evaluate the write/read pair with the v1.6.0 group prompt
    3. Judge issues the final verdict (only when configured)
    4. Layer 3 validation against the group's own edge set
    5. Persist to chain_groups table

The two are separate functions on purpose: the payload and the prompt
differ enough that folding them together would obscure both. What they
share is the ensemble (voters, judge, aggregation) and the validation
discipline — the same objects, called the same way.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Iterable

from ..graph.models import Chain, ChainGroupRecord, Edge, Node
from ..graph.store import GraphStore
from ..graph.query import ANCHOR_EDGE_KINDS, CandidateChain, ChainGroup
from ..patterns import TRAVERSABLE_FLOW_KINDS
from . import aggregation, judge as judge_mod, voters as voters_mod
from .config_loader import EnsembleConfig
from .models import VoterResponse
from .prompts import (
    GROUP_SYSTEM_PROMPT,
    PROMPT_VERSION,
    build_group_prompt,
)


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


def evaluate_chain_groups(
    store: GraphStore,
    groups: list[ChainGroup],
    config: EnsembleConfig,
    host_id: str = "localhost",
    progress: bool = True,
    parallel: bool | None = None,
) -> dict[str, int]:
    """Evaluate selected chain groups and persist verdicts (1.3e).

    ADR-0006's Layer 2 applied to a group (ADR-0010): the candidate is a
    *pair* of chains that meet through an untrusted source, the prompt is
    v1.6.0's group prompt, and the verdict is stored in `chain_groups`
    instead of `chains`. Everything else is the linear path's: the same
    voters, the same judge, the same aggregation, the same Layer 3
    discipline.

    Args:
        store: GraphStore with nodes/edges already loaded.
        groups: selected groups, from
            `prioritize_chain_groups(find_chain_groups())`. An unscored
            group is evaluated too — scoring is 1.3c's business and the
            verdict must not depend on it (the score is passed to the
            prompt, and the prompt says it is a hint).
        config: EnsembleConfig (voters + optional judge + mode).
        host_id: current host.
        progress: print progress to stdout.
        parallel: if None, auto-derive from config.is_parallel_safe.

    Returns:
        Stats dict, same shape as `evaluate_chains`: `evaluated`,
        `validated`, `exploitable`, `not_exploitable`, `uncertain`,
        `errors`.

    Layer 3 for a group differs from the linear path's in what it can
    check. A group is `validated` when

      - every edge of the group's write and read chains exists in the
        graph,
      - every edge a voter cited belongs to the group's own edge set, and
      - every hop of both chains resolved (a chain with a hole is not the
        chain that was enumerated, so it cannot be confirmed).

    Nodes are not validated: the chains were enumerated from the store,
    so their nodes exist by construction — what Layer 3 can catch here is
    a hallucinated *evidence* edge, which is exactly what the linear
    path's `_voter_response_dict` catches (and records per voter; a group
    row has no `voter_outputs` column, so here it is logged and folded
    into `validated`).
    """
    if parallel is None:
        parallel = config.is_parallel_safe

    log.info(
        "evaluating %d chain groups: mode=%s voters=%d judge=%s parallel=%s",
        len(groups), config.mode, len(config.voters),
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

    total = len(groups)
    for i, group in enumerate(groups, start=1):
        if progress:
            print(f"  [{i}/{total}] {group.group_id}", flush=True)

        try:
            record = _evaluate_one_group(store, group, config, host_id, parallel)
            store.chain_groups.upsert(record)
            stats["evaluated"] += 1
            if record.validated:
                stats["validated"] += 1
            stats[record.verdict] = stats.get(record.verdict, 0) + 1
        except Exception:
            log.exception("evaluation failed for chain group %s", group.group_id)
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
    # B.4b (ADR-0019 §2): the guardrails near this chain, read from the
    # graph rather than from the chain — a `guards` edge is `structural`
    # and never appears in an enumerated chain. Empty is the normal case.
    guardrails = _guardrails_for_chain(store, candidate.nodes)

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
        guardrails=guardrails,
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
            guardrails=guardrails,
        )

    # 3. Final verdict + confidence.
    if judge_response is not None:
        verdict = judge_response.verdict
        confidence = judge_response.confidence
        reasoning = judge_response.reasoning
    else:
        verdict, confidence = aggregation.aggregate(voter_responses, config.voters)
        reasoning = _merge_reasoning(voter_responses)

    # 4. Layer 3 deterministic validation: existence plus structure.
    #    A chain that exists but is disconnected, untyped or anchored on
    #    a source that is not a source is stored with validated = False
    #    and the reasons in validation_notes (A.4).
    validated, validated_kind, validation_notes = store.query.validate_chain(
        chain_nodes=candidate.nodes,
        chain_edges=candidate.edges,
    )
    if not validated:
        log.info(
            "chain %s rejected by Layer 3 (%s): %s",
            candidate.chain_id, validated_kind, "; ".join(validation_notes),
        )
    else:
        # 4b. Evidence presence (B.9, ADR-0018 revision 2) — the third
        #     level. The two checks above are checks on the chain; this
        #     one is a check on what the ensemble said about it: at
        #     least one voter cited at least one edge of *this* chain.
        #     A voter that cited nothing, or cited only edges outside
        #     the chain, has not argued the chain, and a chain nobody
        #     argued is not a validated finding. The level reached is
        #     "semantic": existence and coherence did pass, and this is
        #     the last level that did.
        has_evidence, evidence_note = _evidence_present(
            voter_responses, set(candidate.edges),
        )
        if has_evidence:
            validated_kind = "evidence"
        else:
            # `_evidence_present` is total: a False always carries its
            # note, so the stored row says why it was rejected.
            validated, validated_kind = False, "semantic"
            validation_notes = [evidence_note]
            log.info(
                "chain %s rejected by Layer 3 (semantic): %s",
                candidate.chain_id, evidence_note,
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
        validated_kind=validated_kind,
        validation_notes=validation_notes,
        status="active",
    )


def _evaluate_one_group(
    store: GraphStore,
    group: ChainGroup,
    config: EnsembleConfig,
    host_id: str,
    parallel: bool,
) -> ChainGroupRecord:
    """Evaluate one chain group, return a row ready to persist.

    Mirrors `_evaluate_one` step for step. Two differences are worth
    naming:

      - the voters are driven with the *group* prompt. `voters.evaluate`
        picks between the linear prompts internally, so the prompt-
        agnostic runners it is built on (`_evaluate_sequential` /
        `_evaluate_parallel`) are called directly here; voters.py itself
        is untouched (`--mode solo`'s combined prompt has no group
        equivalent, and a group is not a chain).
      - the judge is the linear judge. It receives the group's two sides
        concatenated — write chain into the anchor, read chain out of it,
        which is the walk the anchor makes possible — plus the voters'
        group-aware reasoning. A group-specific judge prompt is not part
        of this item; the group framing reaches the judge through the
        voter reasoning it weighs.

    Guardrails arrive per side (B.4b, ADR-0019 §3): the payload carries
    `{"write": [...], "read": [...], "anchor": [...]}`, and the judge —
    which is the linear one — gets the three lists flattened back into
    the single `guardrails` list its prompt defines.

    The row's `lethal_trifecta` is a *projection*, not a verdict (B.6):
    1.3d writes the flag on every node, and the group carries "some node
    on the read side has it". The write side is not consulted — the
    attacker's content enters through the read, and a trifecta node
    upstream of the write is another chain's problem, which is the
    reasoning `graph/query.py::_score_lethal_trifecta` uses for its own
    +5. A group that was never evaluated keeps `False`: the projection
    is the evaluator's, the same "not computed" convention `validated`
    follows.

    `prompt_version` / `voter_outputs` are B.7: the linear path has
    written both to `chains` since Phase 0, and a group verdict is
    traceable by the same two facts. The responses go through
    `_voter_response_dict` with `group_edge_ids` — the same set Layer 3
    below judges the citations against — so a group's `evidence_valid`
    answers the same question a chain's does: was the cited edge one of
    *this* candidate's.
    """
    payload, node_dicts, edge_dicts = _group_payload(store, group)
    user_prompt = build_group_prompt(group.group_id, payload)

    # The judge is the linear judge, so it takes the same flat
    # `guardrails` list the linear path passes (B.4b, ADR-0019 §3). The
    # pair's three sides are flattened for it — nothing is lost, because
    # the judge's rule is to weigh the voters' guardrail reasoning rather
    # than to split the sides itself.
    guardrails = _dedupe_by_id(
        guardrail
        for side in ("write", "read", "anchor")
        for guardrail in payload["guardrails"][side]
    )

    runner = (
        voters_mod._evaluate_parallel
        if parallel
        else voters_mod._evaluate_sequential
    )
    voter_responses = runner(
        config.voters,
        group.group_id,
        GROUP_SYSTEM_PROMPT,
        user_prompt,
        config.temperature,
        config.max_tokens,
    )

    judge_response = None
    if config.judge is not None:
        judge_response = judge_mod.evaluate(
            chain_id=group.group_id,
            chain_nodes=node_dicts,
            chain_edges=edge_dicts,
            voter_responses=voter_responses,
            judge=config.judge,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            guardrails=guardrails,
        )

    if judge_response is not None:
        verdict = judge_response.verdict
        confidence = judge_response.confidence
        reasoning = judge_response.reasoning
    else:
        verdict, confidence = aggregation.aggregate(voter_responses, config.voters)
        reasoning = _merge_reasoning(voter_responses)

    # Layer 3: the group's own edges must exist, every cited edge must be
    # one of them (a citation outside the pair is a hallucination even
    # when the edge exists elsewhere in the graph), and every hop of both
    # chains must have resolved — a chain with a hole is not the chain
    # that was enumerated, so nothing about it can be confirmed. A.4
    # labels the result (`validated_kind`) and changes nothing else.
    chains = payload["write_chains"] + payload["read_chains"]
    holes = [
        chain["chain_id"] for chain in chains
        if len(chain["edges"]) != len(chain["nodes"]) - 1
    ]
    group_edge_ids = {edge["id"] for edge in edge_dicts}
    _, missing = store.query.validate_edges(sorted(group_edge_ids))
    cited = [eid for r in voter_responses for eid in r.evidence]
    outside = [eid for eid in cited if eid not in group_edge_ids]
    validated = not missing and not outside and not holes
    if holes:
        log.warning(
            "chain group %s: %d chain(s) do not resolve against the graph: %s",
            group.group_id, len(holes), holes,
        )
    if missing:
        log.warning(
            "chain group %s: %d edge(s) of the group are not in the graph: %s",
            group.group_id, len(missing), missing,
        )
    if outside:
        log.warning(
            "chain group %s: voters cited %d edge(s) outside the group: %s",
            group.group_id, len(outside), outside,
        )

    record = group.to_record()
    record.verdict = verdict
    record.confidence = confidence
    record.reasoning = reasoning
    record.validated = validated
    # A.4's label on the same check: "semantic" when the group's Layer 3
    # test passed, "existence" otherwise — the level the group reached,
    # spelled the way a chain spells it. No new check.
    record.validated_kind = "semantic" if validated else "existence"
    # B.6: the group-level column is 1.3d's per-node flag projected onto
    # the read side. The map `_group_payload` already built is that flag
    # read once per node (`_lethal_trifecta_flag`), so this is a read of
    # a read — no second store walk, and no capability walk here either
    # (the stage is the single writer of the definition, ADR-0009).
    record.lethal_trifecta = any(
        flag
        for chain in payload["read_chains"]
        for flag in chain["lethal_trifecta"].values()
    )
    # B.7: the verdict's provenance. The same helper the linear path
    # uses, with the group's own edge set as the set a citation is
    # checked against — the set Layer 3 above already judged the same
    # citations by, so the two never disagree.
    record.prompt_version = PROMPT_VERSION
    record.voter_outputs = [
        _voter_response_dict(r, group_edge_ids) for r in voter_responses
    ]
    return record


# ------------------------------------------------------------
# Layer 3's third level — evidence presence (B.9, ADR-0018 rev. 2)
# ------------------------------------------------------------

def _evidence_present(
    voter_responses: list[VoterResponse],
    candidate_edges: set[str],
) -> tuple[bool, str | None]:
    """Did any voter cite an edge of the candidate chain? (B.9)

    ADR-0018 revision 2's third level, and the narrowest of the three
    questions: the cited edge must be one of **this chain's** edges, not
    merely an edge the graph has. A voter citing a real edge of some
    other path has not argued this one, so it does not count — and an
    empty `evidence` list does not count either, which is the case B.9
    exists for (`exploitable: true, evidence: []` used to validate).

    All-timeout and no-voters are the same answer to this question — no
    evidence — and the note says which happened. A voter that answered
    and cited nothing inside the chain gets the bare note; the count is
    for the citations that left the chain.

    It reads the raw `evidence` lists rather than `_voter_response_dict`'s
    per-voter `evidence_valid` / `evidence_unknown` fields: those
    classify one voter's citations against the chain, and this is the
    aggregate the chain's `validated` is made of.

    Returns `(True, None)` when the evidence is there, and
    `(False, note)` when it is not — `note` is the `validation_notes`
    entry the caller stores.

    What it cannot see: whether the argument made *about* the edge is
    sound. A voter that cites an edge of the chain and reasons badly
    still passes; the layer validates structure, and the reasoning is
    the model's (ADR-0018 §1).
    """
    for response in voter_responses:
        if candidate_edges.intersection(response.evidence):
            return True, None

    if not any(not response.timeout for response in voter_responses):
        return False, (
            "no voter cited an edge from the chain (no voters responded)"
        )

    outside = [
        edge_id
        for response in voter_responses
        for edge_id in response.evidence
        if edge_id not in candidate_edges
    ]
    if outside:
        return False, (
            "no voter cited an edge from the chain "
            f"({len(outside)} citation(s) outside the chain)"
        )
    return False, "no voter cited an edge from the chain"


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
        "observed_by": list(node.observed_by),
        "confidence": node.confidence,
        "attributes": node.attributes,
    }


def _edge_to_dict(store: GraphStore, edge_id: str) -> dict:
    edge = store.edges.get(edge_id)
    if edge is None:
        return {"id": edge_id, "kind": "unknown", "flow_kind": "unknown"}
    # Edge capabilities encode transfer semantics (ADR-0009); precision
    # records how the edge was resolved (ADR-0013/0014); flow_kind
    # separates "X calls Y" from "data from X reaches Y" (ADR-0015); and
    # observed_by says who confirmed the edge — `["ast"]` on Layer 1a,
    # `["ast", "runtime"]` once Layer 2 has seen it (ADR-0016). Both
    # `flow_kind` and `observed_by` are NOT NULL in the schema, so every
    # edge the LLM sees carries them — a missing value would be a
    # construction bug.
    d = {
        "id": edge.id,
        "kind": edge.kind,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "flow_kind": edge.flow_kind,
        "capabilities": list(edge.capabilities),
        "observed_by": list(edge.observed_by),
        "precision": edge.precision,
        "attributes": edge.attributes,
    }
    # data_flow_confirmed is populated only by Layer 2 (ADR-0016); on an
    # AST edge it is NULL. Omitted when absent rather than sent as null:
    # a null would read as a fourth value and invite the LLM to
    # interpret it.
    if edge.data_flow_confirmed is not None:
        d["data_flow_confirmed"] = edge.data_flow_confirmed
    return d


def _guardrails_for_chain(
    store: GraphStore,
    node_ids: Iterable[str],
) -> list[dict]:
    """The guardrail nodes connected to any of `node_ids` by a `guards` edge.

    This is the graph's answer to "is there a guardrail on this path", and
    the one place the evaluator reads `guards` edges. `guards` is
    `structural` (ADR-0015, decision 5), so the edge is never part of an
    enumerated chain and is not in the candidate's `edges`; it is read
    back from the store and filtered by the chain's own node ids — the
    same read the brief for B.4b names, and the same shape the rest of
    this module uses to go from ids to dicts.

    The direction is `guardrail → component` (ADR-0019 §1), so the
    chain's node is the edge's *target* and the guardrail is its
    *source*. One dict per guardrail, in edge-id order (the repository
    orders by id), so the payload is deterministic across runs.

    One level of indirection is read as well, and only one (ADR-0019
    rev 1 §3): a `tool` whose `parent_server` names an `mcp_server` node
    also sees the guardrails of that server's `guards` edge. The
    middleware protects the server, the server contains the tool, the
    chain passes through the tool — so a payload that omitted the
    middleware would hide a guardrail the graph holds. The graph gains
    no edge for this; the read simply follows the tool's `parent_server`
    attribute to the server node and includes it in the targets. A
    server's own server is not followed.

    The dedupe is by guardrail id, so a guardrail on both the tool and
    its server is one entry. Empty when the chain has no guardrail near
    it — the default state of every path, and not a weakness. `[]`,
    never `None`: the prompt's `guardrails` key is always present
    (ADR-0019 §2).
    """
    wanted = set(node_ids)
    if not wanted:
        return []
    targets = wanted | _parent_server_ids(store, wanted)
    found: list[dict] = []
    seen: set[str] = set()
    for edge in store.edges.list(kind="guards", status="active"):
        if edge.target_id not in targets or edge.source_id in seen:
            continue
        node = store.nodes.get(edge.source_id)
        if node is None or node.kind != "guardrail":
            # B.4a emits `guardrail → component` and nothing else, so this
            # is a construction bug (or an edge left behind by a deleted
            # node). Skipping it keeps the prompt from claiming a guardrail
            # the graph does not have — the same reason ADR-0019 refuses to
            # emit `guards` on co-location.
            log.warning(
                "guards edge %s has a non-guardrail source %s; skipped",
                edge.id, edge.source_id,
            )
            continue
        seen.add(edge.source_id)
        found.append(_guardrail_to_dict(node))
    return found


def _parent_server_ids(store: GraphStore, node_ids: Iterable[str]) -> set[str]:
    """The `mcp_server` node ids named by a tool's `parent_server`.

    ADR-0019 rev 1 §3: the one level of indirection the linear-chain
    read adds. Only a `tool` node is asked — a server is not a container
    of a server — and only a string `parent_server` is read, because the
    attribute is the framework detector's (`fastmcp.py`,
    `mcp_python.py`) and a node that does not carry it names no server.
    The server node is found by *name*, the same key `parent_server`
    holds and `mcp_server_nodes` names its node with.

    Empty when no node names a server, so the caller's set is unchanged
    and the read is exactly B.4b's.
    """
    names = set()
    for node_id in node_ids:
        node = store.nodes.get(node_id)
        if node is None or node.kind != "tool":
            continue
        parent = node.attributes.get("parent_server")
        if isinstance(parent, str) and parent:
            names.add(parent)
    if not names:
        return set()
    return {
        node.id
        for node in store.nodes.list(kind="mcp_server")
        if node.name in names
    }


def _guardrail_to_dict(node: Node) -> dict:
    """One guardrail, as the prompt's `guardrails` section shows it.

    Deliberately not `_node_to_dict`: `confidence` scores how likely a
    *chain* is exploitable, and a guardrail is never a chain node — it
    is a reference beside the chain, not a step of it.

    `observed_by` does travel (ADR-0024 §6), and it is the one field
    that tells the guardrail's three producers apart: `["ast"]` for a
    node derived from a capability (B.4-gap-2), `["ast",
    "framework:fastmcp_middleware"]` for one a framework detector
    recognized (B.4-gap-5), and `["ast", "static_llm:<model_id>"]` for
    one the LLM-classifier proposed. Without it the model sees three
    identically-shaped guardrails and cannot tell a syntactic class
    from a model's guess — the distinction ADR-0016 puts on every node.

    What the model gets is what the graph knows about the guardrail:
    its name, its kind, its capabilities, who observed it and its
    attributes. No `policy` and no `enforcement_point` is synthesized
    (ADR-0019 §4); D.6 adds those to the node, and then they arrive
    inside `attributes`.
    """
    return {
        "id": node.id,
        "kind": node.kind,
        "name": node.name,
        "capabilities": list(node.capabilities),
        "observed_by": list(node.observed_by),
        "attributes": node.attributes,
    }


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


# ============================================================
# Chain group helpers (1.3e)
# ============================================================

def _group_payload(store: GraphStore, group: ChainGroup) -> tuple[dict, list[dict], list[dict]]:
    """Build a group's prompt payload and its flattened node/edge lists.

    Returns `(payload, node_dicts, edge_dicts)`:

      - `payload` is what `build_group_prompt` serializes: the anchor,
        the write chains, the read chains (each with the per-node
        `lethal_trifecta` flag), the guardrails split by side, and 1.3c's
        score, outside the marker block.
      - `node_dicts` / `edge_dicts` are the same nodes and edges in one
        flat list, write side first. They go to the judge, which is the
        linear judge and expects a path: the pair concatenated at the
        anchor *is* a path (`entry → ... → source → reader → ... →
        sink`), and the shared anchor appears once.

    The two sides are zipped with their chain ids, which is how the
    evaluator says "this chain" to the model without re-enumerating: a
    group stores node sequences and ids, and the ids are what the prompt
    labels each chain with (ADR-0010 — the ids are the durable
    reference).
    """
    payload = {
        "source": _source_to_dict(store, group),
        "write_chains": [
            _chain_payload(store, chain_id, nodes, with_flag=False)
            for chain_id, nodes in zip(group.write_chain_ids, group.write_chains)
        ],
        "read_chains": [
            _chain_payload(store, chain_id, nodes, with_flag=True)
            for chain_id, nodes in zip(group.read_chain_ids, group.read_chains)
        ],
        # Per side (B.4b, ADR-0019 §3). Read from the graph, not from the
        # chains: `guards` edges are `structural` and are never part of an
        # enumerated chain.
        "guardrails": _guardrails_for_group(store, group),
        "score": group.score,
        "score_components": group.score_components,
        "priority": group.priority,
    }

    chains = payload["write_chains"] + payload["read_chains"]
    node_dicts = _dedupe_by_id(node for chain in chains for node in chain["nodes"])
    edge_dicts = _dedupe_by_id(edge for chain in chains for edge in chain["edges"])
    return payload, node_dicts, edge_dicts


def _guardrails_for_group(store: GraphStore, group: ChainGroup) -> dict:
    """The guardrails near a group, split by side (ADR-0019 §3).

    `{"write": [...], "read": [...], "anchor": [...]}`, the three lists
    the group prompt documents. The split is a *partition*, not three
    views of one set: the anchor is a node of both chains, so a guardrail
    on the channel would otherwise land on both sides and the model could
    not tell "on the channel" from "on both sides". The ADR's three-way
    split only says something if each guardrail lands in exactly one
    list, so the anchor is excluded from `write` and `read`.

    Each list is empty when nothing guards that side — the common case,
    and `[]` rather than a missing key, so the shape the prompt documents
    is the shape the payload always has.
    """
    anchor = group.untrusted_source_id
    write_nodes = {node for chain in group.write_chains for node in chain}
    read_nodes = {node for chain in group.read_chains for node in chain}
    return {
        "write": _guardrails_for_chain(store, write_nodes - {anchor}),
        "read": _guardrails_for_chain(store, read_nodes - {anchor}),
        "anchor": _guardrails_for_chain(store, [anchor]),
    }


def _chain_payload(
    store: GraphStore,
    chain_id: str,
    nodes: list[str],
    *,
    with_flag: bool,
) -> dict:
    """One chain of a group, serialized for the prompt.

    `with_flag` adds `lethal_trifecta` — a map from node id to the 1.3d
    attribute — for the read side only: the flag is about a node that
    *reads* untrusted content on its way to a sink, so on the write side
    it would be noise.
    """
    payload = {
        "chain_id": chain_id,
        "nodes": [_node_to_dict(store, node_id) for node_id in nodes],
        "edges": [
            _edge_to_dict(store, edge_id)
            for edge_id in _chain_edges(store, chain_id, nodes)
        ],
    }
    if with_flag:
        payload["lethal_trifecta"] = {
            node_id: _lethal_trifecta_flag(store, node_id) for node_id in nodes
        }
    return payload


def _chain_edges(
    store: GraphStore,
    chain_id: str,
    nodes: list[str],
) -> list[str]:
    """One enumerated chain's edge ids, in hop order.

    A group stores its chains as node sequences (and their ids); the
    edges are what the enumerator walked and they are not stored, so they
    are recovered here: for each consecutive pair, the active edges of
    that pair a chain hop can be, first by id — the same query and the
    same order `QueryLayer._write_chains_to` / `_read_chains_from` walk.
    On the four corpora every pair resolves to exactly one edge, so the
    reconstruction *is* the enumeration; a pair with several parallel
    edges would leave the choice ambiguous, which is why a mismatch
    against the stored id is logged (the ids are content-derived, so they
    are the only way to tell).

    `_hop_edges` states which edges those are, including the one hop the
    traversability rule does not cover: a store-joined write chain's last
    hop is `writes_to`, which is `structural` (ADR-0021 §4) and therefore
    not traversable, and the enumerator crosses it anyway because it is
    the anchor's declaration rather than a path hop (ADR-0021 §6).

    The ids are needed twice: the prompt's `evidence` cites edges, and
    Layer 3 checks the citations against this set.
    """
    edges: list[str] = []
    for source_id, target_id in zip(nodes, nodes[1:]):
        candidates = _hop_edges(store, source_id, target_id)
        if not candidates:
            # The graph moved under the group (a delta rescan). Leave the
            # hop out; validation will reject the group rather than the
            # evaluator inventing an edge.
            log.warning(
                "chain %s: no active edge %s → %s", chain_id, source_id, target_id
            )
            continue
        edges.append(candidates[0].id)

    if len(edges) == len(nodes) - 1:
        rebuilt = store.query._chain_id(nodes, edges)
        if rebuilt != chain_id:
            log.warning(
                "chain %s: reconstructed edges %s give id %s "
                "(parallel edges between a hop's nodes)",
                chain_id, edges, rebuilt,
            )
    return edges


def _hop_edges(
    store: GraphStore,
    source_id: str,
    target_id: str,
) -> list[Edge]:
    """The active edges one chain hop can be, in id order.

    A traversable edge first, which is the rule `_write_chains_to` /
    `_read_chains_from` apply to every hop along a path. When the pair
    has none, the anchor kinds (`writes_to`) are the fallback: the write
    chain's last hop is `writer → store` since B.3c / B.3b-mcp-catalogue
    and that edge is `structural` — a declaration about a place, not a
    path hop (ADR-0021 §4, §6) — so the enumerator crosses it while the
    traversability rule refuses it. Without the fallback every
    store-joined chain is rejected by Layer 3 for an edge the enumerator
    really did walk.

    `reads_from` is deliberately not a fallback: it joins the write chain
    to the read chain at the store (ADR-0021 §6); it is not a hop of
    either one.
    """
    rows = store.edges.list(
        source_id=source_id, target_id=target_id, status="active"
    )
    traversable = [
        edge for edge in rows if edge.flow_kind in TRAVERSABLE_FLOW_KINDS
    ]
    if traversable:
        return traversable
    return [edge for edge in rows if edge.kind in ANCHOR_EDGE_KINDS]


def _lethal_trifecta_flag(store: GraphStore, node_id: str) -> bool:
    """The 1.3d `lethal_trifecta` attribute of one node.

    Read, never recomputed: the stage (18) is the single writer, and its
    absence means `False` — a graph scanned before 1.3d carries no
    attribute, and the prompt then reports the flag as false rather than
    walking capabilities here. The *scorer* does walk them (it has to, to
    score a pre-1.3d graph), so on such a graph
    `score_components["lethal_trifecta"] == 5` can sit next to a false
    flag; the group prompt says the flag is derived from capabilities and
    that the score is a hint, and the score is not an input to the
    verdict.
    """
    node = store.nodes.get(node_id)
    if node is None:
        return False
    return bool(node.attributes.get("lethal_trifecta", False))


def _source_to_dict(store: GraphStore, group: ChainGroup) -> dict:
    """The group's anchor as a prompt dict: a node dict plus `source_type`.

    `source_type` is promoted out of `attributes` so the prompt's channel
    question can be asked directly (ADR-0010's five classes); it stays in
    `attributes` too, because that is where the stage wrote it and
    `_node_to_dict` serializes attributes wholesale — one field, two
    spellings, and the model sees both.

    A missing node cannot normally happen (deleting the source cascades
    the group away); the fallback keeps a report readable if a caller
    passes groups from another graph.
    """
    node = store.nodes.get(group.untrusted_source_id)
    if node is None:
        return {
            "id": group.untrusted_source_id,
            "kind": "untrusted_source",
            "name": group.untrusted_source_name,
            "source_type": None,
            "capabilities": [],
            "confidence": 0.0,
            "attributes": {},
        }
    return {
        "id": node.id,
        "kind": node.kind,
        "name": node.name,
        "source_type": node.attributes.get("source_type"),
        "capabilities": list(node.capabilities),
        "confidence": node.confidence,
        "attributes": node.attributes,
    }


def _dedupe_by_id(items) -> list[dict]:
    """One dict per id, input order kept (the anchor is in both halves)."""
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        out.append(item)
    return out