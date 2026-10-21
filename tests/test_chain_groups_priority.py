"""Chain group prioritization (1.3c, ADR-0012).

Scoring, ranking, selection and the shape of `score_components`.

The groups here are built by hand (`_pair`) rather than enumerated,
because the point is one signal at a time: a graph that produces a
trifecta node, a graph that produces a dangerous sink, and so on, each
with everything else held still. The nodes *are* written to the store —
the scorer reads capabilities and confidence from the graph, so a
group whose nodes do not exist would score zero for the wrong reason.
Enumeration itself is covered by `test_query_layer` and the pipeline
tests.
"""

from __future__ import annotations

import json

import pytest

from aicorellator import GraphStore
from aicorellator.graph import ChainGroup, Node
from aicorellator.graph.query import (
    SCORE_COMPONENT_KEYS,
    SCORE_DANGEROUS_SINK,
    SCORE_EXFIL_OR_WRITE,
    SCORE_LETHAL_TRIFECTA,
    SCORE_SHORT_CHAIN,
    SCORE_STALE_NODE,
    SCORE_USER_READABLE_SOURCE,
    SCORE_USER_WRITABLE_SOURCE,
    SHORT_CHAIN_TOTAL_HOPS,
    priority_for,
)


def _ids(key: str = "a") -> dict[str, str]:
    """The node ids one pair uses, namespaced by `key`.

    Two groups in one test must not share nodes: `nodes.upsert` unions
    capabilities and merges attributes, so a second pair reusing an id
    would inherit the first pair's capabilities and silently score for
    the wrong reason.
    """
    pad = key * 16
    return {
        "entry": f"entry_point:{pad}",
        "source": f"untrusted_source:{pad}",
        "sink": f"sink:{pad}",
        "writer": f"tool:{key}w0",
        "reader": f"tool:{key}r0",
    }


def _node(
    store: GraphStore,
    node_id: str,
    kind: str,
    name: str,
    capabilities: list[str] | None = None,
    confidence: float = 1.0,
    attributes: dict[str, object] | None = None,
) -> str:
    store.nodes.upsert(Node(
        id=node_id,
        kind=kind,
        name=name,
        host_id="localhost",
        capabilities=capabilities or [],
        confidence=confidence,
        attributes=attributes or {},
    ))
    return node_id


def _pair(
    store: GraphStore,
    *,
    key: str = "a",
    group_id: str | None = None,
    source_type: str = "rag_corpus",
    declare_type: bool = True,
    sink_caps: list[str] | None = None,
    reader_caps: list[str] | None = None,
    writer_caps: list[str] | None = None,
    write_hops: int = 2,
    read_hops: int = 2,
    stale_at: str | None = None,
) -> ChainGroup:
    """One write/read pair around one source, with knobs per signal.

        entry → tool:w* → source → tool:r* → sink

    `write_hops` / `read_hops` are hops, so the node count is one more.
    `reader_caps` land on the read side's intermediate nodes (the place
    a trifecta node would be), `sink_caps` on the terminal node.
    """
    ids = _ids(key)
    _node(
        store,
        ids["source"],
        "untrusted_source",
        f"{source_type}:catalogue.list",
        attributes={"source_type": source_type} if declare_type else {},
    )
    _node(store, ids["entry"], "entry_point", "POST /chat")
    _node(store, ids["sink"], "sink", "run_shell", capabilities=sink_caps)

    write_nodes = [ids["entry"]]
    for i in range(max(0, write_hops - 1)):
        node_id = f"tool:{key}w{i}"
        _node(store, node_id, "tool", f"writer_{i}", capabilities=writer_caps)
        write_nodes.append(node_id)
    write_nodes.append(ids["source"])

    read_nodes = [ids["source"]]
    for i in range(max(0, read_hops - 1)):
        node_id = f"tool:{key}r{i}"
        _node(store, node_id, "tool", f"reader_{i}", capabilities=reader_caps)
        read_nodes.append(node_id)
    read_nodes.append(ids["sink"])

    if stale_at is not None:
        store.nodes.update_confidence(stale_at, -0.5)

    return ChainGroup(
        group_id=group_id or f"chain_group:{key * 16}",
        untrusted_source_id=ids["source"],
        untrusted_source_name=f"{source_type}:catalogue.list",
        write_chain_ids=[f"chain:{key}w"],
        read_chain_ids=[f"chain:{key}r"],
        write_chains=[write_nodes],
        read_chains=[read_nodes],
    )


