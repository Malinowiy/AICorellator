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
    # ADR-0015: control flow, data flow unconfirmed.
    assert uses_model[0].flow_kind == "control"
    assert uses_model[0].data_flow_confirmed == "unknown"


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
    # ADR-0015: control flow, data flow unconfirmed.
    assert uses_tool[0].flow_kind == "control"
    assert uses_tool[0].data_flow_confirmed == "unknown"


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