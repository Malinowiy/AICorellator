"""Framework-detector enrichment rules (1.2a.2).

A framework detector knows its own framework's argument-passing rules:
"does the agent's system prompt name this tool?", "was this LLM
configured with a literal model name?", "does this MCP tool accept
parameters?". This module holds those rules — small, pure functions over
AST nodes, one per detector question.

**After ADR-0016 the answer does not land on an AST edge.** A decision
computed here is a framework-specific reading of the source, not a
data-flow confirmation: the AST layer does not answer that question, so
`data_flow_confirmed` is `NULL` on every AST edge and is populated only
by Layer 2, which runs `flow/` against a pair runtime observed. The rules
remain — like the `flow/` analyzers, they are framework knowledge Layer 2
will consume — and the detectors still ask them, but no edge carries
their result.

Each helper returns a `FlowDecision` (`parsing.flow`). Every helper
takes the framework name, because the decision's `source`
(`"enrichment:langchain"`, `"enrichment:fastmcp"`, ...) is part of the
answer: when an LLM summary disagrees with a second producer about the
same edge, the producer is what tells you which rule to re-read. The
argument is keyword-only and has **no default** — attributing a value
to the wrong framework is worse than writing one more word at the call
site.

Why here and not in `parsing/flow/` (the analyzers): those modules
answer the framework-independent question "how does data reach a sink
call inside this body?". These helpers answer framework-specific
questions — "does the agent's system prompt name this tool?", "was this
LLM configured with a literal model name?", "does this MCP tool accept
parameters?". Keeping them next to the detectors preserves that
separation: the generic analyzer stays free of framework vocabulary,
and each detector owns its own enrichment rule (ADR-0011).

The rules are name-based, one step deep (ADR-0001). They never follow
values through calls, subscripts, or attributes of other objects.

Values are the six of `VALID_KINDS` (ADR-0015). `flow.decision.
flow_kind_for` remains the single place the value → kind rule is applied,
but on Layer 1a nothing consumes the answer: the AST layer writes
`flow_kind = "control"` on every edge and no `data_flow_confirmed` at
all (ADR-0016, decision 3). Layer 2 will call it.

Enrichment is deliberately conservative: a rule that does not apply
returns `"unknown"`. A false `data` reading is worse than a missing one —
it would tell the Phase 3 LLM that an attack path is confirmed when it is
not (ADR-0015, ADR-0009).
"""

from __future__ import annotations

import ast
import re

from ..flow.decision import FlowDecision


# Tool functions may be sync or async (MCP tools are often `async def`).
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


# Implicit receiver parameters: an MCP tool declared on a class takes
# `self`, but the client supplies no argument for it.
_IMPLICIT_PARAMS: frozenset[str] = frozenset({"self", "cls"})


# Keyword names LangChain LLM clients use for the model identifier.
_MODEL_KEYWORDS: tuple[str, ...] = ("model", "model_name")


def _decision(
    kind: str,
    framework: str,
    evidence: tuple[str, ...] = (),
) -> FlowDecision:
    """Build an enrichment decision with its framework provenance."""
    return FlowDecision(kind, evidence, source=f"enrichment:{framework}")


# ============================================================
# LangChain: uses_tool
# ============================================================

def enrich_uses_tool(
    tools_arg: ast.expr | None,
    agent_system_prompt: str | None,
    assignments: dict[str, ast.expr],
    *,
    framework: str,
    tool_name: str | None = None,
) -> FlowDecision:
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

    `framework` names the producer for `source` (keyword-only, no
    default — see the module docstring).
    """
    if not agent_system_prompt or not agent_system_prompt.strip():
        return _decision("unknown", framework, ("no system prompt",))

    if tool_name is not None:
        names = [tool_name]
    else:
        names = resolve_tool_names(tools_arg, assignments)

    for name in names:
        if name and _named_in_prompt(name, agent_system_prompt):
            return _decision(
                "parameter_match", framework, (f"prompt names {name}",)
            )
    return _decision("unknown", framework, ("no tool named in prompt",))


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
    *,
    framework: str,
) -> FlowDecision:
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
    kind, evidence = _classify_model_value(
        model_arg, assignments, set(params), set()
    )
    return _decision(kind, framework, evidence)


def _classify_model_value(
    node: ast.expr | None,
    assignments: dict[str, ast.expr],
    params: set[str],
    visited: set[str],
) -> tuple[str, tuple[str, ...]]:
    """Classify one expression, returning `(kind, evidence)`.

    The recursion carries the evidence with the kind so the reason for
    the answer survives however many steps of name resolution it took.
    """
    if node is None:
        return "unknown", ("no model argument",)

    if isinstance(node, ast.Constant):
        # A model identifier is a string; a non-string constant means
        # the argument is not a model name at all.
        if isinstance(node.value, str):
            return "literal_only", ("literal model name",)
        return "unknown", ("model argument is not a string",)

    if isinstance(node, ast.Name):
        if node.id in params:
            return "parameter_match", (f"model from parameter {node.id}",)
        if node.id in visited:
            return "unknown", ("cyclic model assignment",)
        visited.add(node.id)
        target = assignments.get(node.id)
        if target is None:
            return "unknown", (f"unresolved name {node.id}",)
        return _classify_model_value(target, assignments, params, visited)

    if isinstance(node, ast.Call):
        # ChatOpenAI(model=..., model_name=...), or create_agent(model=...).
        for key in _MODEL_KEYWORDS:
            value = _keyword(node, key)
            if value is not None:
                return _classify_model_value(
                    value, assignments, params, visited
                )
        return "unknown", ("client call sets no model",)

    # An attribute (`settings.model`), a subscript (`config["model"]`),
    # an f-string — not followed (ADR-0001).
    return "unknown", ("model argument is not a name or literal",)


# ============================================================
# MCP / LangChain tools: provides_tool
# ============================================================

def enrich_provides_tool(
    tool_func: ast.expr | None,
    *,
    framework: str,
) -> FlowDecision:
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

    This helper serves three detectors (LangChain `@tool`, FastMCP,
    MCP Python), so `framework` is what keeps their `source` values
    apart: `"enrichment:langchain"`, `"enrichment:fastmcp"`,
    `"enrichment:mcp_python"`.
    """
    if not isinstance(tool_func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _decision(
            "unknown", framework, ("tool signature not visible",)
        )
    if _tool_parameters(tool_func):
        return _decision(
            "parameter_match", framework, ("tool declares parameters",)
        )
    return _decision("no_args", framework, ("tool declares no parameters",))


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
    "enrich_provides_tool",
    "enrich_uses_model",
    "enrich_uses_tool",
    "resolve_tool_names",
]
