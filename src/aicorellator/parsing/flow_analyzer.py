"""Flow Analyzer — name-based data-flow classification for `has_access_to`.

ADR-0015 introduces `flow_kind` on every edge and `data_flow_confirmed`
on `has_access_to`. This module answers one question: *how* does data
reach a sink call that is invoked inside a tool body?

    analyze_has_access_to(tool_func, sink_call) -> str

The result is one of six values:

    parameter_match     an argument is a parameter of the tool function
    local_var_match     an argument is a local variable of the tool
    call_result_match   an argument is the result of another call
    literal_only        every argument is a literal constant
    no_args             the sink call takes no arguments
    unknown             otherwise — an argument could not be classified

The first rule that matches wins, in the order above (with `no_args`
checked first and `unknown` last); the ranking is not linear.

This is deliberately name-based, not value-based (ADR-0001 forbids full
dataflow analysis). It finds `os.remove(query)` where `query` is a tool
parameter. It does not resolve dictionary lookups, f-strings, or
attribute chains — those fall back to `unknown`. False negatives are
accepted; the LLM calibrates confidence from the result (ADR-0015).

The function is pure: it takes AST nodes and returns a string, with no
filesystem or global state. The normalizer supplies the nodes.
"""

from __future__ import annotations

import ast


# Tool functions may be sync or async (MCP tools are often `async def`).
# Both node types expose `.args` and are walkable.
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


# The six values of `data_flow_confirmed` (ADR-0015). Mirrors the CHECK
# constraint on edges.data_flow_confirmed in graph/schema.sql.
DATA_FLOW_CONFIRMED_VALUES: frozenset[str] = frozenset({
    "parameter_match",
    "local_var_match",
    "call_result_match",
    "literal_only",
    "no_args",
    "unknown",
})

# The subset that confirms data flow — `flow_kind = "data"` (ADR-0015,
# decision 3). The rest mean data flow was not confirmed: `control`.
DATA_FLOW_DATA_VALUES: frozenset[str] = frozenset({
    "parameter_match",
    "local_var_match",
    "call_result_match",
})

def analyze_has_access_to(
    tool_func: FunctionNode,
    sink_call: ast.Call,
) -> str:
    """Classify how data reaches `sink_call` inside `tool_func`.

    Args:
        tool_func: the function (or method) whose body contains the call.
        sink_call: the call to the sink node inside that body.

    Returns:
        One of the six `data_flow_confirmed` values.

    The ranking is not linear. The first rule that matches wins:

        no arguments at all              -> no_args
        any argument is a tool parameter -> parameter_match
        any argument is a local variable -> local_var_match
        any argument is a call result    -> call_result_match
        all arguments are literals       -> literal_only
        otherwise                        -> unknown

    `literal_only` is a universal, not an existential: it requires
    *every* argument to be a literal. A single unresolved argument
    means data may flow through it, so the result is `unknown` even
    when another argument is a literal.

    The call is assumed to be inside `tool_func`; the function does not
    verify this. It reads nothing outside the two AST nodes.
    """
    arguments: list[ast.expr] = list(sink_call.args)
    arguments.extend(kw.value for kw in sink_call.keywords)

    if not arguments:
        return "no_args"

    params = _parameter_names(tool_func)
    locals_ = _local_variable_names(tool_func)

    kinds = [_classify_argument(arg, params, locals_) for arg in arguments]

    if "parameter_match" in kinds:
        return "parameter_match"
    if "local_var_match" in kinds:
        return "local_var_match"
    if "call_result_match" in kinds:
        return "call_result_match"
    if all(kind == "literal_only" for kind in kinds):
        return "literal_only"
    return "unknown"


# ============================================================
# Argument classification
# ============================================================

def _classify_argument(
    node: ast.expr,
    params: set[str],
    locals_: set[str],
) -> str:
    """Classify a single argument expression."""
    # `await foo()` is still a call result.
    if isinstance(node, ast.Await):
        node = node.value

    if isinstance(node, ast.Call):
        return "call_result_match"

    if isinstance(node, ast.Name):
        if node.id in params:
            return "parameter_match"
        if node.id in locals_:
            return "local_var_match"
        # A global, import, or builtin — not tracked.
        return "unknown"

    if isinstance(node, ast.Constant):
        return "literal_only"

    # Attribute access, subscript, f-string, binop, starred, ... —
    # name-based analysis cannot follow these (ADR-0001, ADR-0015).
    return "unknown"


def _parameter_names(func: FunctionNode) -> set[str]:
    """All declared parameter names of a function.

    `self` and `cls` are excluded: they are the bound instance/class,
    not attacker-supplied data, and passing them to a sink is not a
    data-flow signal.
    """
    names: set[str] = set()
    a = func.args
    for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
        names.add(arg.arg)
    if a.vararg is not None:
        names.add(a.vararg.arg)
    if a.kwarg is not None:
        names.add(a.kwarg.arg)

    names.discard("self")
    names.discard("cls")
    return names


def _local_variable_names(func: FunctionNode) -> set[str]:
    """Names assigned inside the function body.

    Collects every `ast.Name` in a Store context: plain assignments,
    `for` targets, `with ... as`, walrus expressions, and comprehension
    targets. Nested scopes are not separated — a name bound in an inner
    function still counts, which is acceptable for name-based matching.
    """
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)

    names.discard("self")
    names.discard("cls")
    return names
