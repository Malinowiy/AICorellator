"""Framework-detector enrichment of `data_flow_confirmed` (1.2a.2).

ADR-0015 decision 7 declares `data_flow_confirmed` **enrichable**: a
framework detector knows its own framework's argument-passing rules, so
it may upgrade the values the generic Flow Analyzer leaves at
`unknown`. This module holds the enrichment rules — small, pure
functions over AST nodes, one per detector question.

Why here and not in `parsing/flow_analyzer.py`: the Flow Analyzer
answers the framework-independent question "how does data reach a sink
call inside this body?". These helpers answer framework-specific
questions — "does the agent's system prompt name this tool?", "was this
LLM configured with a literal model name?", "does this MCP tool accept
parameters?". Keeping them next to the detectors preserves that
separation: the generic analyzer stays free of framework vocabulary,
and each detector owns its own enrichment rule (ADR-0011).

The rules are name-based, one step deep (ADR-0001). They never follow
values through calls, subscripts, or attributes of other objects.

Values are the six of `DATA_FLOW_CONFIRMED_VALUES` (ADR-0015). Per
decision 3, `flow_kind = "data"` exactly when the value is one of
`DATA_FLOW_DATA_VALUES`; `flow_kind_for` is the single place that rule
is applied, so a detector and the normalizer cannot disagree.

Enrichment is deliberately conservative: a rule that does not apply
returns `"unknown"`, which leaves the edge as control flow. A false
`data` edge is worse than a missing one — it tells the Phase 3 LLM that
an attack path is confirmed when it is not (ADR-0015, ADR-0009).
"""

from __future__ import annotations

import ast
import re

from ..flow_analyzer import DATA_FLOW_CONFIRMED_VALUES, DATA_FLOW_DATA_VALUES


# Tool functions may be sync or async (MCP tools are often `async def`).
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


# Implicit receiver parameters: an MCP tool declared on a class takes
# `self`, but the client supplies no argument for it.
_IMPLICIT_PARAMS: frozenset[str] = frozenset({"self", "cls"})


# Keyword names LangChain LLM clients use for the model identifier.
_MODEL_KEYWORDS: tuple[str, ...] = ("model", "model_name")


# ============================================================
# Shared rule: value -> flow_kind
# ============================================================

def flow_kind_for(data_flow_confirmed: str | None) -> str:
    """Return the `flow_kind` implied by a `data_flow_confirmed` value.

    ADR-0015 decision 3: `parameter_match`, `local_var_match` and
    `call_result_match` confirm data flow; everything else — including
    a missing value — does not.
    """
    if data_flow_confirmed in DATA_FLOW_DATA_VALUES:
        return "data"
    return "control"


# ============================================================
# LangChain: uses_tool
# ============================================================

def enrich_uses_tool(
    tools_arg: ast.expr | None,
    agent_system_prompt: str | None,
    assignments: dict[str, ast.expr],
    *,
    tool_name: str | None = None,
) -> str:
    """Classify a LangChain `uses_tool` edge.

    The system prompt is the developer's instruction about which tool to
    use. When it names a tool from the agent's `tools=[...]` list, the
    framework routes the agent's parameters into that tool: the tool was
    *given* to the agent, not merely reachable from it. That is the same
    level of evidence as a name match in the sink analyzer — name-based,
    one step of indirection, no dataflow (ADR-0001).

    Returns `"parameter_match"` when at least one tool in the list is
    named in the prompt, `"unknown"` otherwise — including when there is
    no system prompt, which is the common case for
    `AgentExecutor`/`initialize_agent` layouts.

    `tool_name` narrows the question to one edge: `tools=[a, b]` with
    `system_prompt="Use a for X"` must enrich the edge to `a` and leave
    the edge to `b` control. Passing the edge's target name answers
    that; omitting it answers the list-level question.
    """
    if not agent_system_prompt or not agent_system_prompt.strip():
        return "unknown"

    if tool_name is not None:
        names = [tool_name]
    else:
        names = resolve_tool_names(tools_arg, assignments)

    for name in names:
        if name and _named_in_prompt(name, agent_system_prompt):
            return "parameter_match"
    return "unknown"


def _named_in_prompt(tool_name: str, prompt: str) -> bool:
    """True if `tool_name` appears in `prompt` as a whole word.

    Whole-word matching, not substring: a tool named `search` must not
    be matched by "research". The character class mirrors Python
    identifiers, which is the vocabulary of tool names.
    """
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(tool_name)}(?![A-Za-z0-9_])"
    return re.search(pattern, prompt) is not None


# ============================================================
# LangChain: tools= resolution
# ============================================================

