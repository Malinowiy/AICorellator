"""The three provenance limiters, and the rules around them (D.2).

Each limiter stops the model from overturning a deterministic reading of
the same code, so each test asserts two things: what the model said, and
what the graph got. The interesting cases are the negative ones — no node.
"""

from __future__ import annotations

import logging

import pytest

from aicorellator.parsing.llm_classifier.cache import content_hash
from aicorellator.parsing.llm_classifier.prompt import PROMPT_VERSION

from .conftest import answer, symbol


# ============================================================
# Limiter 1 — part_of_parent never creates a node
# ============================================================

def test_part_of_parent_creates_no_node(classifier) -> None:
    layer = classifier(answer("part_of_parent", ("calls_llm",)))
    result = layer.classify_file("pkg/mod.py", [
        symbol("run", class_name="Agent", parent_kind="tool"),
    ])
    assert result.nodes == []


def test_part_of_parent_capabilities_merge_into_our_parent(classifier) -> None:
    """The method's behaviour is the class's behaviour.

    The class is in the same batch and the model classified it an agent, so
    the parent node is one *this layer* created — the only kind it may write
    to.
    """
    layer = classifier(
        answer("agent"),                      # the class
        answer("part_of_parent", ("calls_llm",)),   # the method
    )
    result = layer.classify_file("pkg/mod.py", [
        symbol("Agent", kind="class", source="class Agent:\n    pass\n"),
        symbol("run", class_name="Agent"),
    ])

    assert [node.kind for node in result.nodes] == ["agent"]
    assert result.nodes[0].capabilities == ["calls_llm"]


def test_part_of_parent_capabilities_are_merged_not_replaced(classifier) -> None:
    """A merge is a union: the class's own capabilities survive."""
    layer = classifier(
        answer("agent", ("invokes_mcp_tool",)),
        answer("part_of_parent", ("calls_llm",)),
    )
    result = layer.classify_file("pkg/mod.py", [
        symbol("Agent", kind="class", source="class Agent:\n    pass\n"),
        symbol("run", class_name="Agent"),
    ])

    assert result.nodes[0].capabilities == ["calls_llm", "invokes_mcp_tool"]


def test_part_of_parent_without_a_parent_node_is_dropped(
    classifier, caplog
) -> None:
    """No parent this layer can write to — the capabilities go, with a log.

    A deterministic parent is built later, by `normalize()`. Writing the
    model's capabilities into the map that build reads would let the LLM
    change a deterministic classification, which is what a limiter forbids.
    """
    layer = classifier(answer("part_of_parent", ("calls_llm",)))
    with caplog.at_level(logging.DEBUG):
        result = layer.classify_file("pkg/mod.py", [
            symbol("run", class_name="Agent", parent_kind="tool"),
        ])

    assert result.nodes == []
    assert "no parent node" in caplog.text


def test_part_of_parent_on_a_top_level_function_is_dropped(classifier) -> None:
    """A module-level function has no enclosing class to merge into."""
    layer = classifier(answer("part_of_parent", ("reads_env",)))
    result = layer.classify_file("pkg/mod.py", [symbol("helper")])
    assert result.nodes == []


def test_unknown_creates_no_node(classifier) -> None:
    """`unknown` is an answer — "not enough to decide" — not a node."""
    layer = classifier(answer("unknown"))
    result = layer.classify_file("pkg/mod.py", [symbol("mystery", kind="class")])
    assert result.nodes == []


# ============================================================
# Limiter 2 — a method of an agent-class is not a second agent
# ============================================================

@pytest.mark.parametrize("claimed", ["agent", "llm_endpoint", "mcp_server"])
def test_method_of_a_deterministic_agent_class_is_suppressed(
    classifier, caplog, claimed: str
) -> None:
    """The class is the agent; the method is its behaviour."""
    layer = classifier(answer(claimed, ("calls_llm",)))
    with caplog.at_level(logging.DEBUG):
        result = layer.classify_file("pkg/mod.py", [
            symbol("run", class_name="InvoiceAgent", parent_kind="agent"),
        ])

    assert result.nodes == []
    assert "agent-owned" in caplog.text


