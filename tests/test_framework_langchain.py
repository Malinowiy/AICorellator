"""Unit tests for LangChain detector."""

from __future__ import annotations

import ast

from aicorellator.parsing.framework_detectors.langchain import LangChainDetector


def _detect(code: str):
    tree = ast.parse(code)
    return LangChainDetector().detect("test.py", tree, code)


def test_no_langchain_import_returns_empty() -> None:
    result = _detect("x = 1\n")
    assert result.nodes == []
    assert result.edges == []


def test_detect_agent_executor() -> None:
    result = _detect("""
from langchain.agents import AgentExecutor, initialize_agent
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4")
agent = AgentExecutor(agent=..., tools=[], llm=llm)
""")
    kinds = {n.kind for n in result.nodes}
    assert "agent" in kinds
    assert "llm_endpoint" in kinds
    agent_names = {n.name for n in result.nodes if n.kind == "agent"}
    assert "AgentExecutor" in agent_names
    llm_names = {n.name for n in result.nodes if n.kind == "llm_endpoint"}
    assert "ChatOpenAI" in llm_names


def test_detect_initialize_agent() -> None:
    result = _detect("""
from langchain.agents import initialize_agent
agent = initialize_agent(tools=[], llm=None)
""")
    agent_names = {n.name for n in result.nodes if n.kind == "agent"}
    assert "initialize_agent" in agent_names


def test_detect_tool_decorator() -> None:
    result = _detect("""
from langchain.tools import tool

@tool
def search(query: str) -> str:
    return ""
""")
    tool_names = {n.name for n in result.nodes if n.kind == "tool"}
    assert "search" in tool_names


def test_detect_tool_instance() -> None:
    result = _detect("""
from langchain.tools import Tool, StructuredTool

t = Tool(name="my_tool", func=lambda x: x, description="...")
s = StructuredTool.from_function(my_function)
""")
    tool_names = {n.name for n in result.nodes if n.kind == "tool"}
    assert "my_tool" in tool_names
    assert "my_function" in tool_names


def test_detect_llm_providers() -> None:
    result = _detect("""
from langchain_anthropic import ChatAnthropic
from langchain_community.llms import Ollama

a = ChatAnthropic(model="claude")
o = Ollama(model="llama")
""")
    providers = {
        n.attributes.get("provider")
        for n in result.nodes
        if n.kind == "llm_endpoint"
    }
    assert "anthropic" in providers
    assert "ollama" in providers


def test_provenance_in_attributes() -> None:
    result = _detect("""
from langchain.agents import AgentExecutor
a = AgentExecutor(agent=..., tools=[])
""")
    for node in result.nodes:
        assert node.attributes.get("framework") == "langchain"

def test_uses_model_from_local_assignment() -> None:
    """LLM assigned inside a function is resolved."""
    result = _detect("""
from langchain.agents import create_agent
from langchain_ollama import ChatOllama

def get_agent(role):
    llm = ChatOllama(model="llama3")
    return create_agent(model=llm, tools=[])
""")
    uses_model = [e for e in result.edges if e.kind == "uses_model"]
    assert len(uses_model) == 1
    # The model was configured with a literal name. The detector knows
    # that, but the AST edge records only the control relationship
    # (ADR-0016, decision 3).
    assert uses_model[0].flow_kind == "control"
    assert uses_model[0].data_flow_confirmed is None


def test_uses_tool_from_local_assignment() -> None:
    """Tool list assigned inside a function is resolved."""
    result = _detect("""
from langchain.agents import create_agent
from langchain.tools import tool

@tool
def search(q: str) -> str:
    return ""

def get_agent():
    tools = [search]
    return create_agent(tools=tools, model=None)
""")
    uses_tool = [e for e in result.edges if e.kind == "uses_tool"]
    assert len(uses_tool) == 1
    # ADR-0015: control flow. ADR-0016: no data-flow verdict.
    assert uses_tool[0].flow_kind == "control"
    assert uses_tool[0].data_flow_confirmed is None


def test_assignment_later_wins() -> None:
    """When two assignments share a name, the later one is used."""
    result = _detect("""
from langchain.agents import create_agent
from langchain_ollama import ChatOllama

llm = ChatOllama(model="first")
llm = ChatOllama(model="second")
agent = create_agent(model=llm, tools=[])
""")
    uses_model = [e for e in result.edges if e.kind == "uses_model"]
    assert len(uses_model) == 1

def test_resolve_tool_names_cycle_safe() -> None:
    """Cyclic assignments do not cause infinite recursion."""
    result = _detect("""
from langchain.agents import create_agent

X = Y
Y = X
agent = create_agent(tools=X, model=None)
""")
    # No crash; no edges because X/Y are not resolvable to tools.
    uses_tool = [e for e in result.edges if e.kind == "uses_tool"]
    assert uses_tool == []


