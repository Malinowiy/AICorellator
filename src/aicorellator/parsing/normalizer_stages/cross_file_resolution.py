"""CrossFileResolutionStage — `uses_tool` across modules (Resolver 1).

Run order: 14 of 18 (see `normalizer_stages.STAGES`).

C4: "**Cross-file resolution** — `uses_tool` edges from agent modules to
tool modules they import (ADR-0013, Resolver 1)."

Framework detectors are per-file (ADR-0011), so an agent created in
`agent.py` that uses tools imported from `tools.py` has no `uses_tool`
edge from the detector alone. This stage resolves those edges from the
manifest's import table, linking to *every* tool in an imported module —
a deliberate false-positive trade-off (ADR-0009): Phase 3 filters them.

Edges whose id the delta already has are skipped, so a detector edge
(which carries the 1.2a.2 enrichment) is never overwritten by the
control default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..framework_detectors.cross_file import resolve_agent_tool_edges
from ..normalizer_context import NormalizeContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class CrossFileResolutionStage:
    """`uses_tool` from an agent module to the tool modules it imports."""

    name = "cross_file_resolution"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        existing_edge_ids = {e.id for e in delta.edges}
        for edge in resolve_agent_tool_edges(
            manifest=ctx.manifest,
            detector_nodes=ctx.framework_detection.nodes,
            host_id=ctx.host_id,
        ):
            if edge.id in existing_edge_ids:
                continue
            delta.edges.append(edge)
            existing_edge_ids.add(edge.id)
        return delta
