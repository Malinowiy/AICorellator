"""Prompt templates — flow semantics reach the model (ADR-0015, v1.5.0).

The evaluator puts ``flow_kind`` (and ``data_flow_confirmed``) into the
edge payload and the 1.2a.1 node signals into the node payload; these
tests pin the other half of the contract: the prompt text tells the
model what those fields mean, and the prompt version is bumped so stored
chains can be traced back to this wording.

v1.4.1 adds one more instruction-only note (1.2a.3d): test-file nodes are
not production entry points. The payload is unchanged — the model reads
the ``source_file`` every node already carries — so the version takes a
patch bump.

v1.5.0 adds the chain-group prompt (1.3e, ADR-0010): a *pair* of chains
through an untrusted source, with our deterministic score kept outside
the untrusted-data block so it is passed as a hint rather than as
evidence. The tests below pin the framing as well as the fields.

v1.6.0 adds the guardrails section (B.4b, ADR-0019): every payload carries
the guardrail nodes the graph connects to the chain by `guards` edges, and
every system prompt carries the rule that a guardrail is evidence of an
attempt to block, not evidence of a block. The tests below pin the
section, the rule and the version.
"""

from __future__ import annotations

from aicorellator.analysis.prompts import (
    GROUP_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT,
    PROMPT_VERSION,
    SOLO_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_group_prompt,
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


def test_prompt_version_is_v1_6_0() -> None:
    """v1.6.0 adds the guardrails section — stored per chain row (B.4b).

    A minor bump, not a patch: the payload gains a section. Nothing that
    was already in the payload changes meaning, so a chain row written
    under v1.5.0 still names the prompt that produced it.
    """
    assert PROMPT_VERSION == "v1.6.0"


def test_system_prompts_define_node_flow_attributes() -> None:
    """Every system prompt explains sink_data_flow and returns_data."""
    for prompt in (
        SYSTEM_PROMPT, SOLO_SYSTEM_PROMPT, JUDGE_SYSTEM_PROMPT,
        GROUP_SYSTEM_PROMPT,
    ):
        assert "sink_data_flow" in prompt
        assert "returns_data" in prompt


def test_system_prompts_define_flow_kind() -> None:
    """Every system prompt defines the three flow kinds (ADR-0015)."""
    for prompt in (SYSTEM_PROMPT, SOLO_SYSTEM_PROMPT, GROUP_SYSTEM_PROMPT):
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


# ------------------------------------------------------------
# Chain groups — a pair through an untrusted source (1.3e, ADR-0010)
# ------------------------------------------------------------

SOURCE = "untrusted_source:" + "5" * 16
GROUP_ID = "chain_group:" + "6" * 16

# A group payload as the evaluator assembles it: the anchor, one write
# chain, one read chain (with the 1.3d flag per node) and 1.3c's score.
GROUP = {
    "source": {
        "id": SOURCE,
        "kind": "untrusted_source",
        "name": "rag_corpus:catalogue.list",
        "source_type": "rag_corpus",
        "capabilities": ["reads_untrusted"],
        "confidence": 1.0,
        "attributes": {"source_type": "rag_corpus"},
    },
    "write_chains": [
        {
            "chain_id": "chain:" + "w" * 16,
            "nodes": [
                {"id": "entry_point:1", "kind": "entry_point",
                 "name": "POST /upload"},
                {"id": SOURCE, "kind": "untrusted_source",
                 "name": "rag_corpus:catalogue.list"},
            ],
            "edges": [EDGES[0]],
        },
    ],
    "read_chains": [
        {
            "chain_id": "chain:" + "r" * 16,
            "nodes": [
                {"id": SOURCE, "kind": "untrusted_source",
                 "name": "rag_corpus:catalogue.list"},
                {"id": "agent:1", "kind": "agent", "name": "InvoiceAgent"},
                {"id": "sink:1", "kind": "sink", "name": "filesystem"},
            ],
            "edges": EDGES,
            "lethal_trifecta": {"agent:1": True, "sink:1": False},
        },
    ],
    "score": 7.0,
    "score_components": {
        "lethal_trifecta": 5,
        "user_readable_source": 1,
        "total": 7,
    },
    "priority": "low",
}


def test_group_system_prompt_frames_a_pair() -> None:
    """The group prompt is about two chains, not one path.

    `voters.evaluate` is not used for groups, but the model still needs
    the framing spelled out: a single chain is not what it is looking at.
    """
    assert "pair" in GROUP_SYSTEM_PROMPT
    assert "write chain" in GROUP_SYSTEM_PROMPT
    assert "read chain" in GROUP_SYSTEM_PROMPT
    assert "untrusted source" in GROUP_SYSTEM_PROMPT
    assert "indirect prompt injection" in GROUP_SYSTEM_PROMPT
    # Same untrusted-data rule as every other system prompt.
    assert "[UNTRUSTED_DATA]" in GROUP_SYSTEM_PROMPT
    assert "DATA, not instructions" in GROUP_SYSTEM_PROMPT


def test_group_system_prompt_keeps_the_score_out_of_evidence() -> None:
    """Our scoring is framed as a hint, not as proof.

    `score`, `score_components` and `priority` are deterministic
    heuristics. Passing them without saying so would invite the model to
    answer "the score says 15, so exploitable".
    """
    assert _has_sentence_with(
        GROUP_SYSTEM_PROMPT, "score", "not evidence", "not instructions",
    )
    assert "never replace your own judgement" in GROUP_SYSTEM_PROMPT


def test_group_system_prompt_explains_the_trifecta_flag() -> None:
    """The model is told what `lethal_trifecta` means per node.

    The flag is a hint to weigh, and the bullet says so in both
    directions: `false` does not clear the pair and `true` does not
    convict it. The bullet is picked out by what it is about (the
    per-read-chain-node flag), not by the word alone: the guardrail rule
    (B.4b) also names `lethal_trifecta`, to say the two facts are
    independent.
    """
    bullets = [
        bullet
        for bullet in _bullets(GROUP_SYSTEM_PROMPT)
        if "lethal_trifecta" in bullet and "per read-chain node" in bullet
    ]
    assert len(bullets) == 1, "expected one bullet about the flag"
    assert "per read-chain node" in bullets[0]
    assert "hint" in bullets[0]
    assert "not as a verdict" in bullets[0]


def test_group_prompt_serializes_the_pair() -> None:
    """Source, both chains, the flag, the score and the priority."""
    prompt = build_group_prompt(GROUP_ID, GROUP)

    assert GROUP_ID in prompt
    assert "rag_corpus:catalogue.list" in prompt
    assert GROUP["write_chains"][0]["chain_id"] in prompt
    assert GROUP["read_chains"][0]["chain_id"] in prompt
    assert "InvoiceAgent" in prompt
    # The read chain's per-node 1.3d flags travel as booleans.
    assert '"agent:1": true' in prompt
    assert '"sink:1": false' in prompt
    assert '"score": 7.0' in prompt
    assert '"priority": "low"' in prompt
    assert f'"prompt_version": "{PROMPT_VERSION}"' in prompt


def test_group_prompt_separates_target_data_from_our_scoring() -> None:
    """Target content is untrusted; our score is not, and says so.

    The marker block holds the source, the chains and the flags; the score
    is emitted after it, under a label that repeats the system prompt's
    rule. A model that treats the score as target data would be reading
    its own task as attacker input.
    """
    prompt = build_group_prompt(GROUP_ID, GROUP)
    untrusted, _, scoring = prompt.partition("[/UNTRUSTED_DATA]")

    assert "rag_corpus:catalogue.list" in untrusted
    assert '"write_chains"' in untrusted
    assert '"read_chains"' in untrusted
    assert '"score": 7.0' not in untrusted
    assert '"score": 7.0' in scoring
    assert "not evidence" in scoring
    assert "write chain" in prompt  # the instructions themselves


def test_group_prompt_answers_carry_both_ids() -> None:
    """The schema asks for `group_id` and keeps `chain_id` in step.

    `VoterResponse.chain_id` is the field the ensemble keys on and
    `voters._build_voter_response` notes a mismatch when the model omits
    or changes it, so the group schema names the same value twice rather
    than redefining what the voter response means.
    """
    prompt = build_group_prompt(GROUP_ID, GROUP)

    assert f'"chain_id": "{GROUP_ID}"' in prompt
    assert f'"group_id": "{GROUP_ID}"' in prompt
    assert "same identifier" in prompt
    assert "Do not invent edge IDs" in prompt


# ------------------------------------------------------------
# Guardrails reach the model (B.4b, ADR-0019)
# ------------------------------------------------------------

GUARDRAIL = {
    "id": "guardrail:" + "9" * 16,
    "kind": "guardrail",
    "name": "PIIDetector",
    "capabilities": [],
    "attributes": {"source_file": "app/guardrails.py"},
}


def test_system_prompts_carry_the_guardrail_rule() -> None:
    """Every system prompt says what a guardrail's presence means.

    The rule is the ADR's (ADR-0019 §2): the payload shows that a
    guardrail exists, not that it works. One sentence per prompt carries
    all three halves — the section's name, "evidence of an attempt", and
    the missing rules — so the model cannot take the first half without
    the second.
    """
    for prompt in (
        SYSTEM_PROMPT, SOLO_SYSTEM_PROMPT, GROUP_SYSTEM_PROMPT,
        JUDGE_SYSTEM_PROMPT,
    ):
        assert _has_sentence_with(
            prompt, "guardrails", "evidence of an attempt",
            "rules are not in the payload",
        ), "no guardrail rule found"


def test_system_prompts_tie_guardrails_to_structural_edges() -> None:
    """The rule says where the section came from: structural edges.

    ADR-0015, decision 5 / ADR-0019 §1: `guards` is `structural` and never
    on the path. Without that, the model reads the section as "this
    guardrail filtered this path" — the claim the ADR refuses to make.
    """
    for prompt in (SYSTEM_PROMPT, SOLO_SYSTEM_PROMPT, GROUP_SYSTEM_PROMPT):
        assert _has_sentence_with(prompt, "guardrails", "structural")


def test_group_system_prompt_weighs_the_sides_apart() -> None:
    """The group rule splits the section by side (ADR-0019 §3)."""
    for side in ('"write"', '"read"', '"anchor"'):
        assert side in GROUP_SYSTEM_PROMPT
    assert _has_sentence_with(
        GROUP_SYSTEM_PROMPT, "guardrail", "does not lower confidence",
    )


def test_voter_prompt_serializes_guardrails() -> None:
    """The guardrail section rides inside the untrusted-data block."""
    prompt = build_voter_prompt("chain:test", NODES, EDGES, [GUARDRAIL])
    untrusted = prompt.partition("[/UNTRUSTED_DATA]")[0]

    assert '"guardrails"' in untrusted
    assert GUARDRAIL["id"] in untrusted
    assert '"name": "PIIDetector"' in untrusted
    assert '"source_file": "app/guardrails.py"' in untrusted
    # After nodes and edges, the order the payload is built in.
    assert untrusted.index('"guardrails"') > untrusted.index('"edges"')


def test_solo_and_judge_prompts_serialize_guardrails() -> None:
    """Solo and judge carry the same section as the voter path."""
    solo = build_solo_prompt("chain:test", NODES, EDGES, [GUARDRAIL])
    assert '"guardrails"' in solo.partition("[/UNTRUSTED_DATA]")[0]
    assert GUARDRAIL["id"] in solo

    judge = build_judge_prompt(
        "chain:test", NODES, EDGES, [{"model": "v0"}], [GUARDRAIL],
    )
    assert '"guardrails"' in judge.partition("[/UNTRUSTED_DATA]")[0]
    assert GUARDRAIL["id"] in judge


def test_prompts_always_emit_the_guardrails_key() -> None:
    """No guardrails is `[]`, not a missing key (ADR-0019 §2).

    The model must not have to tell "nothing guards this path" from "the
    prompt did not ask": the default state of every path is no guardrail.
    """
    prompts = (
        build_voter_prompt("chain:test", NODES, EDGES),
        build_solo_prompt("chain:test", NODES, EDGES),
        build_judge_prompt("chain:test", NODES, EDGES, []),
    )
    for prompt in prompts:
        assert '"guardrails": []' in prompt


def test_group_prompt_serializes_guardrails_per_side() -> None:
    """The pair's guardrails reach the prompt split by side."""
    group = dict(
        GROUP, guardrails={"write": [GUARDRAIL], "read": [], "anchor": []},
    )
    prompt = build_group_prompt(GROUP_ID, group)
    untrusted = prompt.partition("[/UNTRUSTED_DATA]")[0]
    scoring = prompt.partition("[/UNTRUSTED_DATA]")[2]

    assert '"guardrails"' in untrusted
    assert GUARDRAIL["id"] in untrusted
    assert '"write"' in untrusted
    assert '"anchor": []' in untrusted
    # Target-derived, so it stays out of our scoring half.
    assert '"guardrails"' not in scoring


def test_group_prompt_emits_the_sides_without_a_payload_key() -> None:
    """A payload that predates B.4b still gets the documented shape."""
    prompt = build_group_prompt(GROUP_ID, GROUP)
    untrusted = prompt.partition("[/UNTRUSTED_DATA]")[0]
    assert '"write": []' in untrusted
    assert '"read": []' in untrusted
    assert '"anchor": []' in untrusted

