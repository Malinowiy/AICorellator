"""The normalizer pipeline — one stage per step (step 4).

`STAGES` is the pipeline, in execution order. `normalize(ctx)` runs it
straight through:

    for stage in STAGES:
        delta = stage.apply(delta, ctx)

The order is the C4 L3 Phase 1 order — the fifteen rows of the AST
Normalizer stage table in `C4_L3_Phase1.md`, which lists these stages in
this order:

     1  mcp_server_nodes       mcp_server nodes from paths
     2  class_nodes            class nodes (classification + capabilities)
     3  method_nodes           method nodes; tools/sinks indexed per file
     4  intra_file_edges       provides_tool, has_access_to (same file)
     5  entry_points           entry_point nodes
     6  untrusted_sources      untrusted_source nodes + reader edges (1.3a)
     7  llm_endpoints          llm_endpoint nodes from configs
     8  delegation_edges       entry → agent, agent → agent, agent → server
     9  entry_to_sink          entry → same-file sink reachability
    10  detector_merge         detector nodes authoritative; id remap
    11  untrusted_source_writers  server → writes_to → source (1.3a.1)
    12  flow_kind              flow_kind on every edge (ADR-0015)
    13  cross_file_resolution  uses_tool across modules (Resolver 1)
    14  mcp_entry_synthesis    virtual MCP entry (Resolver 3)
    15  orphan_entry           orphan entry → single agent (Resolver 2)
    16  body_attributes        sink_data_flow / returns_data backfill

Note on the order:

  - `entry_to_sink` runs **before** the detector merge, where the code it
    was split from called it. This is load-bearing, for a mechanical
    reason: the resolver reads the TSA-side `sinks_by_file` index, whose
    node ids the merge invalidates. Run after the merge it produces
    `has_access_to` edges to nodes that no longer exist — on dvmcp that
    is six dangling edges and a `FOREIGN KEY constraint failed` at write
    time. Before the merge, the merge's id remap re-points those edges to
    the detector nodes, which is what the graph has always contained.
    (C4 listed `entry_to_sink` at the step after the merge until step 4;
    the code was right and the table was corrected to match it.)
    `untrusted_sources` is before the merge for the same reason, and
    after `entry_points` because a read inside a route handler resolves
    to the entry point node, which that stage creates.
  - `untrusted_source_writers` (1.3a.1) runs **after** the merge, for the
    opposite reason: the `mcp_server` nodes it matches against arrive in
    two waves, and the framework-detected one — the kind that carries
    the file the rule matches on — only exists once `detector_merge` has
    run. It runs before `flow_kind` so the one derivation classifies the
    edge it emits.
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
from .untrusted_source_writers import UntrustedSourceWriterStage
from .untrusted_sources import UntrustedSourceStage

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
    UntrustedSourceStage(),
    LlmEndpointStage(),
    DelegationEdgeStage(),
    EntryToSinkStage(),
    DetectorMergeStage(),
    UntrustedSourceWriterStage(),
    FlowKindStage(),
    CrossFileResolutionStage(),
    McpEntrySynthesisStage(),
    OrphanEntryStage(),
    BodyAttributeStage(),
)
