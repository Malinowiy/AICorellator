"""`FlowDecision` — how data flow was established, and who said so.

ADR-0015 gives every edge a `flow_kind` (`control` | `data` |
`structural`) and gives `has_access_to` (and, via enrichment, the
framework edges) a `data_flow_confirmed` value from a six-value set.
Before this type, that answer travelled through the code as a bare
string: the analyzers returned `str`, the enrichment helpers returned
`str`, and every caller re-derived `flow_kind` from the string with its
own copy of the decision-3 rule.

`FlowDecision` makes the answer a value:

    kind       one of the six `data_flow_confirmed` values — the same
               values, the same semantics (ADR-0015). The name says
               "kind" because `data_flow_confirmed` is the column it is
               written to, not the question it answers.
    evidence   short, name-based reasons the producer used, e.g. the
               parameter name that matched. Never values, never source
               text: evidence is for a human reading a report or a test
               failure, and it must not leak program data into the
               graph (ADR-0009 keeps the AST-level no-dataflow rule).
    source     which producer decided — `"edge_analyzer"`, `"sink_body"`,
               `"sink_class"`, `"enrichment:langchain"`, ... Two
               producers can legitimately disagree (`no_args` vs
               `unknown`); `source` is what makes such a disagreement
               readable instead of a mystery value.

`flow_kind_for` is the single mapping from the decision to `flow_kind`
(ADR-0015 decision 3). It takes the decision, not a string, so no caller
can invent a second rule.

The type is frozen: a decision is a statement about one analysis, not a
mutable record. Only `kind` is written to the graph; `evidence` and
`source` are provenance for whoever is debugging the graph.
"""

from __future__ import annotations

from dataclasses import dataclass


# The six values of `data_flow_confirmed` (ADR-0015). Mirrors the CHECK
# constraint on edges.data_flow_confirmed in graph/schema.sql.
VALID_KINDS: frozenset[str] = frozenset({
    "parameter_match",
    "local_var_match",
    "call_result_match",
    "literal_only",
    "no_args",
    "unknown",
})

# The subset that confirms data flow — `flow_kind = "data"` (ADR-0015,
# decision 3). The rest mean data flow was not confirmed: `control`.
DATA_KINDS: frozenset[str] = frozenset({
    "parameter_match",
    "local_var_match",
    "call_result_match",
})


# Legacy spellings of the two sets above. They were published from
# `parsing.flow_analyzer` before step 2 split that module; ADR-0015 (and
# the schema comment) name the column `data_flow_confirmed`, so the names
# are kept as aliases — same objects, one definition each. New code
# reads `VALID_KINDS` / `DATA_KINDS`.
DATA_FLOW_CONFIRMED_VALUES: frozenset[str] = VALID_KINDS
DATA_FLOW_DATA_VALUES: frozenset[str] = DATA_KINDS


@dataclass(frozen=True)
class FlowDecision:
    """One analysis result: the kind, the evidence, and the producer."""

    kind: str
    evidence: tuple[str, ...] = ()
    source: str = ""

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(f"unknown flow kind: {self.kind!r}")

    def is_data(self) -> bool:
        """True when this decision confirms data flow (ADR-0015, 3)."""
        return self.kind in DATA_KINDS


def flow_kind_for(decision: FlowDecision) -> str:
    """Map a decision to `flow_kind` (ADR-0015 decision 3).

    The only place the rule lives: `data` for the three data kinds,
    `control` for everything else — `literal_only`, `no_args` and
    `unknown` all mean data flow was not confirmed.
    """
    if decision.is_data():
        return "data"
    return "control"


__all__ = [
    "DATA_FLOW_CONFIRMED_VALUES",
    "DATA_FLOW_DATA_VALUES",
    "DATA_KINDS",
    "VALID_KINDS",
    "FlowDecision",
    "flow_kind_for",
]
