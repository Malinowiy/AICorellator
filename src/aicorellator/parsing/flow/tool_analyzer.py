"""Tool analyzer — does a tool hand data back to its caller.

The second half of the "potential data flow through the agent" signal
(1.2a.1): a tool that returns data can carry the agent's data onward to
its caller. The answer is a `bool`, not a `FlowDecision` — "does this
body return anything" is a yes/no fact, not a classification of how data
moved, and it never becomes an edge attribute. The normalizer publishes
it as the node's `returns_data` (ADR-0015: a prompt signal, not a chain
filter).

Its own module so the tool question has one home: the sink analyzer
answers a different question about a different node kind, and the two
share nothing but `FunctionNode`.
"""

from __future__ import annotations

import ast

from .arguments import FunctionNode


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
