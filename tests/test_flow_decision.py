"""Unit tests for `FlowDecision` (step 1, architectural repair).

Two things are pinned here:

  1. the value object itself — validation, `is_data`, the
     `data_flow_confirmed` → `flow_kind` mapping (ADR-0015 decision 3),
     and the provenance fields;
  2. **who decides** — each producer returns a decision whose `source`
     names it. The kind values are asserted in the analyzers' own test
     files (`test_flow_edge_analyzer.py`, `test_flow_sink_analyzer.py`,
     `test_enrichment.py`); this file asserts that a value came from the
     producer claimed to have produced it.
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError

import pytest

from aicorellator.parsing.flow.decision import (
    DATA_FLOW_CONFIRMED_VALUES,
    DATA_FLOW_DATA_VALUES,
    DATA_KINDS,
    VALID_KINDS,
    FlowDecision,
    flow_kind_for,
)
from aicorellator.parsing.flow import (
    analyze_has_access_to,
    analyze_sink_body,
    analyze_sink_class,
)
from aicorellator.parsing.framework_detectors.enrichment import (
    enrich_provides_tool,
    enrich_uses_tool,
)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _func(source: str):
    """The first function definition in `source`."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise AssertionError("no function found")


def _cls(source: str) -> ast.ClassDef:
    """The first class definition in `source`."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            return node
    raise AssertionError("no class found")


def _call(func, name: str) -> ast.Call:
    """The call to `name` (last dotted segment) inside `func`."""
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            target = node.func
            attr = target.attr if isinstance(target, ast.Attribute) else None
            ident = target.id if isinstance(target, ast.Name) else None
            if name in (attr, ident):
                return node
    raise AssertionError(f"no call to {name} found")


# ------------------------------------------------------------
# The value object
# ------------------------------------------------------------

def test_valid_kinds_are_accepted() -> None:
    """Every value of the six builds a decision."""
    for kind in VALID_KINDS:
        assert FlowDecision(kind).kind == kind


def test_unknown_kind_is_rejected() -> None:
    """A value outside the six is a programming error, not a value."""
    with pytest.raises(ValueError):
        FlowDecision("bogus")


def test_is_data_true_for_the_three_data_kinds() -> None:
    assert FlowDecision("parameter_match").is_data() is True
    assert FlowDecision("local_var_match").is_data() is True
    assert FlowDecision("call_result_match").is_data() is True


def test_is_data_false_for_the_other_three() -> None:
    assert FlowDecision("literal_only").is_data() is False
    assert FlowDecision("no_args").is_data() is False
    assert FlowDecision("unknown").is_data() is False


def test_flow_kind_for_data_kinds() -> None:
    """Data kinds map to `data` (ADR-0015, decision 3)."""
    for kind in DATA_KINDS:
        assert flow_kind_for(FlowDecision(kind)) == "data"


def test_flow_kind_for_control_kinds() -> None:
    """Everything else — including `unknown` — maps to `control`."""
    for kind in VALID_KINDS - DATA_KINDS:
        assert flow_kind_for(FlowDecision(kind)) == "control"


def test_evidence_and_source_are_preserved() -> None:
    decision = FlowDecision(
        "parameter_match",
        ("file_data.filename",),
        source="sink_body",
    )
    assert decision.kind == "parameter_match"
    assert decision.evidence == ("file_data.filename",)
    assert decision.source == "sink_body"


def test_defaults_are_empty() -> None:
    """A decision with nothing to say about provenance is still valid."""
    decision = FlowDecision("unknown")
    assert decision.evidence == ()
    assert decision.source == ""


def test_decisions_are_frozen() -> None:
    """A decision is a statement about one analysis, not a record."""
    decision = FlowDecision("no_args")
    with pytest.raises(FrozenInstanceError):
        decision.kind = "parameter_match"  # type: ignore[misc]


def test_decisions_are_hashable_and_comparable() -> None:
    """Equal decisions collapse; different ones do not."""
    assert FlowDecision("unknown", source="sink_body") == FlowDecision(
        "unknown", source="sink_body"
    )
    assert len({FlowDecision("unknown"), FlowDecision("no_args")}) == 2



# ============================================================
# Constants
# ============================================================

def test_confirmed_values_are_the_six() -> None:
    assert DATA_FLOW_CONFIRMED_VALUES == {
        "parameter_match", "local_var_match", "call_result_match",
        "literal_only", "no_args", "unknown",
    }


def test_data_values_are_the_three_data_kinds() -> None:
    assert DATA_FLOW_DATA_VALUES == {
        "parameter_match", "local_var_match", "call_result_match",
    }
    assert DATA_FLOW_DATA_VALUES <= DATA_FLOW_CONFIRMED_VALUES

# ------------------------------------------------------------
# Provenance: who decided
# ------------------------------------------------------------

def test_edge_analyzer_source() -> None:
    """The edge question is answered by the edge analyzer."""
    func = _func("def read_file(query):\n    os.remove(query)\n")
    decision = analyze_has_access_to(func, _call(func, "remove"))
    assert decision.source == "edge_analyzer"
    assert decision.kind == "parameter_match"
    assert decision.evidence == ("query",)


def test_edge_analyzer_no_args_source() -> None:
    """A no-argument sink call is still the edge analyzer's answer."""
    func = _func("def run():\n    os.remove()\n")
    decision = analyze_has_access_to(func, _call(func, "remove"))
    assert decision.kind == "no_args"
    assert decision.source == "edge_analyzer"