def test_method_of_an_llm_classified_agent_is_suppressed(classifier) -> None:
    """The parent can be an agent because *this* layer said so.

    Classes are processed first for exactly this reason: the parent's kind
    has to be known before the method is judged.
    """
    layer = classifier(
        answer("agent", ("calls_llm",)),
        answer("agent"),
    )
    result = layer.classify_file("pkg/mod.py", [
        symbol("run", class_name="InvoiceAgent"),
        symbol("InvoiceAgent", kind="class", source="class InvoiceAgent:\n    pass\n"),
    ])

    assert [node.name for node in result.nodes] == ["InvoiceAgent"]


def test_method_of_a_non_agent_class_is_a_node(classifier) -> None:
    """The limiter is about the *agent* parent, not about methods."""
    layer = classifier(answer("agent"))
    result = layer.classify_file("pkg/mod.py", [
        symbol("run", class_name="Helper", parent_kind="tool"),
    ])

    assert [node.kind for node in result.nodes] == ["agent"]
    assert result.nodes[0].name == "run"


def test_method_without_a_parent_class_is_a_node(classifier) -> None:
    layer = classifier(answer("sink", ("executes_shell",)))
    result = layer.classify_file("pkg/mod.py", [symbol("run_shell")])

    assert [node.kind for node in result.nodes] == ["sink"]
    assert result.nodes[0].capabilities == ["executes_shell"]


# ============================================================
# Limiter 3 — a tool without a decorator is marked, not blocked
# ============================================================

def test_tool_in_an_mcp_server_without_a_decorator_is_marked(
    classifier, caplog
) -> None:
    """Blocking would be wrong (custom decorators exist); silence too.

    The node is created either way; the attribute is what tells a reader
    that the model decided this and that nothing in the code declared it.
    """
    layer = classifier(answer("tool", ("reads_filesystem",)))
    with caplog.at_level(logging.WARNING):
        result = layer.classify_file("mcp/servers/filesys/server.py", [
            symbol(
                "read_file",
                file_path="mcp/servers/filesys/server.py",
                class_name="FileServer",
                parent_kind="mcp_server",
            ),
        ])

    assert [node.kind for node in result.nodes] == ["tool"]
    assert result.nodes[0].attributes["unverified_tool"] is True
    assert "unverified tool" in caplog.text


def test_tool_in_an_mcp_server_with_a_decorator_is_not_marked(
    classifier, caplog
) -> None:
    """The declaration is there, so there is nothing to look twice at."""
    layer = classifier(answer("tool"))
    with caplog.at_level(logging.WARNING):
        result = layer.classify_file("mcp/servers/filesys/server.py", [
            symbol(
                "read_file",
                file_path="mcp/servers/filesys/server.py",
                class_name="FileServer",
                parent_kind="mcp_server",
                has_tool_decorator=True,
            ),
        ])

    assert "unverified_tool" not in result.nodes[0].attributes
    assert "unverified tool" not in caplog.text


@pytest.mark.parametrize("parent_kind", ["agent", "tool", None])
def test_a_tool_outside_an_mcp_server_is_not_marked(
    classifier, parent_kind
) -> None:
    """Limiter 3 is about the MCP-server parent, not about tools."""
    layer = classifier(answer("tool"))
    result = layer.classify_file("pkg/mod.py", [
        symbol("fetch", class_name="Client", parent_kind=parent_kind),
    ])

    assert [node.kind for node in result.nodes] == ["tool"]
    assert "unverified_tool" not in result.nodes[0].attributes


def test_a_sink_in_an_mcp_server_is_not_marked(classifier) -> None:
    """Only a *tool* claim is the one that needs a decorator behind it."""
    layer = classifier(answer("sink", ("writes_db",)))
    result = layer.classify_file("mcp/servers/db/server.py", [
        symbol(
            "delete_row",
            file_path="mcp/servers/db/server.py",
            class_name="DbServer",
            parent_kind="mcp_server",
        ),
    ])

    assert "unverified_tool" not in result.nodes[0].attributes


# ============================================================
# The general rules: closed sets and provenance
# ============================================================

