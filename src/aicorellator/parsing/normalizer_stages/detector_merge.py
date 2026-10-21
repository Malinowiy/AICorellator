"""DetectorMergeStage — framework detector nodes are authoritative.

Run order: 10 of 16 (see `normalizer_stages.STAGES`).

C4: "**Detector node merge** — detector nodes are authoritative for
kind. When a detector node and a TSA node share name and source file,
the detector node replaces the TSA node. Capabilities are merged from
the TSA node if the detector node has none. Edges pointing at the
removed node are re-pointed to the replacement, and their IDs are
recomputed and deduplicated. `flow_kind` and `data_flow_confirmed` are
preserved across the merge."

How the id remap and the edge re-point work, in order:

1. **Index** every node of the delta by `(name, source_file)`. That pair
   is the only identity the two producers share — TSA derives ids from
   the symbol path, detectors from a hash of framework + kind + file +
   name, so the ids never collide and never match.
2. **Replace**: for each detector node, look the pair up. When a TSA node
   is there, copy its capabilities onto the detector node if the
   detector found none (a detector knows the *shape*, the capability
   extractor knows the *behaviour*), remove the TSA node from the delta,
   and record `old id → new id` in `id_remap`. The detector node is
   appended in place of the old node's position only in spirit — the
   delta is a set of nodes, and the writer orders by id.
3. **Re-point**: any edge whose source or target was remapped is rebuilt
   with a recomputed id. Rebuilding can produce two edges with the same
   id — a TSA edge and a detector edge that now describe the same
   relationship — so the loop keeps the first and drops the rest. Every
   other edge field (`precision`, `attributes`, `capabilities`,
   `confidence`, `status`, `flow_kind`, `data_flow_confirmed`) is copied
   across, which is what "preserved" means in the C4 sentence.
4. **Detector edges** are appended last, skipping ids the delta already
   has.

The stage does not touch `sink_data_flow` / `returns_data`: a replaced
TSA node loses them with the node, and `BodyAttributeStage` (run last)
fills them in for whatever nodes survive, detector-created ones
included. That two-pass arrangement is why the attribute question has a
stage of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Edge, Node
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import edge_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class DetectorMergeStage:
    """Replace TSA nodes with detector nodes of the same (name, file)."""

    name = "detector_merge"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        existing_by_name_file: dict[tuple[str, str], Node] = {}
        for node in delta.nodes:
            key = (node.name, node.attributes.get("source_file", ""))
            existing_by_name_file[key] = node

        # Map old node id → new node id, for edges that need re-pointing.
        id_remap: dict[str, str] = {}

        for detector_node in ctx.framework_detection.nodes:
            key = (
                detector_node.name,
                detector_node.attributes.get("source_file", ""),
            )
            existing = existing_by_name_file.get(key)

            if existing is not None:
                # Preserve capabilities from existing if detector has
                # none.
                if not detector_node.capabilities and existing.capabilities:
                    detector_node.capabilities = list(existing.capabilities)
                # Remove existing node.
                delta.nodes = [n for n in delta.nodes if n is not existing]
                # Record id remap so edges can be re-pointed below.
                id_remap[existing.id] = detector_node.id

            delta.nodes.append(detector_node)
            existing_by_name_file[key] = detector_node

        if id_remap:
            self._repoint_edges(delta, id_remap)

        existing_edge_ids = {e.id for e in delta.edges}
        for edge in ctx.framework_detection.edges:
            if edge.id in existing_edge_ids:
                continue
            delta.edges.append(edge)
            existing_edge_ids.add(edge.id)

        return delta

    @staticmethod
    def _repoint_edges(
        delta: "GraphDelta", id_remap: dict[str, str]
    ) -> None:
        """Re-point edges from removed nodes to their replacements.

        Without this, edges to a TSA node that got replaced by a
        detector node would be silently dropped.
        """
        new_edges: list[Edge] = []
        seen: set[str] = set()
        for edge in delta.edges:
            new_source = id_remap.get(edge.source_id, edge.source_id)
            new_target = id_remap.get(edge.target_id, edge.target_id)
            if new_source != edge.source_id or new_target != edge.target_id:
                new_id = edge_id(new_source, new_target, edge.kind)
                if new_id in seen:
                    continue
                edge = Edge(
                    id=new_id,
                    source_id=new_source,
                    target_id=new_target,
                    kind=edge.kind,
                    host_id=edge.host_id,
                    precision=edge.precision,
                    attributes=edge.attributes,
                    capabilities=edge.capabilities,
                    confidence=edge.confidence,
                    status=edge.status,
                    flow_kind=edge.flow_kind,
                    data_flow_confirmed=edge.data_flow_confirmed,
                )
                seen.add(new_id)
            new_edges.append(edge)
        delta.edges = new_edges