def test_resolve_llm_names_cycle_safe() -> None:
    """Cyclic LLM assignment does not cause infinite recursion."""
    result = _detect("""
from langchain.agents import create_agent

A = B
B = A
agent = create_agent(model=A, tools=[])
""")
    uses_model = [e for e in result.edges if e.kind == "uses_model"]
    assert uses_model == []


# ------------------------------------------------------------
# ADR-0016 — the detector reads the source, the edge stays control
# ------------------------------------------------------------
#
# Each case below is a question the detector answers with confidence —
# "the prompt names this tool", "the model name is a literal", "the model
# name is a caller-supplied parameter". The answer is framework knowledge
# for Layer 2; none of it is written to an AST edge, so every edge here
# is `control` with `data_flow_confirmed = NULL`.

def _tool_edges(result):
    """Map the uses_tool edges by target tool name."""
    names = {n.id: n.name for n in result.nodes}
    return {
        names[e.target_id]: e
        for e in result.edges
        if e.kind == "uses_tool"
    }


def test_uses_tool_named_in_system_prompt_is_still_control() -> None:
    """The prompt names `a` — and neither edge claims data flow.

    This is the detector's strongest reading of the source: the system
    prompt mentions one of the two tools. It still is not a data-flow
    confirmation, because nothing ran (ADR-0016, decision 3).
    """
    result = _detect("""
from langchain.agents import create_agent
from langchain.tools import tool

@tool
def a(x: str) -> str:
    return x

@tool
def b(x: str) -> str:
    return x

agent = create_agent(tools=[a, b], system_prompt="Use a for X")
""")
    edges = _tool_edges(result)
    assert edges["a"].flow_kind == "control"
    assert edges["a"].data_flow_confirmed is None
    assert edges["b"].flow_kind == "control"
    assert edges["b"].data_flow_confirmed is None


def test_uses_tool_without_system_prompt_stays_control() -> None:
    """No prompt is no evidence."""
    result = _detect("""
from langchain.agents import create_agent
from langchain.tools import tool

@tool
def a(x: str) -> str:
    return x

agent = create_agent(tools=[a])
""")
    edges = _tool_edges(result)
    assert edges["a"].flow_kind == "control"
    assert edges["a"].data_flow_confirmed is None


def test_uses_tool_unrelated_system_prompt_stays_control() -> None:
    """A prompt that does not name the tool stays control."""
    result = _detect("""
from langchain.agents import create_agent
from langchain.tools import tool

@tool
def a(x: str) -> str:
    return x

agent = create_agent(tools=[a], system_prompt="Do something unrelated")
""")
    edges = _tool_edges(result)
    assert edges["a"].flow_kind == "control"
    assert edges["a"].data_flow_confirmed is None


def test_uses_model_literal_is_literal_only() -> None:
    """`ChatOpenAI(model="gpt-4")` is a constant configuration."""
    result = _detect("""
from langchain.agents import AgentExecutor
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4")
agent = AgentExecutor(agent=..., tools=[], llm=llm)
""")
    uses_model = [e for e in result.edges if e.kind == "uses_model"]
    assert len(uses_model) == 1
    assert uses_model[0].flow_kind == "control"
    assert uses_model[0].data_flow_confirmed is None


def test_uses_model_resolved_module_assignment_is_literal_only() -> None:
    """`model=model_var` where `model_var = "gpt-4"` resolves to a literal."""
    result = _detect("""
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

model_var = "gpt-4"
llm = ChatOpenAI(model=model_var)
agent = create_agent(model=llm, tools=[])
""")
    uses_model = [e for e in result.edges if e.kind == "uses_model"]
    assert len(uses_model) == 1
    assert uses_model[0].flow_kind == "control"
    assert uses_model[0].data_flow_confirmed is None


def test_uses_model_from_function_parameter_stays_control() -> None:
    """A caller-supplied model name is the tempting `parameter_match`.

    Same shape as the Flow Analyzer's data case — the value arrives from
    outside — but the AST layer did not see it arrive, so the edge stays
    control with no verdict (ADR-0016, decision 3).
    """
    result = _detect("""
from langchain.agents import create_agent
from langchain_ollama import ChatOllama

def get_agent(model_name):
    llm = ChatOllama(model=model_name)
    return create_agent(model=llm, tools=[])
""")
    uses_model = [e for e in result.edges if e.kind == "uses_model"]
    assert len(uses_model) == 1
    assert uses_model[0].flow_kind == "control"
    assert uses_model[0].data_flow_confirmed is None


def test_tool_node_records_signature_data_flow() -> None:
    """Tool nodes carry the signature result for the normalizer."""
    result = _detect("""
from langchain.tools import tool

@tool
def with_args(x: str) -> str:
    return x

@tool
def without_args() -> str:
    return "x"
""")
    tools = {n.name: n for n in result.nodes if n.kind == "tool"}
    assert tools["with_args"].attributes["tool_data_flow"] == "parameter_match"
    assert tools["without_args"].attributes["tool_data_flow"] == "no_args"