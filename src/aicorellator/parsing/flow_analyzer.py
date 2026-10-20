"""Flow Analyzer — name-based data-flow classification.

ADR-0015 introduces `flow_kind` on every edge and `data_flow_confirmed`
on `has_access_to`. This module answers *how* data moves.

Edge question — *how* does data reach a sink call invoked inside a tool
body?

    analyze_has_access_to(tool_func, sink_call) -> str

Node questions (1.2a.1) — the tool and the sink are often colocated in
a file without the tool calling the sink by name. Then the useful
questions are asked of each body separately:

    analyze_sink_body(sink_func)    -> str   ("sink_data_flow")
    analyze_tool_returns(tool_func) -> bool  ("returns_data")

A tool that returns data and a sink that accepts data make a data flow
*through the agent* plausible even when the `has_access_to` edge is
`control`. The attributes feed the prompt; they do not change chain
enumeration or `flow_kind`.

The result of `analyze_has_access_to` is one of six values:

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

A literal is a constant or a collection of constants: the command list
of `subprocess.run(["docker", "info"])` is constant data, so it is
`literal_only`, not `unknown` (1.2a.4).

The function is pure: it takes AST nodes and returns a string, with no
filesystem or global state. The normalizer supplies the nodes.
"""

from __future__ import annotations

import ast

from ..patterns import SINK_CAPABILITIES
# The Capability Extractor owns the call-site patterns (ADR-0009). The
# sink analyzer reuses its classifier so "dangerous call" means exactly
# the same thing as "the call that gave the node a sink capability".
# Importing the private helper follows the convention the framework
# detectors already use (`from ..capability_extractor import _scan_function`).
from .capability_extractor import _classify_call


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

# The three values of `sink_data_flow` on a sink node (1.2a.1). The
# three data kinds of `has_access_to` collapse into `parameter_match`:
# from the sink's own body, any argument that is not a literal comes
# from the sink's parameters or its internal state.
SINK_DATA_FLOW_VALUES: frozenset[str] = frozenset({
    "parameter_match",
    "literal_only",
    "unknown",
})


def analyze_sink_body(sink_func: FunctionNode) -> str:
    """Classify how a sink passes its own data to a dangerous call.

    Args:
        sink_func: the body of a node classified as `sink`.

    Returns:
        `"parameter_match"`, `"literal_only"`, or `"unknown"`.

    The question is not "does a tool call this sink?" but "does this
    sink accept data?". From the sink's own perspective every argument
    that is not a literal is derived from the sink's parameters or its
    internal state, so the three data kinds of `has_access_to`
    (`parameter_match`, `local_var_match`, `call_result_match`) all
    collapse into `parameter_match`.

    Ranking — the strongest result across every dangerous call wins:

        no dangerous call in the body          -> unknown
        any dangerous call takes a non-literal -> parameter_match
        every dangerous argument is a literal  -> literal_only
        otherwise                              -> unknown

    A dangerous call with no arguments carries no data in either
    direction, so it neither confirms nor denies flow; it is skipped.
    `unknown` therefore means "no argument-level evidence", not "the
    body was not read".

    A `literal_only` sink cannot receive attacker data through its own
    arguments — only constants reach the dangerous call. Like
    `analyze_has_access_to` this is name-based (ADR-0001): attribute
    access, subscripts and f-strings fall back to `unknown`, and false
    negatives are accepted (ADR-0015).
    """
    dangerous = _dangerous_calls(sink_func)
    if not dangerous:
        return "unknown"

    params = _parameter_names(sink_func)
    locals_ = _local_variable_names(sink_func)
    saw_literal_only = False

    for call in dangerous:
        arguments: list[ast.expr] = list(call.args)
        arguments.extend(kw.value for kw in call.keywords)
        if not arguments:
            continue

        kinds = [_classify_argument(arg, params, locals_) for arg in arguments]
        if any(kind in DATA_FLOW_DATA_VALUES for kind in kinds):
            return "parameter_match"
        if all(kind == "literal_only" for kind in kinds):
            saw_literal_only = True

    return "literal_only" if saw_literal_only else "unknown"


def analyze_tool_returns(tool_func: FunctionNode) -> bool:
    """True if `tool_func` returns data to its caller.

    A tool that hands data back to the calling agent can carry the
    agent's data onward — the second half of the "potential data flow
    through the agent" signal (1.2a.1).

    `return <value>` counts. A bare `return`, `return None`, and a body
    with no `return` at all all mean "nothing is handed back". `yield`
    and `yield from` count: the caller receives a generator, which is
    data.

    Nested scopes are not separated — an inner function's `return`
    counts, matching `_local_variable_names` (acceptable for name-based
    analysis, ADR-0001).
    """
    for node in ast.walk(tool_func):
        if isinstance(node, ast.Return):
            value = node.value
            if value is None:
                continue
            if isinstance(value, ast.Constant) and value.value is None:
                continue
            return True
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return True
    return False

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
    *every* argument to be a literal (a constant collection counts as a
    literal — 1.2a.4). A single unresolved argument means data may flow
    through it, so the result is `unknown` even when another argument
    is a literal.

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
# Dangerous calls
# ============================================================

def _dangerous_calls(func: FunctionNode) -> list[ast.Call]:
    """Calls in `func` that exercise a sink capability.

    A dangerous call is one the Capability Extractor would credit with
    a capability in `patterns.SINK_CAPABILITIES` — `os.remove(p)`,
    `subprocess.run(cmd, shell=True)`, `cursor.execute("DELETE ...")`,
    `open(p, "w")`, `requests.post(url, ...)`. Reusing the extractor's
    classifier keeps one definition of "dangerous" in the codebase
    (ADR-0009) and keeps the analyzer name-based (ADR-0001).
    """
    calls: list[ast.Call] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if _classify_call(node) & SINK_CAPABILITIES:
            calls.append(node)
    return calls


# ============================================================
# Argument classification
# ============================================================

def _is_literal(node: ast.expr | None) -> bool:
    """True if `node` is a literal or a constant collection (1.2a.4).

    A collection is a literal when all of its elements are literals, so
    `["docker", "info"]` is as constant as `"docker"`. Collections
    recurse: `[["a"], ["b"]]` is constant, `[["a"], param]` is not. An
    empty collection is constant (`all()` over nothing).

    `None` is not constant: it is how `ast.Dict.keys` represents the
    `{**mapping}` unpacking form, which is not a constant.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal(element) for element in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            _is_literal(element) for element in (*node.keys, *node.values)
        )
    return False


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

    # Constant collections are literals for our purposes (1.2a.4).
    # A collection that mixes a parameter and a literal is deliberately
    # `unknown`, not the element's kind: the collection as a whole is
    # not a constant, and we do not know whether the sink consumes the
    # parameter element or the literal one, so claiming
    # `parameter_match` would overstate the evidence (ADR-0001,
    # ADR-0015 — false negatives are accepted, overstated evidence is
    # not).
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return "literal_only" if _is_literal(node) else "unknown"

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
