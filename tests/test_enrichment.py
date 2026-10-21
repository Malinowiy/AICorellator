"""Unit tests for framework-detector enrichment (1.2a.2).

Each helper is pure: it takes AST nodes and returns a `FlowDecision`
whose `kind` is one of the six `data_flow_confirmed` values (ADR-0015).
The tests build the nodes from source snippets, which keeps the fixtures
readable and independent of the detectors.

The helpers take the framework name so the decision's `source` records
the producer; the detector passes its own `name`, and these tests pass
the same strings.
"""

from __future__ import annotations

import ast

from aicorellator.parsing.flow.decision import VALID_KINDS, FlowDecision
from aicorellator.parsing.framework_detectors.enrichment import (
    enrich_provides_tool,
    enrich_uses_model,
    enrich_uses_tool,
    resolve_tool_names,
)


# The framework names the detectors pass (their `FrameworkDetector.name`).
LANGCHAIN = "langchain"
FASTMCP = "fastmcp"
MCP_PYTHON = "mcp_python"


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------

def _tree(code: str) -> ast.Module:
    return ast.parse(code)


def _assignments(tree: ast.Module) -> dict[str, ast.expr]:
    """`NAME = expr` anywhere in the tree, later assignments winning."""
    out: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
    return out


def _func(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def _call_keyword(tree: ast.Module, key: str) -> ast.expr:
    """The value of `key=` in the last call that has it."""
    value = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == key:
                    value = kw.value
    assert value is not None
    return value


# The helpers only *produce* decisions; the mapping from a decision to
# `flow_kind` lives in `flow.decision` and is tested in
# `test_flow_decision.py` (step 1 moved it out of this module).


# ------------------------------------------------------------
# enrich_uses_tool
# ------------------------------------------------------------

def test_uses_tool_named_in_prompt() -> None:
    """A tool named in the system prompt is given to the agent."""
    tree = _tree('agent = f(tools=[search], system_prompt="Use search for X")')
    decision = enrich_uses_tool(
        _call_keyword(tree, "tools"),
        "Use search for X",
        _assignments(tree),
        framework=LANGCHAIN,
    )
    assert decision.kind == "parameter_match"
    assert decision.source == "enrichment:langchain"
    assert decision.evidence == ("prompt names search",)


def test_uses_tool_not_named_in_prompt() -> None:
    """A prompt about something else does not enrich the edge."""
    tree = _tree('agent = f(tools=[search], system_prompt="Be helpful")')
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"), "Be helpful", _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "unknown"


def test_uses_tool_without_system_prompt() -> None:
    """No prompt means no evidence: the edge stays unknown."""
    tree = _tree("agent = f(tools=[search])")
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"), None, _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "unknown"
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"), "   ", _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "unknown"


def test_uses_tool_whole_word_match() -> None:
    """`search` is not named by the word "research"."""
    tree = _tree("agent = f(tools=[search])")
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"), "research the topic", _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "unknown"


def test_uses_tool_per_tool_narrowing() -> None:
    """In one call, only the named tool's edge is enriched."""
    tree = _tree(
        'agent = f(tools=[read_ticket, search_docs], '
        'system_prompt="Use read_ticket")'
    )
    tools_arg = _call_keyword(tree, "tools")
    assignments = _assignments(tree)
    prompt = "Use read_ticket"

    assert enrich_uses_tool(
        tools_arg, prompt, assignments,
        framework=LANGCHAIN, tool_name="read_ticket",
    ).kind == "parameter_match"
    assert enrich_uses_tool(
        tools_arg, prompt, assignments,
        framework=LANGCHAIN, tool_name="search_docs",
    ).kind == "unknown"


def test_uses_tool_resolves_symbolic_tool_list() -> None:
    """`tools=TOOLS` is resolved through the assignment table."""
    tree = _tree(
        "TOOLS = [read_ticket, search_docs]\n"
        'agent = f(tools=TOOLS, system_prompt="Call search_docs")'
    )
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"),
        "Call search_docs",
        _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "parameter_match"


def test_uses_tool_cycle_safe() -> None:
    """A cyclic tool list resolves to nothing and stays unknown."""
    tree = _tree('X = Y\nY = X\nagent = f(tools=X, system_prompt="Use X")')
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"), "Use X", _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "unknown"


def test_resolve_tool_names_shapes() -> None:
    """The resolver handles lists, names and tool constructors."""
    tree = _tree(
        "TOOLS = [a, b]\n"
        't = Tool(name="my_tool", func=lambda x: x)\n'
        "s = StructuredTool.from_function(my_function)\n"
    )
    assignments = _assignments(tree)
    assert resolve_tool_names(
        ast.parse("[a, b]").body[0].value, assignments
    ) == ["a", "b"]
    assert resolve_tool_names(
        ast.parse("TOOLS").body[0].value, assignments
    ) == ["a", "b"]
    assert resolve_tool_names(assignments["t"], assignments) == ["my_tool"]
    assert resolve_tool_names(assignments["s"], assignments) == [
        "my_function",
    ]


# ------------------------------------------------------------
# enrich_uses_model
# ------------------------------------------------------------

def test_uses_model_literal() -> None:
    """A literal model name is a literal_only configuration."""
    tree = _tree('llm = ChatOpenAI(model="gpt-4")')
    decision = enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree),
        framework=LANGCHAIN,
    )
    assert decision.kind == "literal_only"
    assert decision.source == "enrichment:langchain"
    assert decision.evidence == ("literal model name",)


def test_uses_model_resolved_module_assignment() -> None:
    """`model=MODEL` resolves to the module-level string."""
    tree = _tree('MODEL = "gpt-4"\nllm = ChatOpenAI(model=MODEL)')
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "literal_only"


