"""Graph Writer — writes GraphDelta into a GraphStore.

Handles deduplication by canonical ID and sets pipeline flags.
"""

from __future__ import annotations

import logging

from ..graph.models import Edge, Node
from ..graph.store import GraphStore
from .ast_normalizer import GraphDelta


log = logging.getLogger(__name__)


def write(store: GraphStore, delta: GraphDelta, host_id: str = "localhost") -> dict[str, int]:
    """Write nodes and edges into the graph store.

    Existing nodes with the same ID are upserted (attributes merged,
    confidence takes max). Returns counts of inserted and updated rows.
    """
    nodes_added = 0
    nodes_updated = 0
    edges_added = 0
    edges_updated = 0

    for node in delta.nodes:
        existed = store.nodes.exists(node.id)
        store.nodes.upsert(node)
        if existed:
            nodes_updated += 1
        else:
            nodes_added += 1

    for edge in delta.edges:
        existed = store.edges.exists(edge.id)
        store.edges.upsert(edge)
        if existed:
            edges_updated += 1
        else:
            edges_added += 1

    return {
        "nodes_added": nodes_added,
        "nodes_updated": nodes_updated,
        "edges_added": edges_added,
        "edges_updated": edges_updated,
    }


def set_phase1_done(store: GraphStore, host_id: str = "localhost") -> None:
    """Mark Phase 1 as complete for a host."""
    store.state.set(host_id, "phase1_done", True)