def test_sink_body_source() -> None:
    """The sink-body question is answered by the sink-body analyzer."""
    func = _func("def delete_file(file_id):\n    self.db.delete(file_id)\n")
    decision = analyze_sink_body(func)
    assert decision.source == "sink_body"
    assert decision.kind == "parameter_match"
    assert decision.evidence == ("file_id",)


def test_sink_body_unknown_source() -> None:
    """No dangerous call: the answer is still the sink analyzer's."""
    func = _func("def helper():\n    return 1\n")
    decision = analyze_sink_body(func)
    assert decision.kind == "unknown"
    assert decision.source == "sink_body"
    assert decision.evidence == ()


def test_sink_class_source_and_scope_evidence() -> None:
    """A class decision names the method that decided."""
    cls = _cls(
        "class Repo:\n"
        "    def other(self):\n"
        "        return 1\n"
        "    def claim_username(self, username):\n"
        "        self.db.delete(username)\n"
    )
    decision = analyze_sink_class(cls)
    assert decision.source == "sink_class"
    assert decision.kind == "parameter_match"
    assert decision.evidence == ("method:claim_username", "username")


def test_sink_class_unknown_source() -> None:
    cls = _cls("class Empty:\n    pass\n")
    decision = analyze_sink_class(cls)
    assert decision.kind == "unknown"
    assert decision.source == "sink_class"


def test_enrichment_uses_tool_source() -> None:
    """The LangChain prompt rule names the framework it reasons about."""
    tree = ast.parse(
        'agent = f(tools=[search], system_prompt="Use search")'
    )
    tools_arg = tree.body[0].value.keywords[0].value
    decision = enrich_uses_tool(
        tools_arg, "Use search", {}, framework="langchain"
    )
    assert decision.source == "enrichment:langchain"
    assert decision.kind == "parameter_match"


def test_enrichment_provides_tool_source_is_the_calling_framework() -> None:
    """The MCP rule is shared, so the caller names the framework."""
    tool = _func("def search(query: str) -> str: ...")
    fastmcp = enrich_provides_tool(tool, framework="fastmcp")
    mcp_python = enrich_provides_tool(tool, framework="mcp_python")

    assert fastmcp.source == "enrichment:fastmcp"
    assert mcp_python.source == "enrichment:mcp_python"
    # Same rule, same answer — only the provenance differs.
    assert fastmcp.kind == mcp_python.kind == "parameter_match"
