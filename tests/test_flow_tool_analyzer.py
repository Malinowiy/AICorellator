"""Unit tests for `flow.tool_analyzer` — the tool question (1.2a.1).

A tool that returns (or yields) data can carry the agent's data onward,
so the answer is a `bool` published as the node's `returns_data`. The
analyzer is pure: these tests build AST nodes from source strings and
assert the boolean. No filesystem, no TSA subprocess.
"""

from __future__ import annotations

import ast
import textwrap

from aicorellator.parsing.flow.arguments import FunctionNode
from aicorellator.parsing.flow.tool_analyzer import analyze_tool_returns


# ============================================================
# Helpers
# ============================================================

def _func(source: str) -> FunctionNode:
    """Parse `source` and return its first function definition."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise AssertionError("no function found in source")


# ============================================================
# Tool return analyzer (1.2a.1)
# ============================================================

def _returns(source: str) -> bool:
    """Analyze the first function in `source` as a tool body."""
    return analyze_tool_returns(_func(source))


def test_tool_returns_data() -> None:
    src = """
    def read_file(query):
        return search(query)
    """
    assert _returns(src) is True


def test_tool_returns_none_is_false() -> None:
    src = """
    def read_file(query):
        log(query)
        return None
    """
    assert _returns(src) is False


def test_tool_bare_return_is_false() -> None:
    src = """
    def read_file(query):
        log(query)
        return
    """
    assert _returns(src) is False


def test_tool_without_return_is_false() -> None:
    src = """
    def read_file(query):
        log(query)
    """
    assert _returns(src) is False


def test_tool_yield_is_true() -> None:
    """A generator hands data to its caller."""
    src = """
    def read_file(query):
        yield query
    """
    assert _returns(src) is True


def test_tool_yield_from_is_true() -> None:
    src = """
    def read_file(query):
        yield from query
    """
    assert _returns(src) is True


def test_tool_conditional_return_with_data_is_true() -> None:
    """One data return is enough, even when another branch returns None."""
    src = """
    def read_file(query):
        if query:
            return search(query)
        return None
    """
    assert _returns(src) is True


def test_tool_async_return_is_true() -> None:
    src = """
    async def read_file(query):
        return await search(query)
    """
    assert _returns(src) is True