def test_capabilities_outside_the_enum_are_dropped(classifier) -> None:
    """An invented capability must never reach the graph.

    `lethal_trifecta` and the sink categories are computed from this
    vocabulary, so a capability the model made up would corrupt a derived
    signal — not merely add noise.
    """
    layer = classifier(answer("tool", ("reads_filesystem", "teleports")))
    result = layer.classify_file("pkg/mod.py", [symbol("read")])

    assert result.nodes[0].capabilities == ["reads_filesystem"]


def test_an_invented_kind_produces_no_node(classifier, caplog) -> None:
    layer = classifier(answer("orchestrator"))
    with caplog.at_level(logging.WARNING):
        result = layer.classify_file("pkg/mod.py", [symbol("run")])

    assert result.nodes == []
    assert "unusable answer" in caplog.text


def test_every_node_carries_both_provenances(classifier) -> None:
    """`["ast", "static_llm:<model_id>"]` — ADR-0016, §`observed_by`."""
    layer = classifier(answer("tool"), model="qwen/test-4b")
    result = layer.classify_file("pkg/mod.py", [symbol("read")])

    assert result.nodes[0].observed_by == ["ast", "static_llm:qwen/test-4b"]


def test_confidence_is_the_node_default(classifier) -> None:
    """D.2 does not calibrate a number, so it must not invent one."""
    layer = classifier(answer("tool"))
    result = layer.classify_file("pkg/mod.py", [symbol("read")])

    assert result.nodes[0].confidence == 1.0


def test_a_class_node_has_the_deterministic_id(classifier) -> None:
    """One symbol, one node: the id is derived the same way.

    `node_from_class` derives `f"{kind}:{sha256(kind:file:name:host)}"`, and
    the classifier builds its node through that same constructor. The digest
    is spelled out here so this test fails if the derivation changes — and
    because a different id would mean two nodes for one symbol.
    """
    import hashlib

    layer = classifier(answer("tool"))
    result = layer.classify_file("pkg/mod.py", [
        symbol("Reader", kind="class", source="class Reader:\n    pass\n"),
    ])

    digest = hashlib.sha256(b"tool:pkg/mod.py:Reader:localhost").hexdigest()
    assert result.nodes[0].id == f"tool:{digest}"


def test_node_attributes_carry_the_location(classifier) -> None:
    """The merge matches on `(name, source_file)`, so both must be there."""
    layer = classifier(answer("tool"))
    result = layer.classify_file("pkg/mod.py", [symbol("read", line_start=42)])

    assert result.nodes[0].attributes["source_file"] == "pkg/mod.py"
    assert result.nodes[0].attributes["line_start"] == 42


def test_a_method_node_records_its_parent_class(classifier) -> None:
    layer = classifier(answer("tool"))
    result = layer.classify_file("pkg/mod.py", [
        symbol("read", class_name="Reader", parent_kind="tool"),
    ])

    assert result.nodes[0].attributes["parent_class"] == "Reader"


def test_an_empty_batch_asks_nothing(classifier) -> None:
    """No symbols, no request — the caller may pass an empty list."""
    layer = classifier()
    result = layer.classify_file("pkg/mod.py", [])

    assert result.nodes == []
    assert layer._llm_call.calls == []


def test_a_cached_answer_is_served_without_asking(
    classifier, memory_cache
) -> None:
    """The cache is the layer's reproducibility guarantee, in one test."""
    first = classifier(answer("tool"), cache=memory_cache)
    assert first.classify_file("pkg/mod.py", [symbol("read")]).nodes[0].kind == (
        "tool"
    )

    second = classifier(answer("sink"), cache=memory_cache)
    again = second.classify_file("pkg/mod.py", [symbol("read")])

    assert again.nodes[0].kind == "tool"
    assert second._llm_call.calls == []
    assert memory_cache.get(
        content_hash(symbol("read").source, "pkg/mod.py", []),
        PROMPT_VERSION,
        "test/model",
    ).kind == "tool"


def test_an_unusable_answer_is_not_cached(classifier, memory_cache) -> None:
    """A transient failure must not become permanent."""
    layer = classifier("not json at all", cache=memory_cache)
    assert layer.classify_file("pkg/mod.py", [symbol("read")]).nodes == []
    assert memory_cache.count() == 0
