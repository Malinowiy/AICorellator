"""Unit tests for the evaluator — mocked voters and judge."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aicorellator.analysis.client import LLMResponse
from aicorellator.analysis.config_loader import EnsembleConfig, load_config
from aicorellator.analysis.evaluator import (
    _edge_to_dict,
    _guardrails_for_chain,
    _node_to_dict,
    evaluate_chains,
)
from aicorellator.analysis.models import (
    JudgeResponse,
    VoterConfig,
    VoterResponse,
)
from aicorellator.analysis.prompts import build_voter_prompt
from aicorellator.graph import Edge, Node
from aicorellator.graph.query import CandidateChain
from aicorellator.scan_config import write_default_config


def _candidate(chain_id: str = "chain:test") -> CandidateChain:
    return CandidateChain(
        chain_id=chain_id,
        nodes=["n:1", "n:2"],
        edges=["e:1"],
        length=1,
    )


def _config(voters_count: int = 1, with_judge: bool = False) -> EnsembleConfig:
    voters = [
        VoterConfig(name=f"v{i}", base_url="x", role="voter")
        for i in range(voters_count)
    ]
    judge = None
    if with_judge:
        judge = VoterConfig(name="j", base_url="x", role="judge")
    return EnsembleConfig(
        voters=voters,
        judge=judge,
        mode="full" if with_judge else "fast",
    )


def _voter_response(
    model: str,
    exploitable: bool = True,
    confidence: float = 0.8,
    evidence: list[str] | None = None,
):
    return VoterResponse(
        chain_id="chain:test", model=model, prompt_version="v1.1.0",
        exploitable=exploitable, confidence=confidence,
        reasoning="r", evidence=list(evidence or []),
    )


def test_evaluate_single_chain_fast(store) -> None:
    candidates = [_candidate()]
    config = _config(voters_count=1, with_judge=False)

    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=None,  # timeout
    ):
        stats = evaluate_chains(
            store, candidates, config, progress=False,
        )

    assert stats["evaluated"] == 1
    assert stats["errors"] == 0
    # All voters timed out → uncertain
    assert stats["uncertain"] == 1


def test_evaluate_chains_persisted(store) -> None:
    """Evaluated chains are written to the store."""
    candidates = [_candidate()]
    config = _config(voters_count=1)

    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=None,
    ):
        evaluate_chains(store, candidates, config, progress=False)

    assert store.chains.count() == 1
    chain = store.chains.get("chain:test")
    assert chain is not None


def test_evaluate_multiple_chains(store) -> None:
    candidates = [_candidate("chain:a"), _candidate("chain:b")]
    config = _config(voters_count=1)

    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=None,
    ):
        stats = evaluate_chains(
            store, candidates, config, progress=False,
        )

    assert stats["evaluated"] == 2
    assert store.chains.count() == 2


def test_evaluate_errors_counted(store) -> None:
    candidates = [_candidate()]
    config = _config(voters_count=1)

    with patch(
        "aicorellator.analysis.voters.evaluate",
        side_effect=RuntimeError("boom"),
    ):
        stats = evaluate_chains(
            store, candidates, config, progress=False,
        )

    assert stats["evaluated"] == 0
    assert stats["errors"] == 1


def test_node_to_dict_includes_capabilities(store) -> None:
    """Node capabilities reach the prompt payload (ADR-0009)."""
    store.nodes.upsert(Node(
        id="tool:1", kind="tool", name="execute_python_script",
        host_id="localhost", capabilities=["executes_code"],
    ))
    d = _node_to_dict(store, "tool:1")
    assert d["capabilities"] == ["executes_code"]
    assert "confidence" in d


def test_node_to_dict_includes_flow_attributes(store) -> None:
    """The 1.2a.1 node signals reach the prompt payload.

    `_node_to_dict` serializes `attributes` wholesale, so a tool's
    `returns_data` and a sink's `sink_data_flow` travel with the node.
    """
    store.nodes.upsert(Node(
        id="tool:1", kind="tool", name="search_files", host_id="localhost",
        attributes={"source_file": "mcp/servers/findrive/server.py",
                    "returns_data": True},
    ))
    store.nodes.upsert(Node(
        id="sink:1", kind="sink", name="delete_file", host_id="localhost",
        attributes={"source_file": "mcp/servers/findrive/server.py",
                    "sink_data_flow": "parameter_match"},
    ))

    tool = _node_to_dict(store, "tool:1")
    assert tool["attributes"]["returns_data"] is True

    sink = _node_to_dict(store, "sink:1")
    assert sink["attributes"]["sink_data_flow"] == "parameter_match"



def test_edge_to_dict_includes_capabilities_and_precision(store) -> None:
    """Edge capabilities and precision reach the prompt payload."""
    store.nodes.upsert(Node(id="n:1", kind="tool", name="t", host_id="localhost"))
    store.nodes.upsert(Node(id="n:2", kind="sink", name="s", host_id="localhost"))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2", kind="has_access_to",
        host_id="localhost", capabilities=["control_flows_forward"],
        precision="syntactic",
    ))
    d = _edge_to_dict(store, "e:1")
    assert d["capabilities"] == ["control_flows_forward"]
    assert d["precision"] == "syntactic"


def test_edge_to_dict_includes_flow_kind(store) -> None:
    """Every serialized edge carries flow_kind (ADR-0015).

    flow_kind is NOT NULL in the schema, so an edge without one is a
    construction bug — the LLM must always see it. data_flow_confirmed
    is emitted only when present.
    """
    store.nodes.upsert(Node(
        id="n:1", kind="mcp_server", name="findrive", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="n:2", kind="tool", name="delete_file", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2", kind="provides_tool",
        host_id="localhost",
    ))
    d = _edge_to_dict(store, "e:1")
    assert d["flow_kind"] == "control"       # default: provides_tool is control
    assert "data_flow_confirmed" not in d    # only has_access_to carries it


def test_edge_to_dict_round_trips_confirmed_data_flow(store) -> None:
    """A confirmed data edge keeps flow_kind == "data" and the method."""
    store.nodes.upsert(Node(
        id="n:1", kind="tool", name="delete_file", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="n:2", kind="sink", name="filesystem", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2", kind="has_access_to",
        host_id="localhost", flow_kind="data",
        data_flow_confirmed="parameter_match",
    ))
    d = _edge_to_dict(store, "e:1")
    assert d["flow_kind"] == "data"
    assert d["data_flow_confirmed"] == "parameter_match"


def test_evaluator_flags_unknown_evidence(store) -> None:
    """Evidence edges not present in the chain are recorded as unknown."""
    candidates = [_candidate()]
    config = _config(voters_count=1)
    text = json.dumps({
        "chain_id": "chain:test",
        "exploitable": True,
        "confidence": 0.9,
        "reasoning": "r",
        "evidence": ["e:1", "edge:ghost"],
    })

    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=LLMResponse(text=text, model="test"),
    ):
        evaluate_chains(store, candidates, config, progress=False)

    chain = store.chains.get("chain:test")
    assert chain is not None
    vo = chain.voter_outputs[0]
    assert vo["evidence_valid"] is False
    assert vo["evidence_unknown"] == ["edge:ghost"]


def test_evaluator_accepts_known_evidence(store) -> None:
    """Evidence edges within the chain are marked valid."""
    candidates = [_candidate()]
    config = _config(voters_count=1)
    text = json.dumps({
        "chain_id": "chain:test",
        "exploitable": True,
        "confidence": 0.9,
        "reasoning": "r",
        "evidence": ["e:1"],
    })

    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=LLMResponse(text=text, model="test"),
    ):
        evaluate_chains(store, candidates, config, progress=False)

    chain = store.chains.get("chain:test")
    assert chain is not None
    vo = chain.voter_outputs[0]
    assert vo["evidence_valid"] is True
    assert vo["evidence_unknown"] == []


# ------------------------------------------------------------
# Payload the LLM actually receives
# ------------------------------------------------------------

def _build_finbot_chain(store) -> dict[str, str]:
    """Build a FinBot-shaped chain and return its ids.

    Mirrors the shape and names of the real FinBot target:

        POST /invoice --uses_model(control)--> InvoiceAgent
                      --delegates_to(control)--> OrchestratorAgent
                      --uses_tool(control)--> findrive MCP server
                      --provides_tool(control)--> delete_file tool
                      --has_access_to(data)--> filesystem sink

    The last hop is what the Flow Analyzer emits for a tool whose sink
    argument is one of its own parameters: flow_kind="data" with
    data_flow_confirmed="parameter_match".
    """
    ids = {
        "entry": "entry_point:" + "e" * 16,
        "agent": "agent:" + "a" * 16,
        "orch": "agent:" + "b" * 16,
        "mcp": "mcp_server:" + "c" * 16,
        "tool": "tool:" + "d" * 16,
        "sink": "sink:" + "f" * 16,
        "e1": "edge:" + "1" * 16,
        "e2": "edge:" + "2" * 16,
        "e3": "edge:" + "3" * 16,
        "e4": "edge:" + "4" * 16,
        "e5": "edge:" + "5" * 16,
    }
    store.nodes.upsert(Node(
        id=ids["entry"], kind="entry_point", name="POST /invoice",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=ids["agent"], kind="agent", name="InvoiceAgent",
        host_id="localhost", capabilities=["uses_llm"],
    ))
    store.nodes.upsert(Node(
        id=ids["orch"], kind="agent", name="OrchestratorAgent",
        host_id="localhost", capabilities=["uses_llm"],
    ))
    store.nodes.upsert(Node(
        id=ids["mcp"], kind="mcp_server", name="findrive",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=ids["tool"], kind="tool", name="delete_file",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=ids["sink"], kind="sink", name="filesystem",
        host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id=ids["e1"], source_id=ids["entry"], target_id=ids["agent"],
        kind="uses_model", host_id="localhost", flow_kind="control",
        capabilities=["control_flows_forward"],
    ))
    store.edges.upsert(Edge(
        id=ids["e2"], source_id=ids["agent"], target_id=ids["orch"],
        kind="delegates_to", host_id="localhost", flow_kind="control",
        capabilities=["control_flows_forward"],
    ))
    store.edges.upsert(Edge(
        id=ids["e3"], source_id=ids["orch"], target_id=ids["mcp"],
        kind="uses_tool", host_id="localhost", flow_kind="control",
        capabilities=["control_flows_forward"],
    ))
    store.edges.upsert(Edge(
        id=ids["e4"], source_id=ids["mcp"], target_id=ids["tool"],
        kind="provides_tool", host_id="localhost", flow_kind="control",
    ))
    store.edges.upsert(Edge(
        id=ids["e5"], source_id=ids["tool"], target_id=ids["sink"],
        kind="has_access_to", host_id="localhost", flow_kind="data",
        data_flow_confirmed="parameter_match",
        capabilities=["data_flows_forward", "untrusted_propagates"],
    ))
    return ids



def test_voter_payload_carries_flow_kind(store) -> None:
    """A FinBot-shaped chain reaches the LLM with flow semantics intact.

    This is the observable contract of Step 5: every serialized edge
    carries flow_kind, the confirmed data hop carries
    data_flow_confirmed, and provides_tool is control end to end.
    """
    ids = _build_finbot_chain(store)
    candidates = store.query.find_chains()
    # The tool carries no sink capability, so the single endpoint is the
    # filesystem sink and the chain includes the confirmed data hop.
    assert len(candidates) == 1

    captured: dict = {}

    def _fake_evaluate(**kwargs):
        captured.update(kwargs)
        return [_voter_response("fake-voter")]

    with patch(
        "aicorellator.analysis.voters.evaluate",
        side_effect=_fake_evaluate,
    ):
        stats = evaluate_chains(store, candidates, _config(), progress=False)

    assert stats["evaluated"] == 1

    edges = captured["chain_edges"]
    assert [e["id"] for e in edges] == [ids[f"e{i}"] for i in range(1, 6)]
    assert all("flow_kind" in e for e in edges)

    by_kind = {e["kind"]: e for e in edges}
    assert by_kind["provides_tool"]["flow_kind"] == "control"
    assert "data_flow_confirmed" not in by_kind["provides_tool"]
    assert by_kind["has_access_to"]["flow_kind"] == "data"
    assert by_kind["has_access_to"]["data_flow_confirmed"] == "parameter_match"

    # Control edges still expose capabilities and precision (P0/P2).
    assert by_kind["uses_model"]["capabilities"] == ["control_flows_forward"]
    assert by_kind["uses_model"]["precision"] == "syntactic"


# ------------------------------------------------------------
# Guardrails reach the payload (B.4b, ADR-0019)
# ------------------------------------------------------------

GUARDRAIL_ID = "guardrail:" + "9" * 16
GUARDS_EDGE = "edge:" + "9" * 16


def _build_unguarded_chain(store) -> None:
    """`n:1 → n:2`, the chain the guardrail tests start from."""
    store.nodes.upsert(Node(
        id="n:1", kind="entry_point", name="POST /chat", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="n:2", kind="sink", name="filesystem", host_id="localhost",
        capabilities=["writes_filesystem"],
    ))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2", kind="has_access_to",
        host_id="localhost", flow_kind="data",
        data_flow_confirmed="parameter_match",
    ))


def _add_guardrail(store) -> None:
    """A `guardrail` node and its `guards` edge onto `n:2`.

    `guardrail → component` (ADR-0019 §1) and `structural` (ADR-0015,
    decision 5), so the edge is in the graph and in no chain.
    """
    store.nodes.upsert(Node(
        id=GUARDRAIL_ID, kind="guardrail", name="PIIDetector",
        host_id="localhost", attributes={"source_file": "app/guardrails.py"},
    ))
    store.edges.upsert(Edge(
        id=GUARDS_EDGE, source_id=GUARDRAIL_ID, target_id="n:2",
        kind="guards", host_id="localhost", flow_kind="structural",
    ))


def _build_chain_with_guardrail(store) -> None:
    """`n:1 → n:2`, plus a guardrail guarding `n:2`.

    The `guards` edge is `structural` (ADR-0015, decision 5), so it is in
    the graph and in no chain — which is exactly the case the evaluator
    has to read back for B.4b: the guardrail cannot arrive as a chain
    edge, only as a graph lookup.
    """
    _build_unguarded_chain(store)
    _add_guardrail(store)


def test_voter_payload_carries_the_guardrails_section(store) -> None:
    """A guarded chain reaches the voters with the guardrail named.

    The section is the ADR's "what the graph knows, no more" (ADR-0019
    §2): the node's id, kind, name, capabilities and attributes — no
    synthesized `policy` or `enforcement_point`, which is D.6.
    """
    _build_chain_with_guardrail(store)
    captured: dict = {}

    def _fake_evaluate(**kwargs):
        captured.update(kwargs)
        return [_voter_response("fake-voter")]

    with patch(
        "aicorellator.analysis.voters.evaluate", side_effect=_fake_evaluate,
    ):
        stats = evaluate_chains(
            store, [_candidate()], _config(), progress=False,
        )

    assert stats["evaluated"] == 1
    guardrails = captured["guardrails"]
    assert [g["id"] for g in guardrails] == [GUARDRAIL_ID]
    assert guardrails[0]["kind"] == "guardrail"
    assert guardrails[0]["name"] == "PIIDetector"
    assert guardrails[0]["capabilities"] == []
    assert guardrails[0]["attributes"] == {"source_file": "app/guardrails.py"}
    # The chain itself is unchanged: the `guards` edge is not a chain edge.
    assert [e["id"] for e in captured["chain_edges"]] == ["e:1"]


def test_the_prompt_carries_guardrails_in_the_payload(store) -> None:
    """The section travels the whole path: evaluator → voters → prompt."""
    _build_chain_with_guardrail(store)
    seen: dict = {}

    def _fake_complete(base_url, request, api_key=None):
        seen["user"] = request.user
        return LLMResponse(
            text=json.dumps({
                "chain_id": "chain:test", "exploitable": False,
                "confidence": 0.1, "reasoning": "a guardrail is present",
                "evidence": [],
            }),
            model="test",
        )

    with patch(
        "aicorellator.analysis.voters.complete", side_effect=_fake_complete,
    ):
        evaluate_chains(store, [_candidate()], _config(), progress=False)

    untrusted = seen["user"].partition("[/UNTRUSTED_DATA]")[0]
    assert '"guardrails"' in untrusted
    assert GUARDRAIL_ID in untrusted
    assert '"PIIDetector"' in untrusted


def test_a_chain_without_guardrails_sends_an_empty_list(store) -> None:
    """No guardrail near the chain is `[]`, never a missing key.

    The absence of a guardrail is the default state of every path
    (ADR-0019 §2), and the prompt says so rather than leaving the model
    to wonder whether the question was asked.
    """
    store.nodes.upsert(Node(
        id="n:1", kind="entry_point", name="POST /chat", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="n:2", kind="sink", name="filesystem", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2", kind="has_access_to",
        host_id="localhost", flow_kind="control",
    ))
    captured: dict = {}

    def _fake_evaluate(**kwargs):
        captured.update(kwargs)
        return [_voter_response("fake-voter")]

    with patch(
        "aicorellator.analysis.voters.evaluate", side_effect=_fake_evaluate,
    ):
        evaluate_chains(store, [_candidate()], _config(), progress=False)

    assert captured["guardrails"] == []
    prompt = build_voter_prompt(
        "chain:test", captured["chain_nodes"], captured["chain_edges"],
        captured["guardrails"],
    )
    assert '"guardrails": []' in prompt


def test_guardrails_come_from_the_guards_edge_direction(store) -> None:
    """Only `guardrail → component` counts, and only from a guardrail.

    The direction is the ADR's (ADR-0019 §1): the guardrail is the actor,
    the component the object. A `guards` edge whose source is not a
    guardrail node is a construction bug, and it is left out rather than
    reported to the model as a guardrail the graph does not have.
    """
    store.nodes.upsert(Node(
        id="tool:1", kind="tool", name="delete_file", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=GUARDRAIL_ID, kind="guardrail", name="PIIDetector",
        host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id=GUARDS_EDGE, source_id=GUARDRAIL_ID, target_id="tool:1",
        kind="guards", host_id="localhost", flow_kind="structural",
    ))

    assert [g["id"] for g in _guardrails_for_chain(store, ["tool:1"])] == [
        GUARDRAIL_ID,
    ]
    # The reverse reading — "this component is guarded by" — is not the
    # claim the edge makes, so a guardrail is not found by its own id.
    assert _guardrails_for_chain(store, [GUARDRAIL_ID]) == []

    # ... and a `guards` edge out of a tool is not a guardrail either.
    store.edges.upsert(Edge(
        id="edge:" + "8" * 16, source_id="tool:1", target_id=GUARDRAIL_ID,
        kind="guards", host_id="localhost", flow_kind="structural",
    ))
    assert _guardrails_for_chain(store, [GUARDRAIL_ID]) == []


def test_the_guardrails_helper_is_total(store) -> None:
    """No nodes, or an unknown node id, is simply no guardrail."""
    assert _guardrails_for_chain(store, []) == []
    assert _guardrails_for_chain(store, ["n:missing"]) == []


def test_a_guards_edge_does_not_change_enumeration(store) -> None:
    """B.4b moves the payload, not the graph (ADR-0019, §2).

    The corpus expectation, on a fixture: a guardrail node and its
    `guards` edge leave the chain set exactly where it was — `structural`
    edges are not traversed (ADR-0015, decision 5) — and the guardrail is
    neither a chain node nor a chain edge. Only the payload changes.
    """
    _build_unguarded_chain(store)
    before = [c.chain_id for c in store.query.find_chains()]
    assert before, "the fixture must enumerate at least one chain"
    assert _guardrails_for_chain(store, ["n:1", "n:2"]) == []

    _add_guardrail(store)

    after = store.query.find_chains()
    assert [c.chain_id for c in after] == before
    assert len(after) == len(before)
    assert GUARDRAIL_ID not in after[0].nodes
    assert GUARDS_EDGE not in after[0].edges
    # The payload is the only thing that moved.
    assert [g["id"] for g in _guardrails_for_chain(store, ["n:1", "n:2"])] == [
        GUARDRAIL_ID,
    ]


# ------------------------------------------------------------
# Layer 3's third level — evidence presence (B.9, ADR-0018 rev. 2)
# ------------------------------------------------------------
#
# `validated` is existence + coherence + evidence: the chain has to be
# real, it has to be a chain, and a voter has to have cited one of *its*
# edges. The first two are `validate_chain`'s (A.4) and are tested in
# tests/test_semantic_validation/; what is tested here is the third, and
# the order the three are asked in.

def _evidence_chain(store) -> None:
    """`n:1 --e:1--> n:2`, the chain `_candidate()` names.

    Typed at both ends, one hop, no anchor: `validate_chain` returns
    `(True, "semantic", [])` for it, which is the state B.9's check
    starts from. The ids are `_candidate()`'s, so a test can hand the
    evaluator the candidate and read the row back by one id.
    """
    store.nodes.upsert(Node(
        id="n:1", kind="entry_point", name="POST /chat", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="n:2", kind="sink", name="filesystem", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:1", target_id="n:2", kind="has_access_to",
        host_id="localhost", flow_kind="control",
    ))


def _evaluate(store, candidates, responses) -> None:
    """Run the evaluator with the voters mocked to `responses`."""
    with patch(
        "aicorellator.analysis.voters.evaluate", return_value=responses,
    ):
        evaluate_chains(store, candidates, _config(), progress=False)


def test_a_citation_from_the_chain_is_the_evidence_level(store) -> None:
    """B9A — one voter, one edge of the chain: `"evidence"` and validated."""
    _evidence_chain(store)
    _evaluate(store, [_candidate()], [_voter_response("v1", evidence=["e:1"])])

    chain = store.chains.get("chain:test")
    assert chain.validated is True
    assert chain.validated_kind == "evidence"
    assert chain.validation_notes == []


def test_a_voter_that_cites_nothing_leaves_the_chain_semantic(store) -> None:
    """B9B — the case B.9 exists for: `exploitable` with no evidence.

    The chain is coherent, so the level it reached is `"semantic"`; the
    vote is not a validated one, and the note says why. This is the
    assertion that used to read `validated: true, validated_kind:
    "semantic"`.
    """
    _evidence_chain(store)
    _evaluate(store, [_candidate()], [_voter_response("v1")])

    chain = store.chains.get("chain:test")
    assert chain.validated is False
    assert chain.validated_kind == "semantic"
    assert chain.validation_notes == ["no voter cited an edge from the chain"]


def test_citations_from_outside_the_chain_name_the_count(store) -> None:
    """B9C — an edge that exists but is not this chain's is not evidence.

    A hallucinated citation is the loudest way to cite nothing, so the
    note carries how many there were: two citations, none of them the
    chain's, reads as two.
    """
    _evidence_chain(store)
    _evaluate(store, [_candidate()], [
        _voter_response("v1", evidence=["edge:ghost", "edge:phantom"]),
    ])

    chain = store.chains.get("chain:test")
    assert chain.validated is False
    assert chain.validated_kind == "semantic"
    assert chain.validation_notes == [
        "no voter cited an edge from the chain "
        "(2 citation(s) outside the chain)"
    ]


def test_no_voter_responded_is_its_own_note(store) -> None:
    """B9D — all timeouts, then no voters at all: both are "no evidence".

    The two are the same answer to the evidence question and different
    facts about the run, so the note names the fact. Neither is a chain
    to report.
    """
    _evidence_chain(store)
    timed_out = VoterResponse(
        chain_id="chain:test", model="v1", prompt_version="v1.1.0",
        exploitable=False, confidence=0.0, reasoning="timeout",
        evidence=[], timeout=True,
    )
    _evaluate(store, [_candidate()], [timed_out])

    chain = store.chains.get("chain:test")
    assert chain.validated is False
    assert chain.validated_kind == "semantic"
    assert chain.validation_notes == [
        "no voter cited an edge from the chain (no voters responded)"
    ]

    _evaluate(store, [_candidate()], [])
    assert store.chains.get("chain:test").validation_notes == [
        "no voter cited an edge from the chain (no voters responded)"
    ]


def test_one_voter_citing_the_chain_is_enough(store) -> None:
    """The criterion is "at least one voter, at least one edge".

    A second voter that cites a ghost does not undo the first voter's
    citation: the chain was argued, and the per-voter record of the bad
    citation is in `voter_outputs`, where it belongs.
    """
    _evidence_chain(store)
    _evaluate(store, [_candidate()], [
        _voter_response("v1", evidence=["e:1"]),
        _voter_response("v2", evidence=["edge:ghost"]),
    ])

    chain = store.chains.get("chain:test")
    assert chain.validated is True
    assert chain.validated_kind == "evidence"
    assert chain.validation_notes == []
    assert chain.voter_outputs[1]["evidence_unknown"] == ["edge:ghost"]


def test_a_missing_edge_short_circuits_the_evidence_check(store) -> None:
    """B9E — existence fails first, and the citation cannot rescue it.

    The voter cites `e:1`, which is in the chain: were the checks asked
    in the other order the chain would read `"evidence"`. It reads
    `"existence"` — the level it actually reached — with A.4's note.
    """
    _evidence_chain(store)
    candidate = CandidateChain(
        chain_id="chain:test", nodes=["n:1", "n:2"],
        edges=["e:1", "edge:ghost"], length=2,
    )
    _evaluate(store, [candidate], [_voter_response("v1", evidence=["e:1"])])

    chain = store.chains.get("chain:test")
    assert chain.validated is False
    assert chain.validated_kind == "existence"
    assert chain.validation_notes == ["missing edge edge:ghost"]


def test_a_broken_hop_short_circuits_the_evidence_check(store) -> None:
    """B9F — coherence fails first, for the same reason.

    Existence holds (both nodes and the edge are in the graph) and the
    hop runs the wrong way, so A.4 rejects the chain and its note is the
    one stored. The citation is not an answer to that question.
    """
    store.nodes.upsert(Node(
        id="n:1", kind="entry_point", name="POST /chat", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="n:2", kind="sink", name="filesystem", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id="e:1", source_id="n:2", target_id="n:1", kind="has_access_to",
        host_id="localhost", flow_kind="control",
    ))
    _evaluate(store, [_candidate()], [_voter_response("v1", evidence=["e:1"])])

    chain = store.chains.get("chain:test")
    assert chain.validated is False
    assert chain.validated_kind == "existence"
    assert chain.validation_notes == [
        "disconnected: edge e:1 links n:2 → n:1, expected n:1 → n:2"
    ]


# ------------------------------------------------------------
# Determinism, with the LLM pinned (B.5)
# ------------------------------------------------------------

def test_two_runs_over_one_graph_store_the_same_verdict(
    store, db_path: Path
) -> None:
    """B5A + D1 — the judge is deterministic by default, so a re-run agrees.

    The mock pins the model — it answers the same text both times — so
    what this pins is everything downstream of the model: the config's
    temperature reaching `judge.evaluate` as 0.0 (B.5), the aggregation,
    and the three Layer 3 levels. A template loaded here is the real
    path: `load_config` on the file `ensure()` writes.
    """
    path = db_path.parent / "config" / "config.yaml"
    write_default_config(path)
    config = load_config(path, mode="custom")

    _evidence_chain(store)
    judge_text = json.dumps({
        "chain_id": "chain:test", "verdict": "exploitable",
        "confidence": 0.75, "reasoning": "the chain reaches the sink",
    })
    voter_text = json.dumps({
        "chain_id": "chain:test", "exploitable": True, "confidence": 0.9,
        "reasoning": "r", "evidence": ["e:1"],
    })
    temperatures: list[float] = []

    def _fake_judge(base_url, request, api_key=None):
        temperatures.append(request.temperature)
        return LLMResponse(text=judge_text, model="test")

    with patch(
        "aicorellator.analysis.voters.complete",
        return_value=LLMResponse(text=voter_text, model="test"),
    ), patch(
        "aicorellator.analysis.judge.complete", side_effect=_fake_judge,
    ):
        evaluate_chains(store, [_candidate()], config, progress=False)
        first = store.chains.get("chain:test")
        evaluate_chains(store, [_candidate()], config, progress=False)
        second = store.chains.get("chain:test")

    assert first.verdict == second.verdict == "exploitable"
    assert first.confidence == second.confidence == 0.75
    assert first.validated_kind == second.validated_kind == "evidence"
    assert first.validated is second.validated is True
    assert first.validation_notes == second.validation_notes == []
    # ... and the judge was handed the deterministic default both times.
    assert temperatures == [0.0, 0.0]
