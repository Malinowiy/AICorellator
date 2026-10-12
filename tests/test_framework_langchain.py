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