def _score(store: GraphStore, group: ChainGroup) -> dict[str, object]:
    """Score one group and return its breakdown."""
    score, components = store.query.score_chain_group(group)
    assert score == components["total"]
    return components


# ------------------------------------------------------------
# Lethal trifecta (+5)
# ------------------------------------------------------------

TRIFECTA = ["reads_untrusted", "reads_env", "executes_shell"]


def test_lethal_trifecta_fires_on_a_read_side_node(store: GraphStore) -> None:
    group = _pair(store, reader_caps=TRIFECTA)

    components = _score(store, group)

    assert components["lethal_trifecta"] == SCORE_LETHAL_TRIFECTA
    assert "reads_untrusted" in components["reasons"]["lethal_trifecta"]
    assert _ids()["reader"] in components["reasons"]["lethal_trifecta"]


@pytest.mark.parametrize("missing", ["reads_untrusted", "reads_env",
                                     "executes_shell"])
def test_lethal_trifecta_needs_all_three(
    store: GraphStore, missing: str
) -> None:
    """Two of the three is not the trifecta."""
    caps = [c for c in TRIFECTA if c != missing]
    group = _pair(store, reader_caps=caps)

    components = _score(store, group)

    assert components["lethal_trifecta"] == 0


def test_lethal_trifecta_accepts_reads_secrets_or_reads_env(
    store: GraphStore,
) -> None:
    """The secret half of the trifecta is a set, not one capability."""
    group = _pair(
        store, reader_caps=["reads_untrusted", "reads_secrets",
                            "http_egress"],
    )

    components = _score(store, group)

    assert components["lethal_trifecta"] == SCORE_LETHAL_TRIFECTA


def test_lethal_trifecta_ignores_the_writing_side(
    store: GraphStore,
) -> None:
    """The content enters through the read; the write is the delivery."""
    group = _pair(store, writer_caps=TRIFECTA)

    components = _score(store, group)

    assert components["lethal_trifecta"] == 0
    assert "no read-side node" in components["reasons"]["lethal_trifecta"]


# ------------------------------------------------------------
# Reading chain sink (+4 / +3)
# ------------------------------------------------------------

@pytest.mark.parametrize("cap", ["executes_shell", "executes_code"])
def test_dangerous_sink_fires(store: GraphStore, cap: str) -> None:
    group = _pair(store, sink_caps=[cap])

    components = _score(store, group)

    assert components["dangerous_sink"] == SCORE_DANGEROUS_SINK
    assert cap in components["reasons"]["dangerous_sink"]


@pytest.mark.parametrize("cap", ["writes_filesystem", "http_egress"])
def test_exfil_or_write_sink_fires(store: GraphStore, cap: str) -> None:
    group = _pair(store, sink_caps=[cap])

    components = _score(store, group)

    assert components["exfil_or_write"] == SCORE_EXFIL_OR_WRITE
    assert cap in components["reasons"]["exfil_or_write"]


def test_both_sink_components_can_fire(store: GraphStore) -> None:
    """Executing and exfiltrating are two signals, not two levels."""
    group = _pair(store, sink_caps=["executes_shell", "http_egress"])

    components = _score(store, group)

    assert components["dangerous_sink"] == SCORE_DANGEROUS_SINK
    assert components["exfil_or_write"] == SCORE_EXFIL_OR_WRITE


