"""Chain-group evaluation — the 1.3e path with mocked voters.

The group path shares the ensemble with the linear one but not the
payload, the prompt or the table. These tests pin what is *its*: the
write/read pair reaches the model as a pair, the score travels beside it
without deciding the verdict, Layer 3 checks the group's own edge set,
and the row that lands keeps the columns 1.3c paid for.
"""

from __future__ import annotations

import json
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
from aicorellator.analysis.prompts import GROUP_SYSTEM_PROMPT, PROMPT_VERSION
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


def test_the_group_row_projects_the_read_sides_trifecta(store) -> None:
    """B.6: the group column is 1.3d's node flag, read off the read side.

    The fixture's reader carries `lethal_trifecta: True` and the other
    nodes of the read chain do not, so the projection has something to
    say. It says it on the *row*: the payload's per-node map is the same
    read, and `test_prompt_carries_the_lethal_trifecta_attribute` pins
    that half of it. The column was written by `add` — this row did not
    exist before the evaluation — so the value is the evaluator's, not a
    default that happened to be false.
    """
    group = _build(store, trifecta=True)
    fake, _ = _voter([E_READ_1, E_READ_2])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    row = store.chain_groups.get(GROUP_ID)
    assert row.verdict == "exploitable"
    assert row.lethal_trifecta is True


def test_a_trifecta_on_the_write_side_is_not_the_groups(store) -> None:
    """The write side is the delivery, not the impact (ADR-0009).

    `entry` is on the write chain only — `entry → source`, while the read
    chain is `source → reader → sink` — so a trifecta flag there says
    nothing about the group: the attacker's content enters through the
    read, and a trifecta node upstream of the write is another chain's
    problem. That is the reasoning `graph/query.py::_score_lethal_trifecta`
    uses, and the projection follows it rather than reading both sides.
    """
    group = _build(store)
    entry = store.nodes.get(ENTRY)
    entry.attributes["lethal_trifecta"] = True
    store.nodes.upsert(entry)
    fake, _ = _voter([E_READ_1, E_READ_2])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    row = store.chain_groups.get(GROUP_ID)
    assert row.verdict == "exploitable"
    assert row.lethal_trifecta is False


def test_the_row_carries_the_prompt_version_and_the_voter_outputs(
    store,
) -> None:
    """B.7: a group verdict is traced by the same two facts a chain's is.

    `prompt_version` is the prompt the voters and the judge ran under and
    `voter_outputs` is what they answered. The linear path has written
    both to `chains` since Phase 0; the group path writes the same shape
    through the same `_voter_response_dict`, so a consumer reading the
    two tables sees one shape rather than two.
    """
    group = _build(store)
    fake, _ = _voter([E_READ_1, E_READ_2])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    row = store.chain_groups.get(GROUP_ID)
    assert row.prompt_version == PROMPT_VERSION
    assert len(row.voter_outputs) == 1
    output = row.voter_outputs[0]
    assert output["model"] == "v0"
    assert output["confidence"] == 0.8
    assert output["evidence"] == [E_READ_1, E_READ_2]
    # The set a citation is judged against is the group's own — the same
    # set Layer 3 uses — so `evidence_valid` here answers the group's
    # question, not the graph's.
    assert output["evidence_valid"] is True
    assert output["evidence_unknown"] == []
    # The provider's raw response is not provenance the row needs (the
    # same strip the linear path's rows get).
    assert "raw" not in output


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
    # B9G: B.4b's label for a group that its own check rejected.
    assert row.validated_kind == "existence"
    # B.7: the same judgement is recorded on the provenance row, so a
    # reader of `voter_outputs` can see *which* citation was outside the
    # pair without re-deriving the group's edge set.
    output = row.voter_outputs[0]
    assert output["evidence_valid"] is False
    assert output["evidence_unknown"] == [E_ELSEWHERE]


def test_evidence_inside_the_group_validates(store) -> None:
    """Every cited edge in the pair's own set → validated."""
    group = _build(store)
    fake, _ = _voter([E_READ_1, E_READ_2])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    row = store.chain_groups.get(GROUP_ID)
    assert row.validated is True
    # B9G: the group path is B.4b's and did not move. It writes the two
    # A.4 labels — "semantic" for its own check passing — and never
    # "evidence", which B.9 adds to the linear chain path only.
    assert row.validated_kind == "semantic"


def test_a_group_no_voter_argued_still_validates(store) -> None:
    """B9G — the group's rule, spelled the other way round.

    B.9 does not reach this path: a group whose voters cite nothing is
    validated when its edges exist, its hops resolve and no citation
    left the pair (B.4b's check). The same empty evidence on the linear
    chain path reads `"semantic"` and not validated; the two tables
    disagree on purpose, and this pins that they still do.
    """
    group = _build(store)
    fake, _ = _voter([])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    row = store.chain_groups.get(GROUP_ID)
    assert row.validated is True
    assert row.validated_kind == "semantic"


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


# ------------------------------------------------------------
# Guardrails reach the pair's payload (B.4b, ADR-0019)
# ------------------------------------------------------------