def test_uses_model_function_parameter() -> None:
    """A model name that is a caller-supplied parameter is data."""
    tree = _tree(
        "def build(model_name):\n"
        "    llm = ChatOllama(model=model_name)\n"
        "    return llm\n"
    )
    decision = enrich_uses_model(
        _call_keyword(tree, "model"),
        _assignments(tree),
        {"model_name"},
        framework=LANGCHAIN,
    )
    assert decision.kind == "parameter_match"
    assert decision.evidence == ("model from parameter model_name",)


def test_uses_model_unresolved_name_is_unknown() -> None:
    """A name with no assignment in the file stays unknown."""
    tree = _tree("llm = ChatOpenAI(model=MODEL)")
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "unknown"


def test_uses_model_attribute_and_subscript_are_unknown() -> None:
    """Attributes and subscripts are not followed (ADR-0001)."""
    tree = _tree("llm = ChatOpenAI(model=settings.model)")
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "unknown"

    tree = _tree('llm = ChatOpenAI(model=config["model"])')
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "unknown"


def test_uses_model_follows_agent_argument_to_client() -> None:
    """The agent's `model=llm` is followed to the client constructor."""
    tree = _tree(
        'llm = ChatOpenAI(model="gpt-4")\n'
        "agent = create_agent(model=llm, tools=[])\n"
    )
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "literal_only"


def test_uses_model_direct_agent_argument() -> None:
    """`create_agent(model="gpt-4")` is a literal configuration too."""
    tree = _tree('agent = create_agent(model="gpt-4", tools=[])')
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "literal_only"


def test_uses_model_none_is_unknown() -> None:
    """No model argument means no evidence."""
    assert enrich_uses_model(
        None, {}, framework=LANGCHAIN
    ).kind == "unknown"


def test_uses_model_cycle_safe() -> None:
    """A cyclic assignment does not recurse forever."""
    tree = _tree("A = B\nB = A\nllm = ChatOpenAI(model=A)")
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree),
        framework=LANGCHAIN,
    ).kind == "unknown"


# ------------------------------------------------------------
# enrich_provides_tool
# ------------------------------------------------------------

def test_provides_tool_with_parameters() -> None:
    """The MCP client hands the tool its arguments."""
    tree = _tree("def search(query: str, limit: int = 10) -> str: ...")
    decision = enrich_provides_tool(
        _func(tree, "search"), framework=FASTMCP
    )
    assert decision.kind == "parameter_match"
    assert decision.source == "enrichment:fastmcp"
    assert decision.evidence == ("tool declares parameters",)


def test_provides_tool_without_parameters() -> None:
    """A tool with no parameter has nothing to receive."""
    tree = _tree("def health() -> str: ...")
    assert enrich_provides_tool(
        _func(tree, "health"), framework=FASTMCP
    ).kind == "no_args"


def test_provides_tool_ignores_self() -> None:
    """`self` is not a client-supplied argument."""
    tree = _tree("class S:\n    def ping(self) -> str: ...\n")
    assert enrich_provides_tool(
        _func(tree, "ping"), framework=MCP_PYTHON
    ).kind == "no_args"


def test_provides_tool_counts_varargs() -> None:
    """`*args` / `**kwargs` receive caller data too."""
    tree = _tree("def run(*args, **kwargs): ...")
    assert enrich_provides_tool(
        _func(tree, "run"), framework=MCP_PYTHON
    ).kind == "parameter_match"


def test_provides_tool_without_function_node() -> None:
    """A tool built by a constructor has no visible signature."""
    assert enrich_provides_tool(
        None, framework=LANGCHAIN
    ).kind == "unknown"


def test_provides_tool_source_follows_the_framework() -> None:
    """One helper, three detectors: `source` says which one asked."""
    tree = _tree("def search(query: str) -> str: ...")
    tool = _func(tree, "search")

    sources = {
        enrich_provides_tool(tool, framework=framework).source
        for framework in (LANGCHAIN, FASTMCP, MCP_PYTHON)
    }
    assert sources == {
        "enrichment:langchain",
        "enrichment:fastmcp",
        "enrichment:mcp_python",
    }


# ------------------------------------------------------------
# Contract
# ------------------------------------------------------------

def test_all_helpers_return_contract_values() -> None:
    """The helpers never return a value outside the six (ADR-0015)."""
    tree = _tree(
        'MODEL = "gpt-4"\n'
        "def build(model_name):\n"
        "    llm = ChatOpenAI(model=model_name)\n"
        "    return llm\n"
    )
    model_arg = _call_keyword(tree, "model")
    assignments = _assignments(tree)
    tool_name_node = ast.parse("search").body[0].value

    decisions = {
        enrich_uses_model(model_arg, assignments, set(),
                          framework=LANGCHAIN),
        enrich_uses_model(model_arg, assignments, {"model_name"},
                          framework=LANGCHAIN),
        enrich_uses_model(None, {}, framework=LANGCHAIN),
        enrich_uses_tool(None, None, {}, framework=LANGCHAIN),
        enrich_uses_tool(tool_name_node, "Use search", assignments,
                         framework=LANGCHAIN),
        enrich_uses_tool(tool_name_node, None, assignments,
                         framework=LANGCHAIN),
        enrich_provides_tool(None, framework=FASTMCP),
        enrich_provides_tool(_func(tree, "build"), framework=MCP_PYTHON),
    }
    assert {d.kind for d in decisions} <= VALID_KINDS
    # Every decision is a FlowDecision, so the type is the contract: a
    # helper cannot return an arbitrary string.
    assert all(isinstance(d, FlowDecision) for d in decisions)
