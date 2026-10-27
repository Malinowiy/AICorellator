"""The wiring: the evaluator stores what Layer 3 decided, and the row keeps it.

`_evaluate_one` is where a verdict meets the validator (ADR-0006). A.4
adds two columns to `chains`, so three things have to hold: the
evaluator writes them, `ChainsRepository` moves them, and a row written
before A.4 — the column is NULL — still reads as something honest.
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


def _response(chain_id: str) -> VoterResponse:
    return VoterResponse(
        chain_id=chain_id, model="fake", prompt_version="v1",
        exploitable=True, confidence=0.9, reasoning="r", evidence=[],
    )


def test_a_coherent_chain_is_stored_as_semantic(
    store: GraphStore, graph: Graph
) -> None:
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
    assert stats["validated"] == 1
    assert chain.validated is True
    assert chain.validated_kind == "semantic"
    assert chain.validation_notes == []


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


def test_the_column_only_accepts_the_two_levels(store: GraphStore) -> None:
    """The CHECK is the schema's half of the two-value contract."""
    with pytest.raises(sqlite3.IntegrityError):
        store.chains.add(Chain(
            id="chain:bad", chain_nodes=[], chain_edges=[],
            verdict="uncertain", prompt_version="v1",
            validated_kind="structural",
        ))
