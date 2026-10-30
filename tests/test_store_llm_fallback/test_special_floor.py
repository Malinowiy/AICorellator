"""The special floor: `identity_source='llm'` is judged at 0.1 (W1/W2).

ADR-0021 §9's floor is 0.3, and ADR-0023 §5 makes the fallback's links the
one exception: a `writes_to` / `reads_from` pair that carries
`attributes["identity_source"] = "llm"` is judged against
`confidence.WEAK_JOIN_FLOOR_LLM` (0.1) instead. Without it the fallback's
0.2 links would be inert — no store, no edges, no group — and the whole
layer would be a no-op that costs a model call.

W1: a fallback store at 0.2 is **weak** — in the graph, out of the default
enumeration, `include_weak=True` to see it.
W2: a deterministic link at 0.2 is not given the special floor. That is
the pin that keeps the exception narrow.
"""

from __future__ import annotations

from aicorellator import GraphStore
from aicorellator.graph import Edge, Node
from aicorellator.graph.query import WEAK_JOIN_FLOOR
from aicorellator.store.confidence import (
    LLM_IDENTITY_BASE,
    LLM_IDENTITY_SOURCE,
    WEAK_JOIN_FLOOR_LLM,
)

from .conftest import FakeLLM, answer, read_site, write_site
from .test_runner import READ_LINE, WRITE_LINE, _case


# ============================================================
# The constants, and their order
# ============================================================

def test_the_three_numbers_are_in_order() -> None:
    """0.1 < 0.2 < 0.3: the floor lets the base through, §9 does not."""
    assert WEAK_JOIN_FLOOR_LLM < LLM_IDENTITY_BASE < WEAK_JOIN_FLOOR


def test_the_llm_floor_admits_the_llm_base() -> None:
    """The exception exists to keep the fallback from being inert."""
    assert LLM_IDENTITY_BASE >= WEAK_JOIN_FLOOR_LLM


# ============================================================
# A hand-built pair (W1/W2, without the runner)
# ============================================================

def _put(store: GraphStore, node_id: str, kind: str, name: str) -> str:
    store.nodes.upsert(Node(
        id=node_id, kind=kind, name=name, host_id="localhost",
    ))
    return node_id


def _link(
    store: GraphStore, edge_id: str, source: str, target: str,
) -> str:
    store.edges.upsert(Edge(
        id=edge_id, source_id=source, target_id=target,
        kind="has_access_to", host_id="localhost", flow_kind="control",
    ))
    return edge_id


def _pair(
    store: GraphStore,
    tag: str,
    *,
    write_confidence: float,
    read_confidence: float,
    identity_source: str | None,
) -> str:
    """One write chain into a store and one read chain out of it.

        entry → writer --writes_to--> store --reads_from--> source
        source → reader → sink

    `identity_source` is stamped on both links' attributes, which is what
    the join reads to pick its floor.
    """
    attributes = (
        {} if identity_source is None
        else {"identity_source": identity_source}
    )
    ids = {
        "entry": f"entry_point:{'e' * 15}{tag}",
        "writer": f"tool:{'w' * 15}{tag}",
        "store": f"store:{'d' * 15}{tag}",
        "source": f"untrusted_source:{'u' * 15}{tag}",
        "reader": f"tool:{'r' * 15}{tag}",
        "sink": f"sink:{'s' * 15}{tag}",
    }
    for name, kind in (
        ("entry", "entry_point"), ("writer", "tool"), ("store", "store"),
        ("source", "untrusted_source"), ("reader", "tool"), ("sink", "sink"),
    ):
        _put(store, ids[name], kind, f"{kind}:{tag}")
    _link(store, f"edge:{'1' * 15}{tag}", ids["entry"], ids["writer"])
    _link(store, f"edge:{'3' * 15}{tag}", ids["source"], ids["reader"])
    _link(store, f"edge:{'4' * 15}{tag}", ids["reader"], ids["sink"])
    store.edges.upsert(Edge(
        id=f"edge:{'2' * 15}{tag}", source_id=ids["writer"],
        target_id=ids["store"], kind="writes_to", host_id="localhost",
        flow_kind="structural", identity_confidence=write_confidence,
        attributes=dict(attributes),
    ))
    store.edges.upsert(Edge(
        id=f"edge:{'5' * 15}{tag}", source_id=ids["store"],
        target_id=ids["source"], kind="reads_from", host_id="localhost",
        flow_kind="structural", identity_confidence=read_confidence,
        attributes=dict(attributes),
    ))
    return ids["store"]


def test_w1_a_fallback_pair_at_0_2_is_a_weak_group(
    store: GraphStore,
) -> None:
    """W1 — 0.2 passes 0.1, so the store exists and the group is weak."""
    store_id = _pair(
        store, "a",
        write_confidence=LLM_IDENTITY_BASE,
        read_confidence=LLM_IDENTITY_BASE,
        identity_source=LLM_IDENTITY_SOURCE,
    )

    assert store.query.find_chain_groups() == []

    groups = store.query.find_chain_groups(include_weak=True)

    assert len(groups) == 1
    assert groups[0].store_id == store_id
    assert groups[0].store_confidence == LLM_IDENTITY_BASE
    assert groups[0].weak is True


