"""The normalizer pipeline — one stage per step (step 4).

`STAGES` is the pipeline, in execution order. `normalize(ctx)` runs it
straight through:

    for stage in STAGES:
        delta = stage.apply(delta, ctx)

The order is the C4 L3 Phase 1 order (steps 1-13 of the AST Normalizer
section), plus `BodyAttributeStage` last:

     1  mcp_server_nodes       mcp_server nodes from paths
     2  class_nodes            class nodes (classification + capabilities)
     3  method_nodes           method nodes; tools/sinks indexed per file
     4  intra_file_edges       provides_tool, has_access_to (same file)
     5  entry_points           entry_point nodes
     6  llm_endpoints          llm_endpoint nodes from configs
     7  delegation_edges       entry → agent, agent → agent, agent → server
     8  entry_to_sink          entry → same-file sink reachability
     9  detector_merge         detector nodes authoritative; id remap
    10  flow_kind              flow_kind on every edge (ADR-0015)
    10  cross_file_resolution  uses_tool across modules (Resolver 1)
    11  mcp_entry_synthesis    virtual MCP entry (Resolver 3)
    12  orphan_entry           orphan entry → single agent (Resolver 2)
    13  body_attributes        sink_data_flow / returns_data backfill

Note on the order, verified against the pre-step-4 code:

  - `entry_to_sink` runs **before** the detector merge, where the code it
    was split from called it — not at C4's step 13, after the merge.
    C4's order is wrong and the code is right, for a mechanical reason:
    the resolver reads the TSA-side `sinks_by_file` index, whose node ids
    the merge invalidates. Run after the merge it produces
    `has_access_to` edges to nodes that no longer exist — on dvmcp that
    is six dangling edges and a `FOREIGN KEY constraint failed` at write
    time. Before the merge, the merge's id remap re-points those edges to
    the detector nodes, which is what the graph has always contained.
  - `BodyAttributeStage` runs last, not at node construction. Its
    *position* is load-bearing for the same kind of reason: the merge can
    replace a TSA node with a detector node that arrives without the
    attributes, and the replacement is what ends up in the graph.
"""

from __future__ import annotations

from .base import NormalizerStage
from .body_attributes import BodyAttributeStage
from .class_nodes import ClassNodeStage
from .cross_file_resolution import CrossFileResolutionStage
from .delegation_edges import DelegationEdgeStage
from .detector_merge import DetectorMergeStage
from .entry_points import EntryPointStage
from .entry_to_sink import EntryToSinkStage
from .flow_kind import FlowKindStage
from .intra_file_edges import IntraFileEdgeStage
from .llm_endpoints import LlmEndpointStage
from .mcp_entry_synthesis import McpEntrySynthesisStage
from .mcp_server_nodes import McpServerNodeStage
from .method_nodes import MethodNodeStage
from .orphan_entry import OrphanEntryStage

__all__ = ["STAGES", "NormalizerStage"]


# The pipeline. A tuple, not a configurable list: disabling a stage is a
# debugging concern for a later step, and an operator who needs it can
# run the stages by hand.
STAGES: tuple[NormalizerStage, ...] = (
    McpServerNodeStage(),
    ClassNodeStage(),
    MethodNodeStage(),
    IntraFileEdgeStage(),
    EntryPointStage(),
    LlmEndpointStage(),
    DelegationEdgeStage(),
    EntryToSinkStage(),
    DetectorMergeStage(),
    FlowKindStage(),
    CrossFileResolutionStage(),
    McpEntrySynthesisStage(),
    OrphanEntryStage(),
    BodyAttributeStage(),
)
