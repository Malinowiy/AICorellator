"""Unit tests for the evaluator — mocked voters and judge."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from aicorellator.analysis.client import LLMResponse
from aicorellator.analysis.config_loader import EnsembleConfig
from aicorellator.analysis.evaluator import (
    _edge_to_dict,
    _node_to_dict,
    evaluate_chains,
)
from aicorellator.analysis.models import (
    JudgeResponse,
    VoterConfig,
    VoterResponse,
)
from aicorellator.graph import Edge, Node
from aicorellator.graph.query import CandidateChain


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


def _voter_response(model: str, exploitable: bool = True, confidence: float = 0.8):
    return VoterResponse(
        chain_id="chain:test", model=model, prompt_version="v1.1.0",
        exploitable=exploitable, confidence=confidence,
        reasoning="r", evidence=[],
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
