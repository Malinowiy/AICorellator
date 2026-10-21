"""Edge analyzer — how data reaches a sink call inside a tool body.

One edge, one question: the `has_access_to` edge from a tool to a sink.
The answer is a `FlowDecision` with `source="edge_analyzer"`.

This analyzer publishes `data_flow_confirmed` on an edge, which makes it
the conservative of the two: `_classify_argument` runs with its default
`parameter_attributes=False`, so `file_data.filename` is `unknown` here
even though the sink analyzer accepts it (1.2a.3g, 1.2a.3j). The two
questions are genuinely different — "does the tool hand this value to
the sink?" versus "does this sink body consume its own parameters?" —
and separate modules keep the difference visible instead of one flag
deep in a shared helper.
"""

from __future__ import annotations

import ast

from .arguments import (
    FunctionNode,
    _argument_evidence,
    _classify_argument,
    _local_variable_names,
    _parameter_names,
)
from .decision import FlowDecision


def analyze_has_access_to(
    tool_func: FunctionNode,
    sink_call: ast.Call,
) -> FlowDecision:
    """Classify how data reaches `sink_call` inside `tool_func`.

    Args:
        tool_func: the function (or method) whose body contains the call.
        sink_call: the call to the sink node inside that body.

    Returns:
        A `FlowDecision` with `source="edge_analyzer"`, whose `kind` is
        one of the six `data_flow_confirmed` values. The evidence names
        the arguments that were classified.

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
        return _edge_decision("no_args")

    params = _parameter_names(tool_func)
    locals_ = _local_variable_names(tool_func)

    kinds = [_classify_argument(arg, params, locals_) for arg in arguments]
    evidence = _argument_evidence(arguments)

    for kind in (
        "parameter_match",
        "local_var_match",
        "call_result_match",
    ):
        if kind in kinds:
            return _edge_decision(kind, evidence)
    if all(kind == "literal_only" for kind in kinds):
        return _edge_decision("literal_only", evidence)
    return _edge_decision("unknown", evidence)


def _edge_decision(
    kind: str,
    evidence: tuple[str, ...] = (),
) -> FlowDecision:
    """Build the edge decision. One place, so `source` is stable."""
    return FlowDecision(kind, evidence, source="edge_analyzer")
