"""The normalizer pipeline — one stage per step (step 4).

`STAGES` is the pipeline, in execution order. `normalize(ctx)` runs it
straight through:

    for stage in STAGES:
        delta = stage.apply(delta, ctx)

The order is the C4 L3 Phase 1 order — the rows of the AST Normalizer
stage table in `C4_L3_Phase1.md`, which lists these stages in this
order:

     1  mcp_server_nodes       mcp_server nodes from paths
     2  guardrail_capability   validates_input capability (B.4-gap-1)
     3  class_nodes            class nodes (classification + capabilities)
     4  method_nodes           method nodes; tools/sinks indexed per file
     5  intra_file_edges       provides_tool, has_access_to (same file)
     6  entry_points           entry_point nodes
     7  untrusted_sources      untrusted_source nodes + reader edges (1.3a)
     8  llm_endpoints          llm_endpoint nodes from configs
     9  delegation_edges       entry → agent, agent → agent, agent → server
    10  entry_to_sink          entry → same-file sink reachability
    11  detector_merge         detector nodes authoritative; id remap
    12  untrusted_source_writers  writer → writes_to → store (B.3c, B.3b)
    13  flow_kind              flow_kind = control on every edge (ADR-0016)
    14  dynamic_mcp_invocation agent → uses_tool → tool (1.3a.1.2)
    15  cross_file_resolution  uses_tool across modules (Resolver 1)
    16  mcp_entry_synthesis    virtual MCP entry (Resolver 3)
    17  orphan_entry           orphan entry → single agent (Resolver 2)
    18  body_attributes        sink_data_flow / returns_data backfill
    19  lethal_trifecta        lethal trifecta as a node attribute (1.3d)
    20  store_resolver         store nodes, writes_to / reads_from (B.3b)
    21  guardrail_edges        guards (guardrail → component), B.4a

Note on the order:

  - `guardrail_capability` (B.4-gap-1) runs at 2 — after the MCP server
    nodes and before `class_nodes` / `method_nodes`, and it is the only
    constraint on it: the stage writes a capability into
    `ctx.capabilities`, and those two stages are what read the map and
    turn it into nodes (`normalizer_stages.guardrail_capability`). It
    reads the capability map and the flat call graph rather than the
    delta, so no node stage has to have run for it, and it emits no node
    or edge of its own.
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
    `writes_to` edges stage 11 emitted — through the store the write side
    ends at since B.3b-mcp-catalogue, or onto the source before it — and
    it is a resolver, so it sits with the other post-`flow_kind`
    resolvers and passes `CONTROL_FLOW` the way they do.
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
  - `store_resolver` (B.3b) runs after the merge and after everything
    that builds a node or an edge, because `StoreResolver` resolves both
    ends of a store link against the delta: the component a write call
    sits in, and the `untrusted_source` a read belongs to, are nodes
    stages 2–10 created or re-pointed. It needs nothing `guardrail_edges`
    writes and vice versa, so its position relative to that stage is
    free, and it takes 19 so the stage that documents itself as last
    keeps that slot. `flow_kind` (stage 12) is not a constraint: the
    resolver classifies its own edges `structural` (ADR-0021 §4). See
    `normalizer_stages.store_resolver`.
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
from .guardrail_capability import GuardrailCapabilityStage
from .guardrail_edges import GuardrailEdgeStage
from .intra_file_edges import IntraFileEdgeStage
from .lethal_trifecta import LethalTrifectaStage
from .llm_endpoints import LlmEndpointStage
from .mcp_entry_synthesis import McpEntrySynthesisStage
from .mcp_server_nodes import McpServerNodeStage
from .method_nodes import MethodNodeStage
from .orphan_entry import OrphanEntryStage
from .store_resolver import StoreResolverStage
from .untrusted_source_writers import UntrustedSourceWriterStage
from .untrusted_sources import UntrustedSourceStage

__all__ = ["STAGES", "NormalizerStage"]


# The pipeline. A tuple, not a configurable list: disabling a stage is a
# debugging concern for a later step, and an operator who needs it can
# run the stages by hand.
STAGES: tuple[NormalizerStage, ...] = (
    McpServerNodeStage(),
    GuardrailCapabilityStage(),
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
    StoreResolverStage(),
    GuardrailEdgeStage(),
)
