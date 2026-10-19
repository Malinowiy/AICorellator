"""Prompt templates — flow semantics reach the model (ADR-0015, v1.3.0).

The evaluator puts ``flow_kind`` (and ``data_flow_confirmed``) into the
edge payload; these tests pin the other half of the contract: the prompt
text tells the model what those fields mean, and the prompt version is
bumped so stored chains can be traced back to this wording.
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


def test_prompt_version_is_v1_3_0() -> None:
    """v1.3.0 is the flow_kind payload version — stored per chain row."""
    assert PROMPT_VERSION == "v1.3.0"


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