def resolve_tool_names(
    expr: ast.expr | None,
    assignments: dict[str, ast.expr],
    _visited: set[str] | None = None,
) -> list[str]:
    """Resolve a `tools=` expression to the tool names it references.

    Handles the shapes the LangChain detector already resolves:

        tools=[read_ticket, search_docs]      → ["read_ticket", ...]
        tools=USER_TOOLS                      → the assignment's elements
        tools=Tool(name="x", ...)             → ["x"]
        tools=StructuredTool.from_function(f) → ["f"]

    A `Name` that is not in `assignments` is itself a tool reference
    (`from tools import search` then `tools=[search]`). Cycles are
    guarded: a name already visited is not followed again.
    """
    if _visited is None:
        _visited = set()

    if expr is None:
        return []

    if isinstance(expr, ast.Name):
        if expr.id in _visited:
            return []
        _visited.add(expr.id)
        target = assignments.get(expr.id)
        if target is None:
            return [expr.id]
        return resolve_tool_names(target, assignments, _visited)

    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        names: list[str] = []
        for element in expr.elts:
            names.extend(resolve_tool_names(element, assignments, _visited))
        return names

    if isinstance(expr, ast.Constant):
        return [expr.value] if isinstance(expr.value, str) else []

    if isinstance(expr, ast.Call):
        # Tool(name="x", ...) / StructuredTool(name="x", ...)
        named = _keyword(expr, "name")
        if isinstance(named, ast.Constant) and isinstance(named.value, str):
            return [named.value]

        func = expr.func
        if isinstance(func, ast.Attribute) and func.attr == "from_function":
            if expr.args and isinstance(expr.args[0], ast.Name):
                return [expr.args[0].id]
            if expr.args and isinstance(expr.args[0], ast.Attribute):
                return [expr.args[0].attr]
            return []

        # Tool("x", func, ...) — first positional is the tool name.
        if expr.args and isinstance(expr.args[0], ast.Constant):
            if isinstance(expr.args[0].value, str):
                return [expr.args[0].value]
        return []

    return []



# ============================================================
# LangChain: uses_model
# ============================================================

def enrich_uses_model(
    model_arg: ast.expr | None,
    assignments: dict[str, ast.expr],
    params: frozenset[str] | set[str] = frozenset(),
) -> str:
    """Classify a LangChain `uses_model` edge.

    `model_arg` is the expression the agent was configured with
    (`model=` / `llm=`). It is usually a name bound to a client
    constructor (`llm = ChatOllama(model=model_name)`), so the helper
    follows the name to the constructor and asks about *its* `model=` /
    `model_name=` argument:

        model="gpt-4"                 → literal_only
        model=MODEL_NAME (module)     → literal_only, when it is a string
        model=model_name (parameter)  → parameter_match
        model=os.environ["MODEL"]     → unknown

    `params` are the parameters of the enclosing function (or of any
    enclosing function, for nested definitions). A name in that set is
    caller-supplied data, which is what `parameter_match` means.
    """
    return _classify_model_value(model_arg, assignments, set(params), set())


def _classify_model_value(
    node: ast.expr | None,
    assignments: dict[str, ast.expr],
    params: set[str],
    visited: set[str],
) -> str:
    """Classify one expression as a model configuration value."""
    if node is None:
        return "unknown"

    if isinstance(node, ast.Constant):
        # A model identifier is a string; a non-string constant means
        # the argument is not a model name at all.
        if isinstance(node.value, str):
            return "literal_only"
        return "unknown"

    if isinstance(node, ast.Name):
        if node.id in params:
            return "parameter_match"
        if node.id in visited:
            return "unknown"
        visited.add(node.id)
        target = assignments.get(node.id)
        if target is None:
            return "unknown"
        return _classify_model_value(target, assignments, params, visited)

    if isinstance(node, ast.Call):
        # ChatOpenAI(model=..., model_name=...), or create_agent(model=...).
        for key in _MODEL_KEYWORDS:
            value = _keyword(node, key)
            if value is not None:
                return _classify_model_value(
                    value, assignments, params, visited
                )
        return "unknown"

    # An attribute (`settings.model`), a subscript (`config["model"]`),
    # an f-string — not followed (ADR-0001).
    return "unknown"


# ============================================================
# MCP / LangChain tools: provides_tool
# ============================================================

def enrich_provides_tool(tool_func: ast.expr | None) -> str:
    """Classify a server → tool edge for a decorated tool function.

    The MCP protocol is the channel: the client sends tool arguments as
    data, the server receives them as parameters, the tool body consumes
    them. When the decorated function declares at least one parameter
    (excluding the implicit `self`/`cls`), data flow through the edge is
    confirmed by the framework's own contract: `parameter_match`. A tool
    with no parameters has nothing to receive: `no_args`.

    The rule is deliberately about the *signature*, not the body. A tool
    that ignores its parameter is still handed the caller's data; a tool
    with no parameter cannot be handed any. Deeper analysis (does the
    parameter reach a dangerous call?) is the Flow Analyzer's question
    (1.2a.1), not this one.

    Returns `"unknown"` when there is no function node (a tool created
    by a constructor or factory whose signature is not visible).
    """
    if not isinstance(tool_func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "unknown"
    return "parameter_match" if _tool_parameters(tool_func) else "no_args"


def _tool_parameters(func: FunctionNode) -> list[str]:
    """Return the declared parameters of a tool function.

    Positional-only, positional-or-keyword and keyword-only parameters
    count. `self`/`cls` are excluded: the MCP client does not supply
    them. `*args`/`**kwargs` count — they receive caller data too.
    """
    args = func.args
    names = [
        a.arg
        for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        if a.arg not in _IMPLICIT_PARAMS
    ]
    if args.vararg is not None:
        names.append(args.vararg.arg)
    if args.kwarg is not None:
        names.append(args.kwarg.arg)
    return names


# ============================================================
# Helpers
# ============================================================

def _keyword(call: ast.Call, key: str) -> ast.expr | None:
    """Return the value of `key=` in a call, or None."""
    for kw in call.keywords:
        if kw.arg == key:
            return kw.value
    return None


__all__ = [
    "DATA_FLOW_CONFIRMED_VALUES",
    "enrich_provides_tool",
    "enrich_uses_model",
    "enrich_uses_tool",
    "flow_kind_for",
    "resolve_tool_names",
]
