"""Prompt templates — flow semantics reach the model (ADR-0015, v1.4.1).

The evaluator puts ``flow_kind`` (and ``data_flow_confirmed``) into the
edge payload and the 1.2a.1 node signals into the node payload; these
tests pin the other half of the contract: the prompt text tells the
model what those fields mean, and the prompt version is bumped so stored
chains can be traced back to this wording.

v1.4.1 adds one more instruction-only note (1.2a.3d): test-file nodes are
not production entry points. The payload is unchanged — the model reads
the ``source_file`` every node already carries — so the version takes a
patch bump.
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


def _bullets(text: str) -> list[str]:
    """Bullet blocks of `text`: a ``- `` line plus its indented continuation.

    Prompt prose wraps at 79 columns, so a bullet and its sentences span
    several source lines; this flattens each bullet back into one string.
    """
    bullets: list[str] = []
    index = -1
    for line in text.splitlines():
        if line.startswith("- "):
            bullets.append(line)
            index = len(bullets) - 1
        elif line.startswith("  ") and line.strip() and index >= 0:
            bullets[index] += " " + line.strip()
        else:
            index = -1
    return bullets


def _has_sentence_with(text: str, *words: str) -> bool:
    """True if one sentence of `text` mentions every word (case-insensitive).

    Line wrapping is flattened first, so a sentence split across source
    lines still counts as one sentence.
    """
    for sentence in " ".join(text.split()).split(". "):
        lowered = sentence.lower()
        if all(word.lower() in lowered for word in words):
            return True
    return False


def test_prompt_version_is_v1_4_1() -> None:
    """v1.4.1 adds the test-file note — stored per chain row (1.2a.3d)."""
    assert PROMPT_VERSION == "v1.4.1"


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


# --- Test-file nodes are not production entry points (1.2a.3d) ---
#
# Discovery deliberately keeps the target's test files: some frameworks
# test the production code path, and test-file vulnerabilities are real.
# The prompt carries the judgement instead — the model sees `source_file`
# on every node and can tell `tests/` from production code.


def test_system_prompt_marks_test_files_as_non_production() -> None:
    """The voter's system prompt has a sentence with test and production."""
    assert _has_sentence_with(SYSTEM_PROMPT, "test", "production")
    assert "source_file" in SYSTEM_PROMPT


def test_solo_system_prompt_marks_test_files_as_non_production() -> None:
    """Solo mode carries the same rule, tied to its entry-point step."""
    assert _has_sentence_with(SOLO_SYSTEM_PROMPT, "test", "production")
    assert "source_file" in SOLO_SYSTEM_PROMPT


def test_judge_system_prompt_weighs_test_only_chains() -> None:
    """The judge is told a test-only chain does not get a high verdict."""
    assert _has_sentence_with(JUDGE_SYSTEM_PROMPT, "test", "production")
    assert "test-only" in JUDGE_SYSTEM_PROMPT


def test_system_prompts_name_the_test_file_patterns() -> None:
    """The rule is actionable only if the model knows what test code is."""
    for prompt in (SYSTEM_PROMPT, SOLO_SYSTEM_PROMPT):
        for pattern in ("tests/", "test_*.py", "*_test.py", "conftest.py"):
            assert pattern in prompt


def test_voter_prompt_has_test_file_calibration_bullet() -> None:
    """One calibration bullet says a test-only chain is not production."""
    prompt = build_voter_prompt("chain:test", NODES, EDGES)
    matching = [
        bullet
        for bullet in _bullets(prompt)
        if "test-file" in bullet and "production" in bullet.lower()
    ]
    assert len(matching) == 1, "expected one test-file calibration bullet"
    assert "production attack path" in matching[0]
    assert "lower the confidence" in matching[0]

