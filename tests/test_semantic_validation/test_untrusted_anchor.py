"""Check 3 — `is_untrusted_anchor_valid`: a claimed anchor is a source.

The check runs only where an anchor is claimed. A chain with no
`untrusted_source` is not a chain group and has nothing to prove; a
chain that carries one has to show three things — the right kind, a
declared `source_type` from the families that have a detector, and a
reader that carries `reads_untrusted` (ADR-0010).
"""

from __future__ import annotations

import pytest

from aicorellator import GraphStore
from aicorellator.graph.query import UNTRUSTED_SOURCE_TYPES
from aicorellator.patterns import UNTRUSTED_SOURCE_RULES

from .conftest import Graph


def test_the_families_are_the_three_that_have_detectors() -> None:
    """`user_upload` is not implemented and must not be accepted here."""
    assert UNTRUSTED_SOURCE_TYPES == {
        "rag_corpus",
        "mcp_tool_description",
        "email_inbox",
    }
    assert {name for name, _rule in UNTRUSTED_SOURCE_RULES} == (
        UNTRUSTED_SOURCE_TYPES
    )


def test_a_chain_without_an_anchor_is_vacuously_valid(
    store: GraphStore, graph: Graph
) -> None:
    """No source in the chain, no anchor claim to check."""
    entry = graph.node("entry", "entry_point")
    agent = graph.node("agent", "agent")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", entry, agent)
    e2 = graph.edge("e2", agent, sink)

    ok, kind, notes = store.query.validate_chain(
        [entry, agent, sink], [e1, e2]
    )

    assert (ok, kind, notes) == (True, "semantic", [])


@pytest.mark.parametrize(
    "source_type",
    ["rag_corpus", "mcp_tool_description", "email_inbox"],
)
def test_a_well_formed_anchor_is_valid(
    store: GraphStore, graph: Graph, source_type: str
) -> None:
    """Kind, declared family, and a reader that reads untrusted input."""
    source = graph.source("inbox", source_type=source_type)

    assert store.query.is_untrusted_anchor_valid(source) is True


def test_a_source_without_a_source_type_is_invalid(
    store: GraphStore, graph: Graph
) -> None:
    """The attribute is the claim; without it the node declares nothing."""
    source = graph.source("inbox", source_type=None)

    assert store.query.is_untrusted_anchor_valid(source) is False


@pytest.mark.parametrize("source_type", ["user_upload", "chat_log", ""])
def test_a_source_type_outside_the_three_families_is_invalid(
    store: GraphStore, graph: Graph, source_type: str
) -> None:
    """`user_upload` has no detector yet (BACKLOG B.3)."""
    source = graph.source("inbox", source_type=source_type)

    assert store.query.is_untrusted_anchor_valid(source) is False


def test_a_reader_without_reads_untrusted_is_invalid(
    store: GraphStore, graph: Graph
) -> None:
    """An edge to a node that does not read untrusted input is not a reader."""
    source = graph.source(
        "inbox", reader_capabilities=["executes_code"]
    )

    assert store.query.is_untrusted_anchor_valid(source) is False


def test_a_source_with_no_reader_is_invalid(
    store: GraphStore, graph: Graph
) -> None:
    """An anchor with no `has_access_to` edge is a label, not a channel."""
    source = graph.node(
        "inbox", "untrusted_source", attributes={"source_type": "email_inbox"}
    )

    assert store.query.is_untrusted_anchor_valid(source) is False


def test_one_reader_among_several_is_enough(
    store: GraphStore, graph: Graph
) -> None:
    """The rule is "at least one", not "every target"."""
    source = graph.source("inbox")                 # the good reader
    tool = graph.node("tool", "tool", capabilities=["executes_code"])
    graph.edge("inbox-tool", source, tool)         # a target that is not one

    assert store.query.is_untrusted_anchor_valid(source) is True


def test_a_node_of_another_kind_is_not_an_anchor(
    store: GraphStore, graph: Graph
) -> None:
    """`source_type` on a `tool` does not make it a source."""
    tool = graph.node(
        "search", "tool", attributes={"source_type": "rag_corpus"}
    )

    assert store.query.is_untrusted_anchor_valid(tool) is False


def test_an_unknown_id_is_not_an_anchor(
    store: GraphStore, graph: Graph
) -> None:
    assert store.query.is_untrusted_anchor_valid(
        "untrusted_source:nope"
    ) is False


def test_a_bad_anchor_rejects_the_chain_that_claims_it(
    store: GraphStore, graph: Graph
) -> None:
    """The chain-level view: the anchor's failure is one of its notes."""
    entry = graph.node("entry", "entry_point")
    source = graph.source("inbox", source_type="user_upload")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", entry, source)
    e2 = graph.edge("e2", source, sink)

    ok, kind, notes = store.query.validate_chain(
        [entry, source, sink], [e1, e2]
    )

    assert ok is False
    assert kind == "existence"
    assert notes == [f"untrusted anchor {source} is not a usable source"]


def test_a_good_anchor_does_not_reject_the_chain(
    store: GraphStore, graph: Graph
) -> None:
    """The same chain with a well-formed anchor passes all four checks."""
    entry = graph.node("entry", "entry_point")
    source = graph.source("inbox")
    sink = graph.node("sink", "sink")
    e1 = graph.edge("e1", entry, source)
    e2 = graph.edge("e2", source, sink)

    ok, kind, notes = store.query.validate_chain(
        [entry, source, sink], [e1, e2]
    )

    assert (ok, kind, notes) == (True, "semantic", [])
