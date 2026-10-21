"""Chain-group evaluation — the 1.3e path with mocked voters.

The group path shares the ensemble with the linear one but not the
payload, the prompt or the table. These tests pin what is *its*: the
write/read pair reaches the model as a pair, the score travels beside it
without deciding the verdict, Layer 3 checks the group's own edge set,
and the row that lands keeps the columns 1.3c paid for.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aicorellator import GraphStore
from aicorellator.analysis.config_loader import EnsembleConfig
from aicorellator.analysis.evaluator import evaluate_chain_groups
from aicorellator.analysis.models import (
    JudgeResponse,
    VoterConfig,
    VoterResponse,
)
from aicorellator.analysis.prompts import GROUP_SYSTEM_PROMPT
from aicorellator.graph import ChainGroup, Edge, Node


SOURCE = "untrusted_source:" + "s" * 16
ENTRY = "entry_point:" + "e" * 16
READER = "tool:" + "r" * 16
SINK = "sink:" + "f" * 16
E_WRITE = "edge:" + "1" * 16
E_READ_1 = "edge:" + "2" * 16
E_READ_2 = "edge:" + "3" * 16
# In the graph, not in the group: an evidence edge a voter could cite and
# still be wrong about.
E_ELSEWHERE = "edge:" + "9" * 16
GROUP_ID = "chain_group:" + "g" * 16


def _build(store: GraphStore, trifecta: bool = False) -> ChainGroup:
    """A group whose two sides exist in the graph.

        entry --writes_to--> source --has_access_to--> reader
               --has_access_to(data)--> sink

    The write chain is `entry → source`, the read chain is
    `source → reader → sink`, and the group's chain ids are derived with
    the Query Layer's own `_chain_id` — the ids are content-derived, so a
    test that invented them would not be testing the real thing.
    """
    store.nodes.upsert(Node(
        id=SOURCE, kind="untrusted_source", name="rag_corpus:catalogue.list",
        host_id="localhost", capabilities=["reads_untrusted"],
        attributes={"source_type": "rag_corpus"},
    ))
    store.nodes.upsert(Node(
        id=ENTRY, kind="entry_point", name="POST /upload", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=READER, kind="tool", name="get_documents", host_id="localhost",
        attributes={"returns_data": True,
                    **({"lethal_trifecta": True} if trifecta else {})},
    ))
    store.nodes.upsert(Node(
        id=SINK, kind="sink", name="run_shell", host_id="localhost",
        capabilities=["executes_shell"],
    ))

    store.edges.upsert(Edge(
        id=E_WRITE, source_id=ENTRY, target_id=SOURCE, kind="writes_to",
        host_id="localhost", flow_kind="control",
    ))
    store.edges.upsert(Edge(
        id=E_READ_1, source_id=SOURCE, target_id=READER,
        kind="has_access_to", host_id="localhost", flow_kind="control",
    ))
    store.edges.upsert(Edge(
        id=E_READ_2, source_id=READER, target_id=SINK, kind="has_access_to",
        host_id="localhost", flow_kind="data",
        data_flow_confirmed="parameter_match",
    ))
    store.edges.upsert(Edge(
        id=E_ELSEWHERE, source_id=ENTRY, target_id=SINK, kind="uses_model",
        host_id="localhost", flow_kind="control",
    ))

    write_nodes = [ENTRY, SOURCE]
    read_nodes = [SOURCE, READER, SINK]
    return ChainGroup(
        group_id=GROUP_ID,
        untrusted_source_id=SOURCE,
        untrusted_source_name="rag_corpus:catalogue.list",
        write_chain_ids=[store.query._chain_id(write_nodes, [E_WRITE])],
        read_chain_ids=[
            store.query._chain_id(read_nodes, [E_READ_1, E_READ_2]),
        ],
        write_chains=[write_nodes],
        read_chains=[read_nodes],
        score=7.0,
        score_components={"lethal_trifecta": 0, "writes_db": 3, "total": 7},
        priority="low",
    )


def _config(voters: int = 1, with_judge: bool = False) -> EnsembleConfig:
    voter_configs = [
        VoterConfig(name=f"v{i}", base_url="x", role="voter")
        for i in range(voters)
    ]
    judge = None
    if with_judge:
        judge = VoterConfig(name="j", base_url="x", role="judge")
    return EnsembleConfig(
        voters=voter_configs,
        judge=judge,
        mode="full" if with_judge else "fast",
    )


def _voter_response(
    evidence: list[str],
    exploitable: bool = True,
    confidence: float = 0.8,
    model: str = "v0",
) -> VoterResponse:
    return VoterResponse(
        chain_id=GROUP_ID, model=model, prompt_version="v1.5.0",
        exploitable=exploitable, confidence=confidence,
        reasoning="write side plants, read side executes", evidence=evidence,
    )


def _voter(evidence: list[str], **kwargs):
    """A `voters._evaluate_one` stand-in that records what it was given."""
    captured: dict = {}

    def fake(voter, chain_id, system_prompt, user_prompt, temperature, max_tokens):
        captured.update(
            chain_id=chain_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            voters=voter.name,
        )
        return _voter_response(evidence, **kwargs)

    return fake, captured
def test_group_is_evaluated_and_its_1_3c_columns_survive(store) -> None:
    """A verdict lands and the prioritization columns are untouched."""
    group = _build(store)
    expected = group.to_record()
    fake, _ = _voter([E_WRITE, E_READ_1, E_READ_2])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        stats = evaluate_chain_groups(
            store, [group], _config(), progress=False,
        )

    assert stats == {
        "evaluated": 1, "validated": 1, "exploitable": 1,
        "not_exploitable": 0, "uncertain": 0, "errors": 0,
    }

    row = store.chain_groups.get(GROUP_ID)
    assert row is not None
    assert row.verdict == "exploitable"
    assert row.confidence == 0.8
    assert "write side plants" in row.reasoning
    # 1.3c's columns: the values the record carried in.
    assert row.score == expected.score
    assert row.score_components == expected.score_components
    assert row.priority == expected.priority
    assert row.name == expected.name
    assert row.write_chain_ids == expected.write_chain_ids
    assert row.read_chain_ids == expected.read_chain_ids
    assert row.untrusted_source_id == expected.untrusted_source_id
    assert row.status == "active"


def test_voters_receive_the_group_prompt_not_the_linear_one(store) -> None:
    """The system prompt is v1.5.0's, and the payload is the pair."""
    group = _build(store)
    fake, captured = _voter([E_WRITE])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    assert captured["system_prompt"] == GROUP_SYSTEM_PROMPT
    assert captured["chain_id"] == GROUP_ID
    payload = captured["user_prompt"]
    assert "[UNTRUSTED_DATA]" in payload
    assert "rag_corpus:catalogue.list" in payload
    assert group.write_chain_ids[0] in payload
    assert group.read_chain_ids[0] in payload
    # The score travels, labelled as ours.
    assert '"score": 7.0' in payload
    assert "not evidence" in payload


