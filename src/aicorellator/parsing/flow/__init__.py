"""Flow semantics — the value objects the analyzers speak.

Step 1 (architectural repair) introduces `FlowDecision`: one value
object carrying the `data_flow_confirmed` kind, the evidence that
produced it, and the producer's name. Later steps split the analyzer
implementation into this package (`edge_analyzer`, `sink_analyzer`,
`tool_analyzer`, `arguments`); this module is only the contract they
share.

Keeping the contract in its own module means a producer (an analyzer,
a framework detector's enrichment helper) and every consumer (the
normalizer, the detectors, the query layer) depend on the type, not on
whichever module happens to implement an analyzer today.
"""

from .decision import (
    DATA_KINDS,
    VALID_KINDS,
    FlowDecision,
    flow_kind_for,
)

__all__ = [
    "DATA_KINDS",
    "VALID_KINDS",
    "FlowDecision",
    "flow_kind_for",
]
