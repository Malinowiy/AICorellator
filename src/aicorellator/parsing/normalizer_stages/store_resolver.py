"""StoreResolverStage — store nodes, `writes_to` and `reads_from` (B.3b).

Run order: 19 of 20 (see `normalizer_stages.STAGES`).

ADR-0021 §12: store identity is decided by **families** — one module per
technology a store is reached through — registered in
`aicorellator.store.registry`. This stage is the seam between that
subsystem and the pipeline: it builds a `StoreResolver` and calls it once
on the delta, and the resolver returns the edges. B.3b-core shipped the
seam with an **empty registry**, so the stage was a no-op on every scan;
B.3b-fs then registered `fs` — a family module plus a line in
`families/__init__.py` — and the stage produces an edge wherever that
family recognizes a store literal, with no change here. That is the
shape a second family reuses.

Why a stage, and not a call in `run_scan.py` after `normalize`:

  - `normalize()` is where every node and edge in this graph is built,
    and the pipeline is what a reader consults to find out which step
    produced something (`NormalizerStage.name` exists for exactly that).
    A producer that ran in the CLI and not in `normalize` would leave
    two pipelines in the tree: `run_scan.py` with stores, and every
    other caller of `normalize` — the pipeline tests among them —
    without them. B.3b-core has nothing to emit, which is the moment to
    choose the seam, not a moment at which the difference is invisible.
  - the resolver needs the whole graph: the component a write call
    belongs to, and the `untrusted_source` a read belongs to, are nodes
    stages 2–10 created or replaced (`detector_merge` re-points ids), so
    "after `normalize`" and "after the merge" are the same requirement.

Why *here*, and not after `guardrail_edges` (stage 20): the two share
their only constraint — the merge — and read nothing the other writes.
`guardrail_edges` documents itself as the last slot, which is a claim
about *its* position and not about this one, so the newer stage goes
before it and neither docstring lies.

What this stage does **not** do: `flow_kind` (stage 12) is not a
constraint, because the resolver classifies its own edges `structural`
(ADR-0021 §4) and `FlowKindStage` skips `STRUCTURAL_EDGE_KINDS` anyway.
`find_chain_groups` (B.3a) is not a constraint either: the join lives in
the query layer and reads what this stage wrote.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...store.resolver import StoreResolver
from ..normalizer_context import NormalizeContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class StoreResolverStage:
    """One call to `StoreResolver`, on the delta the pipeline built."""

    name = "store_resolver"

    def __init__(self, resolver: StoreResolver | None = None) -> None:
        #: Default: the registry, read when `resolve` runs — see
        #: `StoreResolver`. A caller (a test, a future variant) may hand
        #: in one built with explicit families or a context.
        self._resolver = resolver or StoreResolver()

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        """Append the store edges the delta earns. No family, no edges.

        The resolver puts its store *nodes* in the delta itself (that is
        the only way a node can leave it) and returns the edges, which
        this stage appends. With the registry empty, `resolve` returns
        `[]` and the delta is untouched.
        """
        delta.edges.extend(self._resolver.resolve(delta, ctx))
        return delta


__all__ = ["StoreResolverStage"]
