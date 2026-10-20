"""Unit tests for framework-detector enrichment (1.2a.2).

Each helper is pure: it takes AST nodes and returns one of the six
`data_flow_confirmed` values (ADR-0015). The tests build the nodes from
source snippets, which keeps the fixtures readable and independent of
the detectors.
"""

from __future__ import annotations

import ast

from aicorellator.parsing.flow_analyzer import DATA_FLOW_CONFIRMED_VALUES
from aicorellator.parsing.framework_detectors.enrichment import (
    enrich_provides_tool,
    enrich_uses_model,
    enrich_uses_tool,
    flow_kind_for,
    resolve_tool_names,
)


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


# ------------------------------------------------------------
# flow_kind_for
# ------------------------------------------------------------

def test_flow_kind_for_data_values() -> None:
    """The three data values imply flow_kind=data (decision 3)."""
    assert flow_kind_for("parameter_match") == "data"
    assert flow_kind_for("local_var_match") == "data"
    assert flow_kind_for("call_result_match") == "data"


def test_flow_kind_for_control_values() -> None:
    """Non-data values — and a missing value — stay control."""
    assert flow_kind_for("literal_only") == "control"
    assert flow_kind_for("no_args") == "control"
    assert flow_kind_for("unknown") == "control"
    assert flow_kind_for(None) == "control"


# ------------------------------------------------------------
# enrich_uses_tool
# ------------------------------------------------------------

def test_uses_tool_named_in_prompt() -> None:
    """A tool named in the system prompt is given to the agent."""
    tree = _tree('agent = f(tools=[search], system_prompt="Use search for X")')
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"),
        "Use search for X",
        _assignments(tree),
    ) == "parameter_match"


def test_uses_tool_not_named_in_prompt() -> None:
    """A prompt about something else does not enrich the edge."""
    tree = _tree('agent = f(tools=[search], system_prompt="Be helpful")')
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"), "Be helpful", _assignments(tree)
    ) == "unknown"


def test_uses_tool_without_system_prompt() -> None:
    """No prompt means no evidence: the edge stays unknown."""
    tree = _tree("agent = f(tools=[search])")
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"), None, _assignments(tree)
    ) == "unknown"
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"), "   ", _assignments(tree)
    ) == "unknown"


def test_uses_tool_whole_word_match() -> None:
    """`search` is not named by the word "research"."""
    tree = _tree("agent = f(tools=[search])")
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"), "research the topic", _assignments(tree)
    ) == "unknown"


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
        tools_arg, prompt, assignments, tool_name="read_ticket"
    ) == "parameter_match"
    assert enrich_uses_tool(
        tools_arg, prompt, assignments, tool_name="search_docs"
    ) == "unknown"


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
    ) == "parameter_match"


def test_uses_tool_cycle_safe() -> None:
    """A cyclic tool list resolves to nothing and stays unknown."""
    tree = _tree('X = Y\nY = X\nagent = f(tools=X, system_prompt="Use X")')
    assert enrich_uses_tool(
        _call_keyword(tree, "tools"), "Use X", _assignments(tree)
    ) == "unknown"


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
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree)
    ) == "literal_only"


def test_uses_model_resolved_module_assignment() -> None:
    """`model=MODEL` resolves to the module-level string."""
    tree = _tree('MODEL = "gpt-4"\nllm = ChatOpenAI(model=MODEL)')
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree)
    ) == "literal_only"


def test_uses_model_function_parameter() -> None:
    """A model name that is a caller-supplied parameter is data."""
    tree = _tree(
        "def build(model_name):\n"
        "    llm = ChatOllama(model=model_name)\n"
        "    return llm\n"
    )
    assert enrich_uses_model(
        _call_keyword(tree, "model"),
        _assignments(tree),
        {"model_name"},
    ) == "parameter_match"


def test_uses_model_unresolved_name_is_unknown() -> None:
    """A name with no assignment in the file stays unknown."""
    tree = _tree("llm = ChatOpenAI(model=MODEL)")
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree)
    ) == "unknown"


def test_uses_model_attribute_and_subscript_are_unknown() -> None:
    """Attributes and subscripts are not followed (ADR-0001)."""
    tree = _tree("llm = ChatOpenAI(model=settings.model)")
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree)
    ) == "unknown"

    tree = _tree('llm = ChatOpenAI(model=config["model"])')
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree)
    ) == "unknown"


def test_uses_model_follows_agent_argument_to_client() -> None:
    """The agent's `model=llm` is followed to the client constructor."""
    tree = _tree(
        'llm = ChatOpenAI(model="gpt-4")\n'
        "agent = create_agent(model=llm, tools=[])\n"
    )
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree)
    ) == "literal_only"


def test_uses_model_direct_agent_argument() -> None:
    """`create_agent(model="gpt-4")` is a literal configuration too."""
    tree = _tree('agent = create_agent(model="gpt-4", tools=[])')
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree)
    ) == "literal_only"


def test_uses_model_none_is_unknown() -> None:
    """No model argument means no evidence."""
    assert enrich_uses_model(None, {}) == "unknown"


def test_uses_model_cycle_safe() -> None:
    """A cyclic assignment does not recurse forever."""
    tree = _tree("A = B\nB = A\nllm = ChatOpenAI(model=A)")
    assert enrich_uses_model(
        _call_keyword(tree, "model"), _assignments(tree)
    ) == "unknown"


# ------------------------------------------------------------
# enrich_provides_tool
# ------------------------------------------------------------

def test_provides_tool_with_parameters() -> None:
    """The MCP client hands the tool its arguments."""
    tree = _tree("def search(query: str, limit: int = 10) -> str: ...")
    assert enrich_provides_tool(_func(tree, "search")) == "parameter_match"


def test_provides_tool_without_parameters() -> None:
    """A tool with no parameter has nothing to receive."""
    tree = _tree("def health() -> str: ...")
    assert enrich_provides_tool(_func(tree, "health")) == "no_args"


def test_provides_tool_ignores_self() -> None:
    """`self` is not a client-supplied argument."""
    tree = _tree("class S:\n    def ping(self) -> str: ...\n")
    assert enrich_provides_tool(_func(tree, "ping")) == "no_args"


def test_provides_tool_counts_varargs() -> None:
    """`*args` / `**kwargs` receive caller data too."""
    tree = _tree("def run(*args, **kwargs): ...")
    assert enrich_provides_tool(_func(tree, "run")) == "parameter_match"


def test_provides_tool_without_function_node() -> None:
    """A tool built by a constructor has no visible signature."""
    assert enrich_provides_tool(None) == "unknown"


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

    values = {
        enrich_uses_model(model_arg, assignments, set()),
        enrich_uses_model(model_arg, assignments, {"model_name"}),
        enrich_uses_model(None, {}),
        enrich_uses_tool(None, None, {}),
        enrich_uses_tool(tool_name_node, "Use search", assignments),
        enrich_uses_tool(tool_name_node, None, assignments),
        enrich_provides_tool(None),
        enrich_provides_tool(_func(tree, "build")),
    }
    assert values <= DATA_FLOW_CONFIRMED_VALUES