GUARDRAIL_W = "guardrail:" + "1" * 16
GUARDRAIL_R = "guardrail:" + "2" * 16
GUARDRAIL_A = "guardrail:" + "3" * 16


def _guard(
    store: GraphStore, guardrail_id: str, target_id: str, name: str,
) -> None:
    """A guardrail node and its `guards` edge onto one component.

    `guardrail → component` (ADR-0019 §1) and `structural` (ADR-0015,
    decision 5), so the edge is in the graph and in neither chain — the
    section can only come from a graph lookup.
    """
    store.nodes.upsert(Node(
        id=guardrail_id, kind="guardrail", name=name, host_id="localhost",
        attributes={"source_file": "app/guardrails.py"},
    ))
    store.edges.upsert(Edge(
        id="edge:" + guardrail_id[-16:], source_id=guardrail_id,
        target_id=target_id, kind="guards", host_id="localhost",
        flow_kind="structural",
    ))


def _target_payload(prompt: str) -> dict:
    """The group prompt's [UNTRUSTED_DATA] block, parsed back."""
    block = prompt.split("[UNTRUSTED_DATA]", 1)[1]
    return json.loads(block.split("[/UNTRUSTED_DATA]", 1)[0])


def test_guardrails_are_split_by_side(store) -> None:
    """A guardrail on one side stays on that side (ADR-0019 §3).

    The sides are weighed independently, so a guardrail on the write
    chain must not lower confidence in the read side's sink. The write
    chain is `entry → source`, so the guardrail goes on the entry point;
    the read chain's guardrail goes on the reader.
    """
    group = _build(store)
    _guard(store, GUARDRAIL_W, ENTRY, "InputSanitizer")
    fake, captured = _voter([E_WRITE])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    guardrails = _target_payload(captured["user_prompt"])["guardrails"]
    assert list(guardrails) == ["write", "read", "anchor"]
    assert [g["name"] for g in guardrails["write"]] == ["InputSanitizer"]
    assert guardrails["read"] == []
    assert guardrails["anchor"] == []

    # Both sides populated: each guardrail stays on its own side.
    _guard(store, GUARDRAIL_R, READER, "OutputFilter")
    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    guardrails = _target_payload(captured["user_prompt"])["guardrails"]
    assert [g["name"] for g in guardrails["write"]] == ["InputSanitizer"]
    assert [g["name"] for g in guardrails["read"]] == ["OutputFilter"]
    assert guardrails["anchor"] == []


def test_a_guardrail_on_the_anchor_is_its_own_side(store) -> None:
    """The channel's guardrail is neither the write nor the read side.

    The anchor is a node of both chains, so the three lists are a
    *partition*: without that, a guardrail on the channel would appear on
    both sides and the model could not tell "on the channel" from "on
    both sides".
    """
    group = _build(store)
    _guard(store, GUARDRAIL_A, SOURCE, "ChannelFilter")
    fake, captured = _voter([E_WRITE])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    guardrails = _target_payload(captured["user_prompt"])["guardrails"]
    assert [g["name"] for g in guardrails["anchor"]] == ["ChannelFilter"]
    assert guardrails["write"] == []
    assert guardrails["read"] == []


def test_a_group_without_guardrails_carries_three_empty_lists(store) -> None:
    """No guardrail is the default state, and the shape is still there.

    `[]`, not a missing key: the model must not have to tell "nothing
    guards either side" from "the prompt did not ask" (ADR-0019 §2).
    """
    group = _build(store)
    fake, captured = _voter([E_WRITE])

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake):
        evaluate_chain_groups(store, [group], _config(), progress=False)

    assert _target_payload(captured["user_prompt"])["guardrails"] == {
        "write": [], "read": [], "anchor": [],
    }


def test_the_group_judge_receives_the_flat_guardrail_list(store) -> None:
    """The judge is the linear one, so it gets the flat list (ADR-0019 §3).

    It weighs the voters' guardrail reasoning against the same evidence
    they reasoned from; the per-side split belongs to the group prompt,
    not to the judge.
    """
    group = _build(store)
    _guard(store, GUARDRAIL_W, ENTRY, "InputSanitizer")
    _guard(store, GUARDRAIL_R, READER, "OutputFilter")
    fake, _ = _voter([E_WRITE])
    judge_seen: dict = {}

    def fake_judge(chain_id, chain_nodes, chain_edges, voter_responses, **kwargs):
        judge_seen.update(kwargs)
        return JudgeResponse(
            chain_id=chain_id, model="j", prompt_version="v1.6.0",
            verdict="uncertain", confidence=0.3, reasoning="insufficient",
        )

    with patch("aicorellator.analysis.voters._evaluate_one", side_effect=fake), \
         patch("aicorellator.analysis.judge.evaluate", side_effect=fake_judge):
        evaluate_chain_groups(
            store, [group], _config(voters=1, with_judge=True), progress=False,
        )

    assert [g["name"] for g in judge_seen["guardrails"]] == [
        "InputSanitizer", "OutputFilter",
    ]