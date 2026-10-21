"""Flow semantics — the value objects and the analyzers that produce them.

One module per question (step 2):

    decision.py        `FlowDecision`, `flow_kind_for`, the value sets
    arguments.py       shared primitives: what an argument *is*
    edge_analyzer.py   `analyze_has_access_to`  → an edge's data flow
    sink_analyzer.py   `analyze_sink_body`, `analyze_sink_class`
    tool_analyzer.py   `analyze_tool_returns`

Before step 2 all of that lived in one `flow_analyzer` module, and the
three questions shared one entry point. A caller could ask "the
analyzer" without saying which question it meant, and the answer
depended on which arguments it passed. One module per question makes
each contract explicit — different node kind, different values, a
different level of evidence — and lets a caller import exactly the
question it is asking.

This package is the import surface for analyzers and decisions; the
individual modules are the contracts. Private primitives (`_classify_
argument`, `_parameter_names`, ...) are imported from the module that
owns them — they are implementation detail, not API.
"""

from .arguments import FunctionNode
from .decision import (
    DATA_FLOW_CONFIRMED_VALUES,
    DATA_FLOW_DATA_VALUES,
    DATA_KINDS,
    VALID_KINDS,
    FlowDecision,
    flow_kind_for,
)
from .edge_analyzer import analyze_has_access_to
from .sink_analyzer import (
    SINK_DATA_FLOW_VALUES,
    analyze_sink_body,
    analyze_sink_class,
)
from .tool_analyzer import analyze_tool_returns

__all__ = [
    "DATA_FLOW_CONFIRMED_VALUES",
    "DATA_FLOW_DATA_VALUES",
    "DATA_KINDS",
    "SINK_DATA_FLOW_VALUES",
    "VALID_KINDS",
    "FlowDecision",
    "FunctionNode",
    "analyze_has_access_to",
    "analyze_sink_body",
    "analyze_sink_class",
    "analyze_tool_returns",
    "flow_kind_for",
]

