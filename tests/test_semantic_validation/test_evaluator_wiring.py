"""The wiring: the evaluator stores what Layer 3 decided, and the row keeps it.

`_evaluate_one` is where a verdict meets the validator (ADR-0006). A.4
adds two columns to `chains`, so three things have to hold: the
evaluator writes them, `ChainsRepository` moves them, and a row written
before A.4 — the column is NULL — still reads as something honest.

B.9 adds the third level to the same wiring: a coherent chain is stored
as `"evidence"` only when a voter cited one of its edges, so the
coherent fixture here cites one and its no-citation twin is the level
below.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from aicorellator import GraphStore
from aicorellator.analysis.config_loader import EnsembleConfig
from aicorellator.analysis.evaluator import evaluate_chains
from aicorellator.analysis.models import VoterConfig, VoterResponse
from aicorellator.graph import Chain
from aicorellator.graph.query import CandidateChain

from .conftest import Graph


def _config() -> EnsembleConfig:
    """One fast voter, no judge — the cheapest real wiring (ADR-0005)."""
    return EnsembleConfig(
        voters=[VoterConfig(name="fake", base_url="x", role="fast")],
        mode="fast",
    )


def _response(
    chain_id: str, evidence: list[str] | None = None
) -> VoterResponse:
    return VoterResponse(
        chain_id=chain_id, model="fake", prompt_version="v1",
        exploitable=True, confidence=0.9, reasoning="r",
        evidence=list(evidence or []),
    )


def test_a_coherent_chain_whose_voter_cites_an_edge_is_stored_as_evidence(
    store: GraphStore, graph: Graph
) -> None:
    """The full path: coherent, and argued — the only `validated` row.

    B.9: `"evidence"` is the level a chain reads when all three checks
    passed, so this is what a validated row looks like after B.9. The
    citation is the chain's own edge, which is the whole criterion.
    """
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", entry, agent)
    graph.edge("e2", agent, sink)

    candidates = store.query.find_chains()
    assert len(candidates) == 1

    with patch(
        "aicorellator.analysis.voters.evaluate",
        return_value=[_response(candidates[0].chain_id, evidence=[e1])],
    ):
        stats = evaluate_chains(store, candidates, _config(), progress=False)

    chain = store.chains.get(candidates[0].chain_id)
    assert stats["validated"] == 1
    assert chain.validated is True
    assert chain.validated_kind == "evidence"
    assert chain.validation_notes == []


def test_a_coherent_chain_no_voter_argued_is_not_validated(
    store: GraphStore, graph: Graph
) -> None:
    """Same chain, a voter that cites nothing: `"semantic"`, not validated.

    The A.4 case this file used to store as `"semantic"` and validated:
    coherent, and nobody said why. B.9 keeps the level it reached —
    existence and coherence passed, so the kind is `"semantic"` — and
    turns `validated` off with the reason in the notes.
    """
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    graph.edge("e1", entry, agent)
    graph.edge("e2", agent, sink)

    candidates = store.query.find_chains()
    assert len(candidates) == 1

    with patch(
        "aicorellator.analysis.voters.evaluate",
        return_value=[_response(candidates[0].chain_id)],
    ):
        stats = evaluate_chains(store, candidates, _config(), progress=False)

    chain = store.chains.get(candidates[0].chain_id)
    assert stats["validated"] == 0
    assert chain.validated is False
    assert chain.validated_kind == "semantic"
    assert chain.validation_notes == ["no voter cited an edge from the chain"]


def test_a_disconnected_chain_is_stored_with_its_notes(
    store: GraphStore, graph: Graph
) -> None:
    """Existence holds, the hop runs backwards: stored, rejected, explained.

    `find_chains` enumerates by adjacency, not by direction, so a
    reversed hop reaches the evaluator. It is the validator — not the
    enumerator — that decides the candidate is not a chain.
    """
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", agent, entry, kind="uses_model")
    e2 = graph.edge("e2", agent, sink)

    candidate = CandidateChain(
        chain_id="chain:reversed",
        nodes=[entry, agent, sink],
        edges=[e1, e2],
        length=2,
    )

    with patch(
        "aicorellator.analysis.voters.evaluate",
        return_value=[_response(candidate.chain_id)],
    ):
        stats = evaluate_chains(store, [candidate], _config(), progress=False)

    chain = store.chains.get(candidate.chain_id)
    assert stats["evaluated"] == 1
    assert stats["validated"] == 0
    assert chain.validated is False
    assert chain.validated_kind == "existence"
    assert chain.validation_notes == [
        f"disconnected: edge {e1} links {agent} → {entry}, "
        f"expected {entry} → {agent}"
    ]


def test_upsert_round_trips_the_validation_fields(store: GraphStore) -> None:
    """`add` on the first call, the UPDATE path on the second."""
    store.chains.upsert(Chain(
        id="chain:1", chain_nodes=[], chain_edges=[],
        verdict="uncertain", prompt_version="v1",
        validated=False, validated_kind="existence",
        validation_notes=["missing edge edge:1"],
    ))
    first = store.chains.get("chain:1")
    assert first.validated_kind == "existence"
    assert first.validation_notes == ["missing edge edge:1"]

    store.chains.upsert(Chain(
        id="chain:1", chain_nodes=[], chain_edges=[],
        verdict="uncertain", prompt_version="v1",
        validated=True, validated_kind="semantic",
        validation_notes=[],
    ))
    second = store.chains.get("chain:1")
    assert second.validated is True
    assert second.validated_kind == "semantic"
    assert second.validation_notes == []


def test_a_row_written_before_a4_reads_as_existence(
    store: GraphStore,
) -> None:
    """The column is NULL for such a row; the read path names the level.

    A pre-A.4 chain was judged by the existence rule and nothing else,
    which is exactly what `"existence"` records.
    """
    store.chains.add(Chain(
        id="chain:old", chain_nodes=["n:1"], chain_edges=["e:1"],
        verdict="exploitable", prompt_version="v1", validated=True,
    ))

    raw = store._conn.execute(
        "SELECT validated_kind FROM chains WHERE id = 'chain:old'"
    ).fetchone()
    assert raw["validated_kind"] is None

    chain = store.chains.get("chain:old")
    assert chain.validated is True
    assert chain.validated_kind == "existence"
    assert chain.validation_notes == []


def test_the_column_only_accepts_the_known_levels(store: GraphStore) -> None:
    """The CHECK is the schema's half of the level contract.

    B.9 widens it to the third level and no further: `"evidence"` is a
    row the schema accepts, and a level nobody defined is still
    rejected.
    """
    store.chains.add(Chain(
        id="chain:evidence", chain_nodes=[], chain_edges=[],
        verdict="uncertain", prompt_version="v1",
        validated=True, validated_kind="evidence",
    ))
    assert store.chains.get("chain:evidence").validated_kind == "evidence"

    with pytest.raises(sqlite3.IntegrityError):
        store.chains.add(Chain(
            id="chain:bad", chain_nodes=[], chain_edges=[],
            verdict="uncertain", prompt_version="v1",
            validated_kind="structural",
        ))
