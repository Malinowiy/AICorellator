"""The normalizer pipeline — one stage per step (step 4).

`STAGES` is the pipeline, in execution order. `normalize(ctx)` runs it
straight through:

    for stage in STAGES:
        delta = stage.apply(delta, ctx)

The order is the C4 L3 Phase 1 order — the rows of the AST Normalizer
stage table in `C4_L3_Phase1.md`, which lists these stages in this
order:

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
    11  untrusted_source_writers  writer → writes_to → source (1.3a.1, B.2)
    12  flow_kind              flow_kind = control on every edge (ADR-0016)
    13  dynamic_mcp_invocation agent → uses_tool → tool (1.3a.1.2)
    14  cross_file_resolution  uses_tool across modules (Resolver 1)
    15  mcp_entry_synthesis    virtual MCP entry (Resolver 3)
    16  orphan_entry           orphan entry → single agent (Resolver 2)
    17  body_attributes        sink_data_flow / returns_data backfill
    18  lethal_trifecta        lethal trifecta as a node attribute (1.3d)
    19  guardrail_edges        guards (guardrail → component), B.4a

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
    run. It runs before `flow_kind` so the kind is pinned once, by
    `FlowKindStage`, rather than the edge arriving at that stage carrying
    a `flow_kind` of its own. B.2's two call-written families want the
    same position for the same reason: the component a write belongs to
    is a node stages 2–5 created, and the merge may re-point it, so the
    edge is drawn against the node the merge settled on.
  - `dynamic_mcp_invocation` (1.3a.1.2) runs after both: it walks the
    `writes_to` edges stage 11 emitted, and it is a resolver, so it sits
    with the other post-`flow_kind` resolvers and passes `CONTROL_FLOW`
    the way they do.
  - `BodyAttributeStage` runs late, not at node construction. Its
    *position* is load-bearing for the same kind of reason: the merge can
    replace a TSA node with a detector node that arrives without the
    attributes, and the replacement is what ends up in the graph.
  - `LethalTrifectaStage` (1.3d) runs after it, and last-but-one. It shares
    the "after the merge" constraint and has no other, so it takes the end
    of the pipeline, where the node set is final. It reads capabilities
    rather than source, which is why it is not a branch inside
    `body_attributes` — see `normalizer_stages.lethal_trifecta`.
  - `guardrail_edges` (B.4a) runs last, and after `lethal_trifecta`. It
    shares the same single constraint — the detector merge, which is what
    puts a `guardrail` node in the delta — and no other, and the end of
    the pipeline is where the node set is final. It is `structural`-only,
    so `flow_kind`'s position is not a constraint on it either way. See
    `normalizer_stages.guardrail_edges`.
"""

from __future__ import annotations

from .base import NormalizerStage
from .body_attributes import BodyAttributeStage
from .class_nodes import ClassNodeStage
from .cross_file_resolution import CrossFileResolutionStage
from .delegation_edges import DelegationEdgeStage
from .detector_merge import DetectorMergeStage
from .dynamic_mcp_invocation import DynamicMcpInvocationStage
from .entry_points import EntryPointStage
from .entry_to_sink import EntryToSinkStage
from .flow_kind import FlowKindStage
from .guardrail_edges import GuardrailEdgeStage
from .intra_file_edges import IntraFileEdgeStage
from .lethal_trifecta import LethalTrifectaStage
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
    DynamicMcpInvocationStage(),
    CrossFileResolutionStage(),
    McpEntrySynthesisStage(),
    OrphanEntryStage(),
    BodyAttributeStage(),
    LethalTrifectaStage(),
    GuardrailEdgeStage(),
)
