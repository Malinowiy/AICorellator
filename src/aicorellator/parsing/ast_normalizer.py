"""AST Normalizer — the pipeline that turns scan outputs into a graph.

One entry point:

    normalize(ctx) -> GraphDelta

`ctx` is a `NormalizeContext` (`make_context` builds one): the manifest,
TSA symbols, entry points, LLM configs, capabilities and detector output,
plus the per-scan caches and the indexes the stages share. The pipeline
itself is `normalizer_stages.STAGES` — fifteen stages, one per step of
the C4 L3 Phase 1 pipeline, each with an `apply(delta, ctx) -> delta`
(`normalizer_stages.base.NormalizerStage`).

What the stages produce, in one place for orientation:

Edges:
  - provides_tool  (mcp_server → tool)              flow_kind=data|control
  - has_access_to  (tool → sink)                    flow_kind=data|control
  - has_access_to  (untrusted_source → reader)      flow_kind=data  (1.3a)
  - delegates_to   (entry_point → agent)            flow_kind=control
  - delegates_to   (agent → agent, orchestrator)    flow_kind=control
  - has_access_to  (agent → mcp_server)             flow_kind=control

Nodes:
  - agent, llm_endpoint (from classification)
  - mcp_server (from file paths)
  - tool, sink (from methods)
  - guardrail (from classification)
  - entry_point (from entry_points)
  - untrusted_source (from call sites, 1.3a)
  - llm_endpoint (from llm_extractor configs)
  - framework-specific nodes (from framework detectors)

Edges NOT derived here (require source analysis beyond TSA):
  - uses_model     (agent → llm_endpoint)

Source beyond the TSA index is read by three stages — `intra_file_edges`
(the Flow Analyzer for `has_access_to`, ADR-0015), `untrusted_sources`
(the call-site walk for the source nodes, 1.3a) and `body_attributes`
(the tool/sink analyzers, 1.2a.1). All go through the context's module
cache, so a file is parsed at most once per scan.

The helpers this module used to own — node constructors, id derivation,
the file indexes and their resolvers — now live in
`normalizer_helpers.py`, and the names are re-exported below so existing
callers and tests do not have to care where they moved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..graph.models import Edge, Node
from .normalizer_context import NormalizeContext, make_context
from .normalizer_helpers import (
    CONTROL_FLOW,
    HOST_ID,
    STRUCTURAL_FLOW,
    caps_for_symbol,
    cli_entry_point_id,
    edge_id,
    entry_point_id,
    find_sink_call,
    first_definition,
    flow_for_tool_node,
    index_classes,
    index_functions,
    llm_endpoint_id,
    llm_endpoint_name,
    mcp_entry_point_id,
    mcp_server_id,
    node_from_class,
    node_from_method,
    node_line_start,
    parse_python_file,
    resolve_definition,
    server_name_for_file,
    sha256,
    streamlit_entry_point_id,
)
from .normalizer_stages import STAGES


log = logging.getLogger(__name__)


@dataclass
class GraphDelta:
    """Nodes and edges produced by normalization."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


def normalize(ctx: NormalizeContext) -> GraphDelta:
    """Run the stage pipeline over one scan's outputs.

    Args:
        ctx: inputs, caches and derived state for this run — see
            `make_context`.

    Returns:
        The `GraphDelta` the stages built. Every node and edge id is
        deterministic; the same context produces the same delta.
    """
    delta = GraphDelta()
    for stage in STAGES:
        delta = stage.apply(delta, ctx)
    return delta


# --- Backward-compatible private spellings -------------------------
# The helpers moved to `normalizer_helpers.py` in step 4 and their names
# lost the leading underscore there, because they are that module's API
# for the stages. These aliases keep the old spellings working for
# callers (and the normalizer's tests) that import them from here.
_caps_for_symbol = caps_for_symbol
_cli_entry_point_id = cli_entry_point_id
_edge_id = edge_id
_entry_point_id = entry_point_id
_find_sink_call = find_sink_call
_first_definition = first_definition
_flow_for_tool_node = flow_for_tool_node
_index_classes = index_classes
_index_functions = index_functions
_llm_endpoint_id = llm_endpoint_id
_llm_endpoint_name = llm_endpoint_name
_mcp_entry_point_id = mcp_entry_point_id
_mcp_server_id = mcp_server_id
_node_from_class = node_from_class
_node_from_method = node_from_method
_node_line_start = node_line_start
_parse_python_file = parse_python_file
_resolve_definition = resolve_definition
_server_name_for_file = server_name_for_file
_sha256 = sha256
_streamlit_entry_point_id = streamlit_entry_point_id