def test_w1_one_llm_side_is_enough(store: GraphStore) -> None:
    """The pair is an LLM guess if either link says so."""
    _pair(
        store, "a",
        write_confidence=LLM_IDENTITY_BASE,
        read_confidence=0.9,
        identity_source=LLM_IDENTITY_SOURCE,
    )

    groups = store.query.find_chain_groups(include_weak=True)

    assert [g.store_confidence for g in groups] == [LLM_IDENTITY_BASE]


def test_w2_a_deterministic_pair_at_0_2_is_no_group(
    store: GraphStore,
) -> None:
    """W2 — §9's floor is unchanged for a link with no `identity_source`."""
    _pair(
        store, "a",
        write_confidence=0.2,
        read_confidence=0.2,
        identity_source=None,
    )

    assert store.query.find_chain_groups() == []
    assert store.query.find_chain_groups(include_weak=True) == []


def test_w2_the_exception_follows_the_attribute(store: GraphStore) -> None:
    """At the same 0.2, the attribute is what decides."""
    store_id = _pair(
        store, "a",
        write_confidence=0.2,
        read_confidence=0.2,
        identity_source=LLM_IDENTITY_SOURCE,
    )

    groups = store.query.find_chain_groups(include_weak=True)

    assert [g.store_id for g in groups] == [store_id]


def test_a_fallback_pair_at_0_1_is_still_a_group(store: GraphStore) -> None:
    """§5's floor is inclusive: 0.1 is the bottom, not a rejection."""
    _pair(
        store, "a",
        write_confidence=WEAK_JOIN_FLOOR_LLM,
        read_confidence=WEAK_JOIN_FLOOR_LLM,
        identity_source=LLM_IDENTITY_SOURCE,
    )

    groups = store.query.find_chain_groups(include_weak=True)

    assert [g.store_confidence for g in groups] == [WEAK_JOIN_FLOOR_LLM]


def test_a_fallback_pair_under_0_1_is_no_group(store: GraphStore) -> None:
    """Under the special floor there is still no link at all."""
    _pair(
        store, "a",
        write_confidence=0.05,
        read_confidence=0.05,
        identity_source=LLM_IDENTITY_SOURCE,
    )

    assert store.query.find_chain_groups(include_weak=True) == []


def test_a_non_string_identity_source_is_a_deterministic_link(
    store: GraphStore,
) -> None:
    """A value nothing else understands is not the LLM's marker."""
    _pair(
        store, "a",
        write_confidence=0.2,
        read_confidence=0.2,
        identity_source=7,
    )

    assert store.query.find_chain_groups(include_weak=True) == []


# ============================================================
# End to end: the runner's store, in a real graph (the pipeline)
# ============================================================

def test_the_fallback_store_makes_a_weak_group_end_to_end(
    case, store: GraphStore,
) -> None:
    """The pipeline case: the runner fills a gap, the group is weak.

    The fallback's own store and edges go into a graph that already holds
    the two chains — `entry → writer` and `source → reader → sink` — so
    the join has a write side and a read side to pair. Without the special
    floor the pair would be dropped at 0.2 and the graph would hold two
    stores' worth of nothing.
    """
    fake = FakeLLM(answer("/uploads/"), answer("/uploads/"))
    built = _case(case)

    stores, edges = built.run(
        [write_site(line=WRITE_LINE), read_site(line=READ_LINE)],
        llm_call=fake,
    )
    assert len(stores) == 1 and len(edges) == 2

    for node in [
        *built.delta.nodes,
        *stores,
        Node(
            id="entry_point:upload", kind="entry_point",
            name="POST /upload", host_id="localhost",
        ),
        Node(
            id="tool:read_uploads", kind="tool", name="read_uploads",
            host_id="localhost",
        ),
        Node(
            id="sink:shell", kind="sink", name="run_shell",
            host_id="localhost",
        ),
    ]:
        store.nodes.upsert(node)
    for edge in [
        *edges,
        Edge(
            id="edge:entry", source_id="entry_point:upload",
            target_id="tool:handle", kind="has_access_to",
            host_id="localhost", flow_kind="control",
        ),
        Edge(
            id="edge:reader", source_id="untrusted_source:read",
            target_id="tool:read_uploads", kind="has_access_to",
            host_id="localhost", flow_kind="control",
        ),
        Edge(
            id="edge:sink", source_id="tool:read_uploads",
            target_id="sink:shell", kind="has_access_to",
            host_id="localhost", flow_kind="control",
        ),
    ]:
        store.edges.upsert(edge)

    assert store.query.find_chain_groups() == []

    groups = store.query.find_chain_groups(include_weak=True)

    assert len(groups) == 1
    assert groups[0].store_id == stores[0].id
    assert groups[0].store_name == "fs:/uploads/"
    assert groups[0].store_confidence == LLM_IDENTITY_BASE
    assert groups[0].weak is True
    assert groups[0].write_chains == [
        ["entry_point:upload", "tool:handle", stores[0].id]
    ]
    assert groups[0].read_chains == [
        ["untrusted_source:read", "tool:read_uploads", "sink:shell"]
    ]