def test_a_benign_sink_scores_nothing(store: GraphStore) -> None:
    group = _pair(store, sink_caps=["reads_db"])

    components = _score(store, group)

    assert components["dangerous_sink"] == 0
    assert components["exfil_or_write"] == 0


def test_a_mid_chain_dangerous_node_is_not_a_sink(
    store: GraphStore,
) -> None:
    """A read chain reaches its sink; it does not pass through it."""
    group = _pair(store, reader_caps=["executes_shell"])

    components = _score(store, group)

    assert components["dangerous_sink"] == 0


# ------------------------------------------------------------
# Source type (+2 / +1)
# ------------------------------------------------------------

@pytest.mark.parametrize("source_type", ["rag_corpus", "user_upload"])
def test_user_writable_source_fires(
    store: GraphStore, source_type: str
) -> None:
    group = _pair(store, source_type=source_type)

    components = _score(store, group)

    assert components["user_writable_source"] == SCORE_USER_WRITABLE_SOURCE
    assert components["user_readable_source"] == 0
    assert source_type in components["reasons"]["user_writable_source"]


def test_user_readable_source_fires(store: GraphStore) -> None:
    group = _pair(store, source_type="mcp_tool_description")

    components = _score(store, group)

    assert components["user_readable_source"] == SCORE_USER_READABLE_SOURCE
    assert components["user_writable_source"] == 0


def test_an_unweighed_source_type_scores_zero(store: GraphStore) -> None:
    """`email_inbox` has no weight in ADR-0012. It is not writable."""
    group = _pair(store, source_type="email_inbox")

    components = _score(store, group)

    assert components["user_writable_source"] == 0
    assert components["user_readable_source"] == 0


def test_source_type_falls_back_to_the_node_name(
    store: GraphStore,
) -> None:
    """A group whose anchor carries no attributes still has a type."""
    group = _pair(store, source_type="rag_corpus", declare_type=False)

    components = _score(store, group)

    assert components["user_writable_source"] == SCORE_USER_WRITABLE_SOURCE


# ------------------------------------------------------------
# Short chain (+1)
# ------------------------------------------------------------

def test_short_chain_at_the_threshold(store: GraphStore) -> None:
    group = _pair(store, write_hops=3, read_hops=3)

    components = _score(store, group)

    assert group.total_hops == SHORT_CHAIN_TOTAL_HOPS
    assert components["short_chain"] == SCORE_SHORT_CHAIN


def test_one_hop_past_the_threshold_is_not_short(
    store: GraphStore,
) -> None:
    group = _pair(store, write_hops=4, read_hops=3)

    components = _score(store, group)

    assert group.total_hops == SHORT_CHAIN_TOTAL_HOPS + 1
    assert components["short_chain"] == 0
    assert "7 hops" in components["reasons"]["short_chain"]


# ------------------------------------------------------------
# Stale node (-2)
# ------------------------------------------------------------

def test_a_stale_node_costs_two(store: GraphStore) -> None:
    group = _pair(store, stale_at="tool:ar0")

    components = _score(store, group)

    assert components["stale_node"] == SCORE_STALE_NODE
    assert "tool:ar0" in components["reasons"]["stale_node"]


def test_a_low_score_is_negative_not_clamped(store: GraphStore) -> None:
    """A stale, low-signal group ranks below a group that scored 0."""
    group = _pair(
        store,
        source_type="email_inbox",
        write_hops=4,
        read_hops=3,
        stale_at="tool:ar0",
    )

    score, components = store.query.score_chain_group(group)

    assert score == SCORE_STALE_NODE
    assert score < 0
    assert components["total"] == SCORE_STALE_NODE


# ------------------------------------------------------------
# Signals deliberately not applied
# ------------------------------------------------------------

@pytest.mark.parametrize("key", ["authenticated_entry", "shared_entry"])
def test_unmodelled_signals_are_pinned_at_zero(
    store: GraphStore, key: str
) -> None:
    """No corpus evidence, so the weight is off — and visible."""
    group = _pair(store, reader_caps=TRIFECTA)

    components = _score(store, group)

    assert components[key] == 0
    assert "not modelled" in components["reasons"][key]


