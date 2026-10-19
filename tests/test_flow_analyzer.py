"""Unit tests for the Flow Analyzer (ADR-0015).

The analyzer is pure: these tests build AST nodes from source strings
and assert the returned `data_flow_confirmed` value. No filesystem, no
TSA subprocess.
"""

from __future__ import annotations

import ast
import textwrap

from aicorellator.parsing.flow_analyzer import (
    DATA_FLOW_CONFIRMED_VALUES,
    DATA_FLOW_DATA_VALUES,
    FunctionNode,
    analyze_has_access_to,
)


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


def _sink_call(func: FunctionNode, name: str) -> ast.Call:
    """Find the call to `name` (matched on the last dotted segment)."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == name:
            return node
        if isinstance(node.func, ast.Attribute) and node.func.attr == name:
            return node
    raise AssertionError(f"no call to {name} found")


def _analyze(source: str, sink: str = "os.remove") -> str:
    """Analyze the call to `sink` inside the function in `source`."""
    func = _func(source)
    call = _sink_call(func, sink.split(".")[-1])
    return analyze_has_access_to(func, call)


# ============================================================
# The six values
# ============================================================

def test_parameter_match() -> None:
    """An argument that is a tool parameter is a direct match."""
    src = """
    def read_file(query):
        os.remove(query)
    """
    assert _analyze(src) == "parameter_match"


def test_local_var_match() -> None:
    """An argument assigned in the body is a local-variable match."""
    src = """
    def purge():
        path = fetch_path()
        os.remove(path)
    """
    assert _analyze(src) == "local_var_match"


def test_local_var_match_for_loop_target() -> None:
    """`for` targets are locals (Store context)."""
    src = """
    def purge(files):
        for f in files:
            os.remove(f)
    """
    assert _analyze(src) == "local_var_match"


def test_call_result_match() -> None:
    """An argument that is itself a call result is inferential."""
    src = """
    def purge():
        os.remove(fetch_path())
    """
    assert _analyze(src) == "call_result_match"


def test_call_result_match_await() -> None:
    """`await foo()` is still a call result."""
    src = """
    async def purge():
        os.remove(await fetch_path())
    """
    assert _analyze(src) == "call_result_match"


def test_literal_only() -> None:
    """A literal argument is a literal-only match."""
    src = """
    def purge():
        os.remove("/tmp/scratch")
    """
    assert _analyze(src) == "literal_only"


def test_no_args() -> None:
    """A sink call with no arguments is `no_args`, not `unknown`."""
    src = """
    def purge():
        delete_all()
    """
    assert _analyze(src, "delete_all") == "no_args"


def test_unknown_attribute_access() -> None:
    """Attribute access cannot be followed by name-based analysis."""
    src = """
    def purge(config):
        os.remove(config.path)
    """
    assert _analyze(src) == "unknown"


# ============================================================
# Edge cases
# ============================================================

def test_empty_args_is_no_args() -> None:
    """Zero args and zero keywords → `no_args`."""
    src = """
    def purge():
        os.remove()
    """
    assert _analyze(src) == "no_args"


def test_nested_attribute_call_is_call_result() -> None:
    """`config.get("path")` is a call result, not a parameter match."""
    src = """
    def purge(config):
        os.remove(config.get("path"))
    """
    assert _analyze(src) == "call_result_match"


def test_nested_subscript_is_unknown() -> None:
    """A subscript of a parameter cannot be resolved → unknown."""
    src = """
    def purge(config):
        os.remove(config["path"])
    """
    assert _analyze(src) == "unknown"


def test_fstring_is_unknown() -> None:
    """Interpolated f-strings are not literals (ADR-0015)."""
    src = """
    def purge(base, name):
        os.remove(f"{base}/{name}")
    """
    assert _analyze(src) == "unknown"


def test_strongest_result_wins() -> None:
    """A parameter beats a coexisting literal."""
    src = """
    def purge(query):
        os.remove("/tmp/scratch", query)
    """
    assert _analyze(src) == "parameter_match"


def test_mixed_literal_and_unknown_is_unknown() -> None:
    """`literal_only` is a universal: one unresolved argument defeats it.

    `os.remove(config.path, "/tmp/scratch")` may still route data through
    `config.path`, so the result cannot claim "literals only".
    """
    src = """
    def purge(config):
        os.remove(config.path, "/tmp/scratch")
    """
    assert _analyze(src) == "unknown"


def test_mixed_literal_and_subscript_is_unknown() -> None:
    """A subscript alongside a literal is likewise not `literal_only`."""
    src = """
    def purge(config):
        os.remove(config["a"], "/tmp/x")
    """
    assert _analyze(src) == "unknown"


def test_all_literals_is_literal_only() -> None:
    """Every argument a literal → `literal_only`."""
    src = """
    def purge():
        os.remove("/tmp/a", "/tmp/b")
    """
    assert _analyze(src) == "literal_only"


def test_literal_with_call_result_is_call_result() -> None:
    """A call result outranks a coexisting literal."""
    src = """
    def purge():
        os.remove("/tmp/scratch", fetch_path())
    """
    assert _analyze(src) == "call_result_match"


def test_literal_with_local_var_is_local_var() -> None:
    """A local variable outranks a coexisting literal."""
    src = """
    def purge():
        path = fetch_path()
        os.remove("/tmp/scratch", path)
    """
    assert _analyze(src) == "local_var_match"


def test_keyword_argument_parameter() -> None:
    """Keyword-argument values are classified like positional ones."""
    src = """
    def purge(query):
        os.remove(path=query)
    """
    assert _analyze(src) == "parameter_match"


def test_self_is_not_a_parameter() -> None:
    """`self` is the bound instance, not attacker-supplied data."""
    src = """
    def purge(self):
        os.remove(self)
    """
    assert _analyze(src) == "unknown"


def test_global_name_is_unknown() -> None:
    """A module-level constant is not a parameter or a local."""
    src = """
    def purge():
        os.remove(DEFAULT_PATH)
    """
    assert _analyze(src) == "unknown"


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