def test_prompt_carries_the_lethal_trifecta_attribute(store) -> None:
    """The read chain's nodes carry their 1.3d flag, absent = false."""
    group = _build(store, trifecta=True)
    fake, captured = _voter([E_READ_1])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    payload = captured["user_prompt"]
    assert f'"{READER}": true' in payload
    assert f'"{SINK}": false' in payload


def test_evidence_outside_the_group_fails_layer_3(store) -> None:
    """A cited edge that is not in the pair is a hallucination.

    `E_ELSEWHERE` exists in the graph — `validate_edges` would accept it —
    but it is not one of the group's edges, so the group is not validated.
    The verdict is still recorded: Layer 3 rejects the finding, not the
    fact that the model spoke.
    """
    group = _build(store)
    fake, _ = _voter([E_WRITE, E_ELSEWHERE])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        stats = evaluate_chain_groups(store, [group], _config(), progress=False)

    assert stats["evaluated"] == 1
    assert stats["validated"] == 0
    row = store.chain_groups.get(GROUP_ID)
    assert row.verdict == "exploitable"
    assert row.validated is False


def test_evidence_inside_the_group_validates(store) -> None:
    """Every cited edge in the pair's own set → validated."""
    group = _build(store)
    fake, _ = _voter([E_READ_1, E_READ_2])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    assert store.chain_groups.get(GROUP_ID).validated is True


def test_an_edge_missing_from_the_graph_fails_layer_3(store) -> None:
    """A group whose edges vanished (delta rescan) is rejected.

    A stale edge is invisible to `_chain_edges` too — it only walks active
    edges — so the reconstruction comes up a hop short and Layer 3 is what
    catches it.
    """
    group = _build(store)
    store._conn.execute(
        "UPDATE edges SET status = 'stale' WHERE id = :id", {"id": E_READ_2},
    )
    fake, _ = _voter([E_READ_1])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        stats = evaluate_chain_groups(store, [group], _config(), progress=False)

    assert stats["validated"] == 0
    assert store.chain_groups.get(GROUP_ID).validated is False
