"""`NormalizerStage` — the shape of one normalizer step.

A stage is a pure-ish function over the graph under construction: it
reads the context (inputs, caches, what earlier stages discovered), adds
nodes and edges to the delta, and returns the delta. It never reads
`ast_normalizer` at runtime — the pipeline imports the stages, not the
other way round — and it never reaches outside its own inputs, which is
what makes a stage testable on a hand-built delta.

`name` exists for logs, for the order test, and for the operator who
wants to know which step produced a node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..normalizer_context import NormalizeContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


@runtime_checkable
class NormalizerStage(Protocol):
    """One step of the normalization pipeline."""

    name: str

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        """Add this stage's nodes and edges to `delta` and return it."""
        ...
