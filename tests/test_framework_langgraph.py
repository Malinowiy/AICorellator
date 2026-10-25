"""Unit tests for LangGraph detector."""

from __future__ import annotations

import ast

from aicorellator.parsing.framework_detectors.langgraph import LangGraphDetector


def _detect(code: str):
    tree = ast.parse(code)
    return LangGraphDetector().detect("test.py", tree, code)


def test_no_langgraph_import_returns_empty() -> None:
    result = _detect("x = 1\n")
    assert result.nodes == []
    assert result.edges == []


def test_detect_simple_graph() -> None:
    result = _detect("""
from langgraph.graph import StateGraph

graph = StateGraph(dict)
graph.add_node("agent", agent_fn)
graph.add_node("tools", tool_fn)
graph.add_edge("agent", "tools")
graph.add_edge("tools", "agent")
""")
    node_names = {n.name for n in result.nodes}
    assert "agent" in node_names
    assert "tools" in node_names

    edge_pairs = {(e.source_id, e.target_id) for e in result.edges}
    assert len(edge_pairs) == 2
    # ADR-0015: delegates_to is control flow. ADR-0016: no verdict.
    assert all(e.flow_kind == "control" for e in result.edges)
    assert all(e.data_flow_confirmed is None for e in result.edges)


def test_node_kinds_inferred() -> None:
    result = _detect("""
from langgraph.graph import StateGraph

graph = StateGraph(dict)
graph.add_node("agent", a)
graph.add_node("tools", t)
""")
    kinds = {n.name: n.kind for n in result.nodes}
    assert kinds["agent"] == "agent"
    assert kinds["tools"] == "tool"


def test_entry_point_marked() -> None:
    result = _detect("""
from langgraph.graph import StateGraph

graph = StateGraph(dict)
graph.add_node("agent", a)
graph.set_entry_point("agent")
""")
    agent_node = next(n for n in result.nodes if n.name == "agent")
    assert agent_node.attributes.get("entry_point") == "true"


def test_conditional_edges_list() -> None:
    result = _detect("""
from langgraph.graph import StateGraph

graph = StateGraph(dict)
graph.add_node("agent", a)
graph.add_node("tools", t)
graph.add_node("end", e)
graph.add_conditional_edges("agent", router, ["tools", "end"])
""")
    edge_pairs = {(e.source_id, e.target_id) for e in result.edges}
    assert len(edge_pairs) == 2


def test_conditional_edges_dict() -> None:
    result = _detect("""
from langgraph.graph import StateGraph

graph = StateGraph(dict)
graph.add_node("agent", a)
graph.add_node("tools", t)
graph.add_node("end", e)
graph.add_conditional_edges("agent", router, {"continue": "tools", "stop": "end"})
""")
    edge_pairs = {(e.source_id, e.target_id) for e in result.edges}
    assert len(edge_pairs) == 2


def test_provenance_in_attributes() -> None:
    result = _detect("""
from langgraph.graph import StateGraph

graph = StateGraph(dict)
graph.add_node("agent", a)
""")
    for node in result.nodes:
        assert node.attributes.get("framework") == "langgraph"