# ------------------------------------------------------------
# score_components shape
# ------------------------------------------------------------

def test_score_components_shape(store: GraphStore) -> None:
    """Every component key present, reasons complete and name-based."""
    group = _pair(
        store,
        reader_caps=TRIFECTA,
        sink_caps=["executes_shell"],
    )

    components = _score(store, group)

    assert set(components) == set(SCORE_COMPONENT_KEYS) | {
        "total", "reasons",
    }
    assert components["total"] == sum(
        components[key] for key in SCORE_COMPONENT_KEYS
    )
    reasons = components["reasons"]
    assert set(reasons) == set(SCORE_COMPONENT_KEYS)
    for key, reason in reasons.items():
        assert reason, key
        assert "\n" not in reason
        assert len(reason) <= 100
    # Short, name-based: a capability name or a node id, no values.
    assert "reads_untrusted" in reasons["lethal_trifecta"]
    assert json.loads(json.dumps(components)) == components


def test_score_components_is_json_safe_for_every_group(
    store: GraphStore,
) -> None:
    """A zero-signal group still round-trips through JSON."""
    group = _pair(
        store,
        source_type="email_inbox",
        sink_caps=["reads_db"],
        write_hops=4,
        read_hops=3,
    )

    components = _score(store, group)

    assert components["total"] == 0
    assert json.loads(json.dumps(components)) == components


# ------------------------------------------------------------
# Priority buckets
# ------------------------------------------------------------

@pytest.mark.parametrize(
    ("score", "expected"),
    [(0, "low"), (-2, "low"), (9, "low"), (10, "medium"),
     (14, "medium"), (15, "high"), (20, "high")],
)
def test_priority_boundaries(score: int, expected: str) -> None:
    assert priority_for(score) == expected


def test_priority_boundaries_are_configurable() -> None:
    assert priority_for(10, high_min=5, medium_min=3) == "high"
    assert priority_for(4, high_min=5, medium_min=3) == "medium"
    assert priority_for(2, high_min=5, medium_min=3) == "low"


# ------------------------------------------------------------
# Ranking and selection
# ------------------------------------------------------------

def _two_groups(store: GraphStore) -> tuple[ChainGroup, ChainGroup]:
    """A strong group (7) and a weak one (2)."""
    strong = _pair(store, key="a", sink_caps=["executes_shell"])
    weak = _pair(
        store,
        key="b",
        source_type="mcp_tool_description",
        sink_caps=["reads_db"],
    )
    return strong, weak


def test_ranking_puts_the_higher_score_first(store: GraphStore) -> None:
    strong, weak = _two_groups(store)

    selected = store.query.prioritize_chain_groups(
        [weak, strong], min_score_to_evaluate=0,
    )

    assert selected == [strong, weak]
    assert [g.score for g in selected] == [7.0, 2.0]


def test_ties_break_on_group_id(store: GraphStore) -> None:
    """Deterministic, and a property of the groups, not of the input."""
    first = _pair(store, key="c")
    second = _pair(store, key="b")

    selected = store.query.prioritize_chain_groups(
        [first, second], min_score_to_evaluate=0,
    )

    assert first.score == second.score
    assert [g.group_id for g in selected] == sorted(
        [first.group_id, second.group_id]
    )


def test_selection_honors_the_threshold(store: GraphStore) -> None:
    strong, weak = _two_groups(store)

    selected = store.query.prioritize_chain_groups(
        [weak, strong], min_score_to_evaluate=5,
    )

    assert selected == [strong]
    # The weak group was scored and dropped, not ignored.
    assert weak.score == 2.0
    assert weak.priority is None


def test_selection_honors_max_groups_evaluated(
    store: GraphStore,
) -> None:
    strong, weak = _two_groups(store)

    selected = store.query.prioritize_chain_groups(
        [weak, strong],
        min_score_to_evaluate=0,
        max_groups_evaluated=1,
    )

    assert selected == [strong]
    assert weak.priority is None


