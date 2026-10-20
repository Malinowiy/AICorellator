"""Prompt templates — flow semantics reach the model (ADR-0015, v1.4.0).

The evaluator puts ``flow_kind`` (and ``data_flow_confirmed``) into the
edge payload and the 1.2a.1 node signals into the node payload; these
tests pin the other half of the contract: the prompt text tells the
model what those fields mean, and the prompt version is bumped so stored
chains can be traced back to this wording.
"""

from __future__ import annotations

from aicorellator.analysis.prompts import (
    JUDGE_SYSTEM_PROMPT,
    PROMPT_VERSION,
    SOLO_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_judge_prompt,
    build_solo_prompt,
    build_voter_prompt,
)


NODES = [
    {"id": "entry_point:1", "kind": "entry_point", "name": "POST /invoice"},
    {"id": "agent:1", "kind": "agent", "name": "InvoiceAgent"},
    {"id": "sink:1", "kind": "sink", "name": "filesystem"},
]

# Edges as the evaluator serializes them for a FinBot-shaped chain.
EDGES = [
    {
        "id": "edge:1", "kind": "uses_model", "source_id": "entry_point:1",
        "target_id": "agent:1", "flow_kind": "control",
        "capabilities": ["control_flows_forward"],
        "precision": "syntactic", "attributes": {},
    },
    {
        "id": "edge:2", "kind": "has_access_to", "source_id": "agent:1",
        "target_id": "sink:1", "flow_kind": "data",
        "data_flow_confirmed": "parameter_match",
        "capabilities": ["data_flows_forward"],
        "precision": "syntactic", "attributes": {},
    },
]


def test_prompt_version_is_v1_4_0() -> None:
    """v1.4.0 adds the node signals — stored per chain row (1.2a.1)."""
    assert PROMPT_VERSION == "v1.4.0"


def test_system_prompts_define_node_flow_attributes() -> None:
    """Every system prompt explains sink_data_flow and returns_data."""
    for prompt in (SYSTEM_PROMPT, SOLO_SYSTEM_PROMPT, JUDGE_SYSTEM_PROMPT):
        assert "sink_data_flow" in prompt
        assert "returns_data" in prompt


def test_system_prompts_define_flow_kind() -> None:
    """Every system prompt defines the three flow kinds (ADR-0015)."""
    for prompt in (SYSTEM_PROMPT, SOLO_SYSTEM_PROMPT):
        assert "flow_kind" in prompt
        assert '"control"' in prompt
        assert '"data"' in prompt
        assert '"structural"' in prompt
        assert "data_flow_confirmed" in prompt
        assert "parameter_match" in prompt
        assert "provides_tool" in prompt
        assert "guards" in prompt


def test_voter_prompt_calibration_covers_node_attributes() -> None:
    """The voter's calibration rules mention the node-level signal."""
    prompt = build_voter_prompt("chain:test", NODES, EDGES)
    assert "sink_data_flow" in prompt
    assert "returns_data" in prompt
    assert "potential data flow" in prompt


def test_node_attributes_reach_the_solo_prompt_payload() -> None:
    """A tool/sink node payload carries the 1.2a.1 attributes verbatim."""
    nodes = [
        {"id": "tool:1", "kind": "tool", "name": "search_files",
         "attributes": {"returns_data": True}},
        {"id": "sink:1", "kind": "sink", "name": "delete_file",
         "attributes": {"sink_data_flow": "parameter_match"}},
    ]
    prompt = build_solo_prompt("chain:test", nodes, EDGES)
    assert '"returns_data": true' in prompt
    assert '"sink_data_flow": "parameter_match"' in prompt


def test_voter_prompt_serializes_flow_fields() -> None:
    """The voter prompt payload carries flow_kind and data_flow_confirmed."""
    prompt = build_voter_prompt("chain:test", NODES, EDGES)
    assert '"flow_kind": "control"' in prompt
    assert '"flow_kind": "data"' in prompt
    assert '"data_flow_confirmed": "parameter_match"' in prompt
    assert "Calibration rules:" in prompt
    assert 'Read each edge\'s "flow_kind"' in prompt
    # The chain keeps its answer schema and version stamp.
    assert f'"prompt_version": "{PROMPT_VERSION}"' in prompt


def test_solo_prompt_serializes_flow_fields() -> None:
    """Solo mode gets the same payload contract as the voter path."""
    prompt = build_solo_prompt("chain:test", NODES, EDGES)
    assert '"flow_kind": "data"' in prompt
    assert '"data_flow_confirmed": "parameter_match"' in prompt
    assert 'flow_kind == "data"' in prompt
    assert f'"prompt_version": "{PROMPT_VERSION}"' in prompt


def test_judge_prompt_mentions_flow_semantics() -> None:
    """The judge is told how to weigh data chains vs control chains."""
    assert "flow_kind" in JUDGE_SYSTEM_PROMPT
    assert "data_flow_confirmed" in JUDGE_SYSTEM_PROMPT

    prompt = build_judge_prompt(
        "chain:test", NODES, EDGES,
        [{"model": "v0", "verdict": "exploitable", "confidence": 0.7,
          "reasoning": "data edge on the last hop"}],
    )
    assert '"flow_kind": "data"' in prompt
    assert '"data_flow_confirmed": "parameter_match"' in prompt