def test_judge_decides_when_configured(store) -> None:
    """A judge's verdict and reasoning win over the voters'.

    The judge is the linear judge, so it sees the pair concatenated at the
    anchor: the write chain into the source, the read chain out of it, and
    one shared anchor rather than two.
    """
    group = _build(store)
    fake, _ = _voter([E_WRITE])
    judge_seen: dict = {}

    def fake_judge(chain_id, chain_nodes, chain_edges, voter_responses, **kwargs):
        judge_seen.update(
            chain_id=chain_id, nodes=chain_nodes, edges=chain_edges,
            voter_reasoning=[r.reasoning for r in voter_responses],
        )
        return JudgeResponse(
            chain_id=chain_id, model="j", prompt_version="v1.5.0",
            verdict="uncertain", confidence=0.3, reasoning="insufficient",
        )

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake), \
         patch("aicorellator.analysis.judge.evaluate", side_effect=fake_judge):
        evaluate_chain_groups(
            store, [group], _config(voters=2, with_judge=True), progress=False,
        )

    row = store.chain_groups.get(GROUP_ID)
    assert row.verdict == "uncertain"
    assert row.confidence == 0.3
    assert row.reasoning == "insufficient"
    assert judge_seen["chain_id"] == GROUP_ID
    assert [node["id"] for node in judge_seen["nodes"]] == [
        ENTRY, SOURCE, READER, SINK,
    ]
    assert [edge["id"] for edge in judge_seen["edges"]] == [
        E_WRITE, E_READ_1, E_READ_2,
    ]
    assert judge_seen["voter_reasoning"] == [
        "write side plants, read side executes",
        "write side plants, read side executes",
    ]


def test_aggregation_weights_voters_without_a_judge(store) -> None:
    """Two voters, no judge: the weighted mean decides."""
    group = _build(store)
    config = _config(voters=2)
    config.voters[0].weight = 3.0
    responses = [
        _voter_response([E_WRITE], exploitable=True, confidence=0.9, model="v0"),
        _voter_response([E_WRITE], exploitable=False, confidence=0.1, model="v1"),
    ]

    with patch(
        "aicorellator.analysis.voters._evaluate_one", side_effect=responses,
    ):
        stats = evaluate_chain_groups(store, [group], config, progress=False)

    assert stats["exploitable"] == 1
    row = store.chain_groups.get(GROUP_ID)
    assert row.verdict == "exploitable"
    assert row.confidence == pytest.approx((0.9 * 3 + 0.1 * 1) / 4)


def test_a_timeout_is_uncertain_not_an_error(store) -> None:
    """Voters that never answer produce `uncertain`, as on the linear path."""
    group = _build(store)
    timeout = VoterResponse(
        chain_id=GROUP_ID, model="v0", prompt_version="v1.5.0",
        exploitable=False, confidence=0.0, reasoning="timeout", evidence=[],
        timeout=True,
    )

    with patch("aicorellator.analysis.voters._evaluate_one", return_value=timeout):
        stats = evaluate_chain_groups(store, [group], _config(), progress=False)

    assert stats["uncertain"] == 1
    assert stats["errors"] == 0
    assert store.chain_groups.get(GROUP_ID).verdict == "uncertain"


def test_errors_are_counted_not_raised(store) -> None:
    """A group that blows up is logged and skipped."""
    group = _build(store)

    with patch(
        "aicorellator.analysis.evaluator._evaluate_one_group",
        side_effect=RuntimeError("boom"),
    ):
        stats = evaluate_chain_groups(store, [group], _config(), progress=False)

    assert stats["evaluated"] == 0
    assert stats["errors"] == 1
    assert store.chain_groups.count() == 0


def test_an_unscored_group_is_still_evaluated(store) -> None:
    """Scoring is 1.3c's; a verdict must not depend on it."""
    group = _build(store)
    group.score = 0.0
    group.score_components = {}
    group.priority = None
    fake, _ = _voter([E_WRITE])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        stats = evaluate_chain_groups(store, [group], _config(), progress=False)

    assert stats["evaluated"] == 1
    row = store.chain_groups.get(GROUP_ID)
    assert row.verdict == "exploitable"
    assert row.score == 0.0
    assert row.priority is None