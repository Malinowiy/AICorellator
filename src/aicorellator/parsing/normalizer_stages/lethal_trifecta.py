"""LethalTrifectaStage — the `lethal_trifecta` node attribute (1.3d).

Run order: 18 of 18 (see `normalizer_stages.STAGES`) — last.

One node fact, derived from the node's own capabilities: does this node
read untrusted input, can it read secrets, and can it get data out?
That is ADR-0009's lethal trifecta, spelled by the three sets in
`patterns.py`, and it is what ADR-0012 scores +5 for. Until 1.3d the
combination was computed inside `graph/query.py`'s scorer and nowhere
else, which made the fact a property of one score rather than something
the prompt (1.3e), the report, or any other consumer could read off the
node. The stage writes it down once per scan.

Why its own stage rather than a branch inside `body_attributes`:

  - the two answer different questions about different inputs.
    `body_attributes` reads *source* — it resolves the function or class
    behind a tool or sink node and parses its body — and it is scoped to
    those two kinds. This stage reads *capabilities*, which every node
    kind can carry, and touches no file. Folding it in would make a
    stage named for bodies the authority on a capability combination,
    and would leave the question "which node kinds does it visit?"
    answered differently in two halves of one loop.
  - every other concern in the pipeline has its own stage and its own
    test file; this one is ~10 lines and reads well on its own.

Why last, and why after `body_attributes`: the only ordering constraint
is the detector merge (stage 10), which can replace a node and the
replacement is what the graph keeps — the same reason `body_attributes`
runs late. This stage has no constraint relative to `body_attributes`,
so it takes the end of the pipeline, where the node set is final.

The attribute is written on **every** node, `True` or `False`, and the
stage is its only producer, so it recomputes rather than guarding: a
node that no longer satisfies the combination loses a flag it had. The
`body_attributes` "leave an existing attribute alone" guard exists
because a detector can arrive with a better answer for that attribute;
nothing produces this one, and a stale flag would be worse than a
recomputed one.

Storing `False` rather than omitting it is deliberate, and it is the
`body_attributes` posture read closely: that stage *does* store its
negative (`returns_data = False`, `sink_data_flow = "unknown"` — see
`tests/test_ast_normalizer.py`), and its attributes may be absent only
because they are node-kind specific. This attribute is not kind
specific — any node can read untrusted input and hold secrets — so
absence cannot mean "not applicable". It means "not computed", which is
what `graph/query.py`'s scorer needs to tell apart from a computed
`False` in order to fall back to the capability walk on a graph written
before 1.3d.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...patterns import lethal_trifecta_caps
from ..normalizer_context import NormalizeContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class LethalTrifectaStage:
    """Record ADR-0009's lethal trifecta as a node attribute."""

    name = "lethal_trifecta"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        for node in delta.nodes:
            node.attributes["lethal_trifecta"] = bool(
                lethal_trifecta_caps(node.capabilities)
            )
        return delta