def test_max_groups_scored_leaves_the_rest_untouched(
    store: GraphStore,
) -> None:
    """The cap is on enumeration order, so it cannot be on rank."""
    strong, weak = _two_groups(store)

    selected = store.query.prioritize_chain_groups(
        [weak, strong],
        max_groups_scored=1,
        min_score_to_evaluate=0,
    )

    assert selected == [weak]
    assert weak.score == 2.0
    # Never scored: no score, no breakdown, no priority.
    assert strong.score == 0.0
    assert strong.score_components == {}
    assert strong.priority is None


def test_priority_buckets_end_to_end(store: GraphStore) -> None:
    """15 → high, 10 → medium, and the rest is not selected."""
    high = _pair(
        store,
        key="a",
        reader_caps=TRIFECTA,
        sink_caps=["executes_shell", "http_egress"],
    )
    medium = _pair(
        store,
        key="b",
        source_type="mcp_tool_description",
        reader_caps=TRIFECTA,
        sink_caps=["executes_shell"],
        write_hops=4,
        read_hops=3,
    )
    low = _pair(store, key="c", source_type="email_inbox")

    selected = store.query.prioritize_chain_groups([high, medium, low])

    assert [g.priority for g in selected] == ["high", "medium"]
    assert high.score == 15.0
    assert medium.score == 10.0
    assert low.priority is None


def test_prioritize_rejects_negative_caps(store: GraphStore) -> None:
    with pytest.raises(ValueError, match="max_groups_scored"):
        store.query.prioritize_chain_groups([], max_groups_scored=-1)
    with pytest.raises(ValueError, match="max_groups_evaluated"):
        store.query.prioritize_chain_groups([], max_groups_evaluated=-1)
    with pytest.raises(ValueError, match="min_score_to_evaluate"):
        store.query.prioritize_chain_groups([], min_score_to_evaluate=-1)


def test_prioritize_is_deterministic(store: GraphStore) -> None:
    """Same groups, same scores, same order, twice."""
    strong, weak = _two_groups(store)

    first = store.query.prioritize_chain_groups(
        [weak, strong], min_score_to_evaluate=0,
    )
    second = store.query.prioritize_chain_groups(
        [weak, strong], min_score_to_evaluate=0,
    )

    assert [g.group_id for g in first] == [g.group_id for g in second]
    assert [g.score for g in first] == [g.score for g in second]
    assert [g.score_components for g in first] == [
        g.score_components for g in second
    ]


def test_prioritize_is_read_only(store: GraphStore) -> None:
    """Scoring writes nothing: not the graph, not `chain_groups`."""
    strong, weak = _two_groups(store)
    before = (store.nodes.count(), store.edges.count())

    store.query.prioritize_chain_groups([weak, strong])

    after = (store.nodes.count(), store.edges.count())
    assert before == after
    assert store.chain_groups.count() == 0


# ------------------------------------------------------------
# The bridge to the table
# ------------------------------------------------------------

def test_to_record_derives_the_name_and_owns_only_its_columns(
    store: GraphStore,
) -> None:
    group = _pair(
        store, sink_caps=["executes_shell"], write_hops=3, read_hops=4,
    )
    store.query.prioritize_chain_groups([group])

    record = group.to_record()

    assert record.id == group.group_id
    assert record.name == "rag_corpus:catalogue.list:3x4"
    assert record.untrusted_source_id == group.untrusted_source_id
    assert record.write_chain_ids == group.write_chain_ids
    assert record.read_chain_ids == group.read_chain_ids
    assert record.score == group.score
    assert record.score_components == group.score_components
    assert record.priority == "low"
    assert record.status == "active"
    # 1.3d/1.3e columns keep their defaults — 1.3c does not write them.
    assert record.lethal_trifecta is False
    assert record.verdict is None
    assert record.confidence == 0.0
    assert record.reasoning is None
    assert record.validated is False
