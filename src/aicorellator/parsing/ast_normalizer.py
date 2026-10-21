"""AST Normalizer — turns TSA symbols into Node/Edge objects.

Responsibilities:
  1. Create Node objects for classified symbols.
  2. Create edges between nodes using simple heuristics.
  3. Attach capabilities (ADR-0009) to nodes.
  4. Merge framework detector nodes and edges (ADR-0011).
  5. Classify every edge's flow_kind (ADR-0015). For has_access_to
     (tool → sink) a Flow Analyzer inspects the tool body.
  6. Attach flow attributes to tool and sink nodes (1.2a.1): a tool
     records `returns_data`, a sink records `sink_data_flow`. A sink
     named after a class is analyzed by the class-body variant
     (1.2a.5). Both are prompt signals, not chain filters.

Steps 5 and 6 are the only places where source beyond the TSA index is
read. They share one per-scan module cache, so a file is parsed at most
once.

Edges derived here:
  - provides_tool  (mcp_server → tool)              flow_kind=data|control
  - has_access_to  (tool → sink)                    flow_kind=data|control
  - delegates_to   (entry_point → agent)            flow_kind=control
  - delegates_to   (agent → agent, orchestrator)    flow_kind=control
  - has_access_to  (agent → mcp_server)             flow_kind=control

Nodes derived here:
  - agent, llm_endpoint (from classification)
  - mcp_server (from file paths)
  - tool, sink (from methods)
  - guardrail (from classification)
  - entry_point (from entry_points)
  - llm_endpoint (from llm_extractor configs)
  - framework-specific nodes (from framework detectors)

Edges NOT derived here (require source analysis beyond TSA):
  - uses_model     (agent → llm_endpoint)
"""

from __future__ import annotations

import ast
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from ..graph.models import Edge, Node
from ..ingestion.discovery import FileManifest
from ..patterns import MCP_DIR_SEGMENT, MCP_SERVERS_SEGMENT
from .capability_extractor import FileCapabilities
from .classification import (
    Classification,
    classify_class,
    classify_file,
    classify_method,
)
from .entry_points import EntryPointOutput
from .flow import (
    FunctionNode,
    FlowDecision,
    VALID_KINDS,
    analyze_has_access_to,
    analyze_sink_body,
    analyze_sink_class,
    analyze_tool_returns,
    flow_kind_for,
)
from .framework_detectors import DetectionResult
from .llm_extractor import LLMExtractorOutput
from .models import TSAOutput, TSASymbol
from .framework_detectors.cross_file import resolve_agent_tool_edges
from ..patterns import MCP_DIR_SEGMENT, MCP_SERVERS_SEGMENT, SINK_CAPABILITIES


log = logging.getLogger(__name__)

HOST_ID = "localhost"       # Phase 0/1 — single host


# Flow-kind values for edges whose kind fixes the classification
# (ADR-0015, decisions 4 and 5).
#
# `uses_model`, `uses_tool`, `provides_tool` and `delegates_to` are
# control flow with no confirmed data flow: `control` +
# `data_flow_confirmed = "unknown"`.
#
# `provides_tool` is control, not structural: "the agent can call the
# tool" is, for attack-path purposes, the same as "the agent calls the
# tool". The access path is the candidate attack path until runtime
# monitoring (Phase 2) can distinguish an exposed tool from an invoked
# one. `guards` stays structural — a guardrail is a defensive control,
# not a step in an attack path.
# Reserved for `guards` (guardrail → component). No resolver emits
# that edge yet; the constant is kept so the kind has one declared
# source of truth.
STRUCTURAL_FLOW: dict[str, str] = {"flow_kind": "structural"}
CONTROL_FLOW: dict[str, str] = {
    "flow_kind": "control",
    "data_flow_confirmed": "unknown",
}


def _flow_for_tool_node(tool_node: Node) -> dict[str, str]:
    """Flow attributes for a server → tool edge (1.2a.2).

    A framework detector records the tool's signature result on the tool
    node as `tool_data_flow` (`parameter_match` / `no_args`). The edge
    that exposes the tool inherits it: the MCP protocol is the channel,
    so a tool with parameters is handed the client's data by the
    framework itself.

    A tool node without the attribute — every node the normalizer
    creates from TSA symbols — keeps the control default. Enrichment
    never invents a value the detector did not record.

    The node attribute is the decision's `kind` (attributes are JSON,
    so the object itself cannot be stored); the decision is rebuilt
    here, with the node's `framework` attribute supplying the
    provenance — the same `enrichment:<framework>` source the detector
    used when it made the decision.
    """
    data_flow = tool_node.attributes.get("tool_data_flow")
    if not isinstance(data_flow, str) or data_flow not in VALID_KINDS:
        return CONTROL_FLOW
    framework = tool_node.attributes.get("framework", "unknown")
    decision = FlowDecision(
        data_flow, source=f"enrichment:{framework}"
    )
    return {
        "flow_kind": flow_kind_for(decision),
        "data_flow_confirmed": decision.kind,
    }


@dataclass
class GraphDelta:
    """Nodes and edges produced by normalization."""
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


def normalize(
    manifest: FileManifest,
    tsa_output: TSAOutput,
    entry_points: EntryPointOutput | None = None,
    llm_output: LLMExtractorOutput | None = None,
    capabilities: dict[str, FileCapabilities] | None = None,
    framework_detection: DetectionResult | None = None,
) -> GraphDelta:
    """Build graph delta from manifest, TSA output, entry points, LLM configs.

    Args:
        manifest: file list from ingestion.discover()
        tsa_output: symbols from parsing.tsa_worker.run()
        entry_points: routes, agent calls, and MCP usages from
                      parsing.entry_points.run(). Pass None to skip.
        llm_output: LLM endpoints from parsing.llm_extractor.run().
                    Pass None to skip.
        capabilities: per-file capabilities from
                      parsing.capability_extractor.extract().
                      Keys are relative file paths. Pass None to skip.
        framework_detection: DetectionResult from
                             parsing.detectors_runner.run().
                             Contains framework-specific nodes
                             (agent, tool, llm_endpoint) and
                             edges. Pass None to skip.
    """
    delta = GraphDelta()
    entry_points = entry_points or EntryPointOutput()
    llm_output = llm_output or LLMExtractorOutput()
    capabilities = capabilities or {}
    framework_detection = framework_detection or DetectionResult()

    # --- Per-scan module cache ---
    # Two consumers read source beyond the TSA index: the inline Flow
    # Analyzer for `has_access_to` (ADR-0015) and the tool/sink body
    # analyzers (1.2a.1). Both go through this cache, so a file is read
    # and parsed at most once per scan. A missing file, an unreadable
    # file or a syntax error yields None — unit tests build manifests
    # whose abs_path does not exist on disk, so the fallback is a normal
    # path, not an error path.
    abs_path_by_path = {entry.path: entry.abs_path for entry in manifest.files}
    module_cache: dict[str, ast.Module | None] = {}
    functions_cache: dict[str, dict[tuple[str, int], FunctionNode]] = {}
    classes_cache: dict[str, dict[tuple[str, int], ast.ClassDef]] = {}

    def _module_for(file_path: str) -> ast.Module | None:
        """Parsed module for a manifest file, cached per scan."""
        if file_path in module_cache:
            return module_cache[file_path]
        module = _parse_python_file(abs_path_by_path.get(file_path))
        module_cache[file_path] = module
        return module

    def _functions_for(
        file_path: str,
    ) -> dict[tuple[str, int], FunctionNode]:
        """Function index for a manifest file, cached per scan.

        Keyed by `(name, line_start)` (1.2a.3f) — see
        `_index_functions`.
        """
        if file_path in functions_cache:
            return functions_cache[file_path]
        module = _module_for(file_path)
        functions = _index_functions(module) if module is not None else {}
        functions_cache[file_path] = functions
        return functions

    def _classes_for(
        file_path: str,
    ) -> dict[tuple[str, int], ast.ClassDef]:
        """Class index for a manifest file, cached per scan.

        Keyed by `(name, line_start)` (1.2a.3f) — see `_index_classes`.
        """
        if file_path in classes_cache:
            return classes_cache[file_path]
        module = _module_for(file_path)
        classes = _index_classes(module) if module is not None else {}
        classes_cache[file_path] = classes
        return classes

    def _body_attributes(node: Node) -> dict[str, object]:
        """Flow attributes for a tool or sink node (1.2a.1, 1.2a.5).

        A tool records whether it returns data to its caller; a sink
        records how its own body passes data to a dangerous call. The
        body is resolved in the file's function index by name **and**
        `line_start` (1.2a.3f), so when a file defines the same name
        twice — `toggle_enabled` at
        `finbot/core/data/repositories.py:664` and `:1426` — each node
        reads its own definition instead of the first one. A sink whose
        name is a **class** — a capability-based sink such as
        `UserProfileRepository` — falls back to the class index and is
        analyzed by `analyze_sink_class` (1.2a.5); a tool is always a
        function, so it never consults the class index.

        When the body cannot be resolved — a heuristic node with no
        function or class of that name, or a file that could not be
        parsed — the fallback is `returns_data = False` /
        `sink_data_flow = "unknown"`.
        """
        source_file = str(node.attributes.get("source_file", ""))
        line_start = _node_line_start(node)
        func = _resolve_definition(
            _functions_for(source_file), node.name, line_start
        )
        if node.kind == "tool":
            return {
                "returns_data": (
                    func is not None and analyze_tool_returns(func)
                ),
            }
        if func is not None:
            # The node attributes carry the decision's kind; the
            # decision's evidence and source stay with the producer
            # (step 1 adds them; nothing persists them yet).
            return {"sink_data_flow": analyze_sink_body(func).kind}
        cls = _resolve_definition(
            _classes_for(source_file), node.name, line_start
        )
        if cls is not None:
            return {"sink_data_flow": analyze_sink_class(cls).kind}
        return {"sink_data_flow": "unknown"}

    # --- MCP servers from files ---
    mcp_servers: dict[str, str] = {}
    for file_entry in manifest.files:
        c = classify_file(file_entry.path)
        if c is None:
            continue
        server_name = c.attributes["server_name"]
        if server_name in mcp_servers:
            continue
        node_id = _mcp_server_id(server_name)
        mcp_servers[server_name] = node_id
        delta.nodes.append(Node(
            id=node_id,
            kind="mcp_server",
            name=server_name,
            host_id=HOST_ID,
            attributes={"server_name": server_name},
            content_hash=None,
        ))

    # --- Classes ---
    for symbol in tsa_output.symbols:
        if symbol.kind != "class":
            continue
        symbol_caps = _caps_for_symbol(capabilities, symbol.file_path, symbol.name)
        c = classify_class(symbol.name, symbol.file_path, capabilities=symbol_caps)
        if c is None:
            continue
        node = _node_from_class(symbol, c, capabilities)
        if node.kind == "sink":
            # A class can be a sink (capability-based). The body is a
            # class, not a function, so `_body_attributes` resolves it
            # through the class index and `analyze_sink_class` (1.2a.5).
            node.attributes.update(_body_attributes(node))
        delta.nodes.append(node)

    # --- Methods (tools and sinks) ---
    tools_by_file: dict[str, list[Node]] = {}
    sinks_by_file: dict[str, list[Node]] = {}

    for symbol in tsa_output.symbols:
        if symbol.kind != "method":
            continue
        symbol_caps = _caps_for_symbol(capabilities, symbol.file_path, symbol.name)
        c = classify_method(
            symbol.name, symbol.file_path, symbol.class_name,
            capabilities=symbol_caps,
        )
        if c is None:
            continue
        node = _node_from_method(symbol, c, capabilities)
        if node.kind in ("tool", "sink"):
            # Enrichment for the prompt (1.2a.1): a tool records whether
            # it hands data back, a sink how its own body feeds a
            # dangerous call. Neither changes flow_kind.
            node.attributes.update(_body_attributes(node))
        delta.nodes.append(node)

        if c.kind == "tool":
            tools_by_file.setdefault(symbol.file_path, []).append(node)
        elif c.kind == "sink":
            sinks_by_file.setdefault(symbol.file_path, []).append(node)

    # --- Edges: provides_tool (mcp_server -> tool) ---
    for file_path, tools in tools_by_file.items():
        server_name = _server_name_for_file(file_path)
        if server_name is None:
            continue
        server_id = mcp_servers.get(server_name)
        if server_id is None:
            continue
        for tool_node in tools:
            delta.edges.append(Edge(
                id=_edge_id(server_id, tool_node.id, "provides_tool"),
                source_id=server_id,
                target_id=tool_node.id,
                kind="provides_tool",
                host_id=HOST_ID,
                # Access path, not a declaration (ADR-0015, decision 5):
                # "the agent can call the tool" is the candidate attack
                # path until runtime monitoring separates the two.
                # A framework detector may have enriched the tool node
                # with its signature result (1.2a.2); the edge inherits
                # it and nothing else changes.
                **_flow_for_tool_node(tool_node),
            ))

    # --- Edges: has_access_to (tool -> sink) ---
    #
    # The edge is a candidate: every tool in a file is linked to every
    # sink in the same file. Whether data actually flows from the tool
    # into the sink depends on the tool's body. The Flow Analyzer
    # (ADR-0015) inspects the sink call inside the tool body and
    # records how data reaches it; that result drives `flow_kind`.
    #
    # If the tool body or the sink call cannot be resolved, the edge
    # falls back to `control` / `unknown`.
    for file_path, tools in tools_by_file.items():
        sinks = sinks_by_file.get(file_path, [])
        if not sinks:
            continue
        functions = _functions_for(file_path)
        for tool_node in tools:
            tool_func = _resolve_definition(
                functions, tool_node.name, _node_line_start(tool_node)
            )
            for sink_node in sinks:
                sink_call = (
                    _find_sink_call(tool_func, sink_node.name)
                    if tool_func is not None
                    else None
                )
                if sink_call is not None:
                    # Flow Analyzer is inline for now. If cross-file data-flow
                    # analysis is added (backlog), it moves to a separate pass.
                    decision = analyze_has_access_to(tool_func, sink_call)
                else:
                    # The sink call is not in the tool body: reachability
                    # without evidence, so the decision says so.
                    decision = FlowDecision(
                        "unknown",
                        ("no sink call in the tool body",),
                        source="edge_analyzer",
                    )
                delta.edges.append(Edge(
                    id=_edge_id(tool_node.id, sink_node.id, "has_access_to"),
                    source_id=tool_node.id,
                    target_id=sink_node.id,
                    kind="has_access_to",
                    host_id=HOST_ID,
                    flow_kind=flow_kind_for(decision),
                    data_flow_confirmed=decision.kind,
                ))


    # --- Entry points ---
    entry_node_by_handler: dict[str, str] = {}
    for ep in entry_points.entry_points:
        if ep.method == "CLI":
            node_id = _cli_entry_point_id(ep.file_path)
            attrs = {
                "source_file": ep.file_path,
                "line_start": ep.line,
                "method": "CLI",
                "entry_kind": "cli",
                "path": ep.path,
            }
            name = f"CLI:{ep.file_path}"
        elif ep.method == "STREAMLIT":
            node_id = _streamlit_entry_point_id(ep.file_path, ep.path)
            attrs = {
                "source_file": ep.file_path,
                "line_start": ep.line,
                "method": "STREAMLIT",
                "entry_kind": "streamlit",
                "path": ep.path,
            }
            name = f"Streamlit:{ep.file_path}:{ep.path}"
        else:
            node_id = _entry_point_id(ep)
            attrs = {
                "source_file": ep.file_path,
                "line_start": ep.line,
                "method": ep.method,
                "path": ep.path,
                "handler": ep.handler,
            }
            name = f"{ep.method} {ep.path}"

        entry_node_by_handler[ep.handler] = node_id
        delta.nodes.append(Node(
            id=node_id,
            kind="entry_point",
            name=name,
            host_id=HOST_ID,
            attributes=attrs,
        ))
    # --- LLM endpoints from configs ---
    for llm_ep in llm_output.deduplicated():
        node_id = _llm_endpoint_id(llm_ep)
        delta.nodes.append(Node(
            id=node_id,
            kind="llm_endpoint",
            name=_llm_endpoint_name(llm_ep),
            host_id=HOST_ID,
            attributes={
                "source_file": llm_ep.source_file,
                "provider": llm_ep.provider,
                "model": llm_ep.model or "",
                "base_url": llm_ep.base_url or "",
            },
        ))

    # --- Indexes for edge derivation ---
    agent_node_by_name: dict[str, str] = {
        n.name: n.id for n in delta.nodes if n.kind == "agent"
    }
    agent_node_by_file: dict[str, str] = {
        n.attributes.get("source_file", ""): n.id
        for n in delta.nodes
        if n.kind == "agent"
    }

    # --- Edges: entry_point → agent (delegates_to) ---
    for call in entry_points.agent_calls:
        if call.is_agent_to_agent:
            continue
        entry_id = entry_node_by_handler.get(call.handler)
        agent_id = agent_node_by_name.get(call.agent_name)
        if entry_id is None or agent_id is None:
            continue
        delta.edges.append(Edge(
            id=_edge_id(entry_id, agent_id, "delegates_to"),
            source_id=entry_id,
            target_id=agent_id,
            kind="delegates_to",
            host_id=HOST_ID,
            **CONTROL_FLOW,
        ))

    # --- Edges: agent → agent (delegates_to) ---
    for call in entry_points.agent_calls:
        if not call.is_agent_to_agent:
            continue
        source_id = agent_node_by_file.get(call.file_path)
        target_id = agent_node_by_name.get(call.agent_name)
        if source_id is None or target_id is None:
            continue
        if source_id == target_id:
            continue
        delta.edges.append(Edge(
            id=_edge_id(source_id, target_id, "delegates_to"),
            source_id=source_id,
            target_id=target_id,
            kind="delegates_to",
            host_id=HOST_ID,
            **CONTROL_FLOW,
        ))

    # --- Edges: entry_point → sink (same file, transitive) ---
    # A route handler reaches a sink if the handler itself is a sink
    # function, or if the handler can reach a sink function through
    # a chain of local (same-file) calls.
    #
    # This is a control-flow reachability analysis over a per-file
    # call graph. It is NOT a dataflow analysis: we track which
    # functions a handler calls, not which values flow where.
    # ADR-0001 forbids full dataflow; control-flow reachability is
    # in scope, and the entry-point detector already does similar
    # name-based call inspection.
    #
    # Cross-file sinks are not followed here. A handler that calls a
    # helper in another module is not linked to sinks in that module.
    # That is a separate problem; a cross-file resolver would need
    # the import table and a project-wide call graph.
    #
    # False positives are accepted (ADR-0009): the LLM ensemble in
    # Phase 3 filters them. Better a spurious edge than a missing one.
    entry_sink_edges = _link_entry_points_to_sinks(
        entry_points=entry_points,
        sinks_by_file=sinks_by_file,
        entry_node_by_handler=entry_node_by_handler,
        imports_by_file=entry_points.imports,
    )
    delta.edges.extend(entry_sink_edges)


    # --- Edges: agent → mcp_server (has_access_to) ---
    for usage in entry_points.mcp_usages:
        if not usage.is_agent_module:
            continue
        agent_id = agent_node_by_file.get(usage.file_path)
        server_id = mcp_servers.get(usage.mcp_server_name)
        if agent_id is None or server_id is None:
            continue
        delta.edges.append(Edge(
            id=_edge_id(agent_id, server_id, "has_access_to"),
            source_id=agent_id,
            target_id=server_id,
            kind="has_access_to",
            host_id=HOST_ID,
            # Agent → MCP server is reachability, not a call into a sink
            # (ADR-0015): there is no tool body and no sink call to
            # analyze, so data flow stays unconfirmed.
            **CONTROL_FLOW,
        ))

    # --- Framework detector nodes and edges ---
    # Detector nodes are authoritative for kind. If a node with the
    # same (name, source_file) already exists (from TSA heuristics),
    # it is replaced. Capabilities from the existing node are
    # preserved if the detector node has none.
    existing_by_name_file: dict[tuple[str, str], Node] = {}
    for node in delta.nodes:
        key = (node.name, node.attributes.get("source_file", ""))
        existing_by_name_file[key] = node

    # Map old node id → new node id, for edges that need re-pointing.
    id_remap: dict[str, str] = {}

    for detector_node in framework_detection.nodes:
        key = (
            detector_node.name,
            detector_node.attributes.get("source_file", ""),
        )
        existing = existing_by_name_file.get(key)

        if existing is not None:
            # Preserve capabilities from existing if detector has none.
            if not detector_node.capabilities and existing.capabilities:
                detector_node.capabilities = list(existing.capabilities)
            # Remove existing node.
            delta.nodes = [n for n in delta.nodes if n is not existing]
            # Record id remap so edges can be re-pointed below.
            id_remap[existing.id] = detector_node.id

        delta.nodes.append(detector_node)
        existing_by_name_file[key] = detector_node

    # Backfill flow attributes for tool and sink nodes that do not carry
    # them yet (1.2a.1). Detector nodes are authoritative for kind and
    # replace the TSA node of the same (name, source_file) — which drops
    # the attributes set above — and detectors also create tool nodes of
    # their own. Both are resolved through the same module cache, so this
    # costs no extra file reads. A detector-created sink node named after
    # a class gets its class body analyzed here too (1.2a.5): the class
    # fallback lives in `_body_attributes`, not in the caller.
    for node in delta.nodes:
        if node.kind == "tool" and "returns_data" not in node.attributes:
            node.attributes.update(_body_attributes(node))
        elif node.kind == "sink" and "sink_data_flow" not in node.attributes:
            node.attributes.update(_body_attributes(node))

    # Re-point edges from removed nodes to their replacements.
    # Without this, edges to a TSA node that got replaced by a
    # detector node would be silently dropped.
    if id_remap:
        new_edges: list[Edge] = []
        seen: set[str] = set()
        for edge in delta.edges:
            new_source = id_remap.get(edge.source_id, edge.source_id)
            new_target = id_remap.get(edge.target_id, edge.target_id)
            if new_source != edge.source_id or new_target != edge.target_id:
                new_id = _edge_id(new_source, new_target, edge.kind)
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

    existing_edge_ids = {e.id for e in delta.edges}
    for edge in framework_detection.edges:
        if edge.id in existing_edge_ids:
            continue
        delta.edges.append(edge)
        existing_edge_ids.add(edge.id)

    # --- Edges: MCP entry point → tool (A3, ADR-0013) ---
    # A framework-detected MCP server (from FastMCP or the MCP Python
    # SDK) has no HTTP route. To let chain enumeration start at it, we
    # synthesize a virtual entry point per server and link it to every
    # tool whose parent_server matches. The chain semantics are: "an
    # MCP client can invoke any tool this server exposes".
    #
    # Only detector-created mcp_servers (those carrying a `framework`
    # attribute) get virtual entry points. Servers discovered via the
    # file-path heuristic (mcp/servers/*/server.py) already carry
    # provides_tool edges and are left alone.
    mcp_entry_edges = _link_mcp_entries_to_tools(delta=delta)
    delta.edges.extend(mcp_entry_edges)

    # --- Cross-file edges: agent → tool (uses_tool) ---
    # Framework detectors are per-file (ADR-0011): an agent created
    # in agent.py that uses tools imported from tools.py has no
    # uses_tool edge from the detector alone. Resolve those here,
    # using the manifest's import table. False positives are
    # accepted (ADR-0009): Phase 3 (LLM ensemble) filters them.
    cross_edges = resolve_agent_tool_edges(
        manifest=manifest,
        detector_nodes=framework_detection.nodes,
        host_id=HOST_ID,
    )
    for edge in cross_edges:
        if edge.id in existing_edge_ids:
            continue
        delta.edges.append(edge)
        existing_edge_ids.add(edge.id)
    # --- Edges: orphan entry_point → single agent (framework fallback) ---
    # When a project has exactly one agent node with framework
    # provenance, and some entry_points have no outgoing
    # delegates_to edge, link them to that agent.
    #
    # Rationale: LangChain 1.0 creates agents through factories
    # (create_agent, get_agent) that return a CompiledStateGraph.
    # The route body then calls agent_graph.invoke(...). Neither
    # the detector (per-file) nor entry_points.py (route-focused)
    # can link them without cross-file dataflow. This resolves the
    # ambiguity at the normalizer level.
    #
    # False positives are accepted (ADR-0009): the LLM ensemble in
    # Phase 3 filters them. Better a spurious edge than no chains.
    orphan_edges = _link_orphan_entry_points(
        delta=delta,
        host_id=HOST_ID,
    )
    delta.edges.extend(orphan_edges)

    return delta

# ============================================================
# Capability lookup
# ============================================================

def _caps_for_symbol(
    capabilities: dict[str, FileCapabilities],
    file_path: str,
    symbol_name: str,
) -> list[str]:
    """Return the sorted capability list for a symbol.

    Returns [] if the file is not in the capabilities map, or if the
    symbol is not found in that file.
    """
    fc = capabilities.get(file_path)
    if fc is None:
        return []
    for sym in fc.symbols:
        if sym.symbol_name == symbol_name:
            return sorted(sym.capabilities)
    return []


# ============================================================
# Canonical ID derivation (from C4_L3_Phase1)
# ============================================================

def _mcp_server_id(server_name: str) -> str:
    h = _sha256(f"mcp_server:{server_name}:{HOST_ID}")
    return f"mcp_server:{h}"

def _mcp_entry_point_id(server_name: str) -> str:
    """Canonical ID for the virtual MCP entry point of a server.

    Distinct from `_entry_point_id` (HTTP routes) so that an MCP entry
    and an HTTP route can never collide, even if they share a name.
    """
    key = f"entry_point:mcp:{server_name}:{HOST_ID}"
    return f"entry_point:{_sha256(key)}"


def _entry_point_id(ep) -> str:
    key = f"entry_point:{ep.file_path}:{ep.method}:{ep.path}:{HOST_ID}"
    return f"entry_point:{_sha256(key)}"

def _cli_entry_point_id(file_path: str) -> str:
    """Canonical ID for a CLI entry point (one per script)."""
    key = f"entry_point:cli:{file_path}:{HOST_ID}"
    return f"entry_point:{_sha256(key)}"


def _streamlit_entry_point_id(file_path: str, call: str) -> str:
    """Canonical ID for a Streamlit entry point (one per call name)."""
    key = f"entry_point:streamlit:{file_path}:{call}:{HOST_ID}"
    return f"entry_point:{_sha256(key)}"

def _llm_endpoint_id(llm_ep) -> str:
    """Canonical ID: sha256(provider + model + base_url + host)."""
    key = (
        f"llm_endpoint:{llm_ep.provider}:"
        f"{llm_ep.model or ''}:{llm_ep.base_url or ''}:{HOST_ID}"
    )
    return f"llm_endpoint:{_sha256(key)}"


def _llm_endpoint_name(llm_ep) -> str:
    """Human-readable name: provider:model, or provider:base_url."""
    if llm_ep.model:
        return f"{llm_ep.provider}:{llm_ep.model}"
    if llm_ep.base_url:
        return f"{llm_ep.provider}:{llm_ep.base_url}"
    return llm_ep.provider


def _node_from_class(
    symbol: TSASymbol,
    c: Classification,
    capabilities: dict[str, FileCapabilities] | None = None,
) -> Node:
    h = _sha256(f"{c.kind}:{symbol.file_path}:{symbol.name}:{HOST_ID}")
    node_id = f"{c.kind}:{h}"
    symbol_caps = _caps_for_symbol(capabilities or {}, symbol.file_path, symbol.name)
    return Node(
        id=node_id,
        kind=c.kind,
        name=symbol.name,
        host_id=HOST_ID,
        attributes={
            "source_file": symbol.file_path,
            "line_start": symbol.line_start,
            **c.attributes,
        },
        capabilities=symbol_caps,
    )


def _node_from_method(
    symbol: TSASymbol,
    c: Classification,
    capabilities: dict[str, FileCapabilities] | None = None,
) -> Node:
    key_parts = [c.kind, symbol.file_path, symbol.name]
    if symbol.class_name:
        key_parts.append(symbol.class_name)
    key_parts.append(HOST_ID)
    h = _sha256(":".join(key_parts))
    node_id = f"{c.kind}:{h}"
    symbol_caps = _caps_for_symbol(capabilities or {}, symbol.file_path, symbol.name)
    return Node(
        id=node_id,
        kind=c.kind,
        name=symbol.name,
        host_id=HOST_ID,
        attributes={
            "source_file": symbol.file_path,
            "line_start": symbol.line_start,
            **c.attributes,
        },
        capabilities=symbol_caps,
    )


def _edge_id(source_id: str, target_id: str, kind: str) -> str:
    h = _sha256(f"{source_id}:{target_id}:{kind}:{HOST_ID}")
    return f"edge:{h}"


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_python_file(abs_path: Path | None) -> ast.Module | None:
    """Read and parse a Python file. Returns None on any failure.

    The Flow Analyzer is name-based (ADR-0001): it needs the file's AST
    but never evaluates it. A missing file, an unreadable file or a
    syntax error all yield None, which the caller treats as
    "cannot confirm data flow".
    """
    if abs_path is None:
        return None
    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.debug("flow analyzer: cannot read %s: %s", abs_path, exc)
        return None
    try:
        return ast.parse(source, filename=str(abs_path))
    except (SyntaxError, ValueError) as exc:
        log.debug("flow analyzer: cannot parse %s: %s", abs_path, exc)
        return None


def _index_functions(
    module: ast.Module,
) -> dict[tuple[str, int], FunctionNode]:
    """Map `(name, line_start)` → node for a parsed module.

    Built once per file. Keyed by position, not by name alone: a module
    may define the same name twice — `toggle_enabled` at
    `finbot/core/data/repositories.py:664` and `:1426`, in two different
    repository classes — and the two bodies are different code. Keying
    by name made the second node read the first body (1.2a.3f); keying
    by position lets `_resolve_definition` pick the definition the node
    came from.

    Every definition is kept, nested functions included, so a node
    inside one resolves to it. Insertion order is `ast.walk` order
    (breadth-first, so module-level definitions first) — the order the
    name fallback in `_resolve_definition` returns, which is the
    pre-1.2a.3f behaviour for a node with no usable `line_start`.
    """
    index: dict[tuple[str, int], FunctionNode] = {}
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            index.setdefault((node.name, node.lineno), node)
    return index


def _index_classes(module: ast.Module) -> dict[tuple[str, int], ast.ClassDef]:
    """Map `(name, line_start)` → node for a parsed module (1.2a.5).

    Built once per file from the module's own top level: TSA reports
    symbols for top-level classes, and a nested class is not a node of
    its own. Keyed by position for the same reason as
    `_index_functions` (1.2a.3f); a name defined twice keeps both
    definitions.
    """
    index: dict[tuple[str, int], ast.ClassDef] = {}
    for node in module.body:
        if isinstance(node, ast.ClassDef):
            index.setdefault((node.name, node.lineno), node)
    return index


# A function or class definition — the values of the two indexes above.
_Definition = TypeVar("_Definition", bound=ast.AST)


def _node_line_start(node: Node) -> int | None:
    """The node's `line_start` when it is a usable integer (1.2a.3f).

    TSA nodes always carry one. Detector-created nodes carry none, and a
    hand-built node may carry a placeholder; both resolve by name.
    """
    line_start = node.attributes.get("line_start")
    if isinstance(line_start, int):
        return line_start
    return None


def _resolve_definition(
    index: dict[tuple[str, int], _Definition],
    name: str,
    line_start: int | None,
) -> _Definition | None:
    """The definition of `name` that a node at `line_start` belongs to.

    Four steps, most precise first (1.2a.3f):

    1. **exact** — a definition starting at `line_start`. This is the
       normal path: TSA reports the definition's own line.
    2. **containing** — a definition whose body spans `line_start`
       (`lineno <= line_start <= end_lineno`). Several can contain it
       when a function is nested in a same-named one; the closest
       preceding `lineno` wins.
    3. **closest preceding** — nothing contains `line_start`, but a
       definition starts before it: an approximate line number resolves
       to the definition the node has already passed, never to a
       following one. A node that lands in the gap between two
       same-named definitions is not evidence about the definition that
       starts after it.
    4. **name fallback** — the first definition of that name in the
       file, which is exactly what the name-keyed index returned before
       this change. Every node without a `line_start` takes this path —
       detector-created nodes carry none — so their resolution is
       unchanged.

    Returns None when the file defines no such name at all, which is the
    `sink_data_flow = "unknown"` / `returns_data = False` path.
    """
    if line_start is None:
        return _first_definition(index, name)
    exact = index.get((name, line_start))
    if exact is not None:
        return exact

    containing: _Definition | None = None
    preceding: _Definition | None = None
    containing_start = -1
    preceding_start = -1
    for (candidate, start), definition in index.items():
        if candidate != name or start > line_start:
            continue
        if start > preceding_start:
            preceding, preceding_start = definition, start
        end = definition.end_lineno or start
        if line_start <= end and start > containing_start:
            containing, containing_start = definition, start

    if containing is not None:
        return containing
    if preceding is not None:
        return preceding
    return _first_definition(index, name)


def _first_definition(
    index: dict[tuple[str, int], _Definition],
    name: str,
) -> _Definition | None:
    """The first definition of `name`, in index (source) order.

    The name-keyed behaviour of the pre-1.2a.3f index: the first
    definition of a name wins.
    """
    for (candidate, _start), definition in index.items():
        if candidate == name:
            return definition
    return None


def _find_sink_call(
    tool_func: FunctionNode,
    sink_name: str,
) -> ast.Call | None:
    """Find the first call to `sink_name` inside `tool_func`'s body.

    Matches a bare call (`delete_file(...)`) and a method call
    (`self.delete_file(...)`) by the called name. Returns None when the
    tool never calls the sink — the edge is then `control` / `unknown`.
    """
    for node in ast.walk(tool_func):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == sink_name:
            return node
        if isinstance(func, ast.Attribute) and func.attr == sink_name:
            return node
    return None


def _server_name_for_file(file_path: str) -> str | None:
    """Extract mcp server name from a file path, or None."""
    parts = file_path.split("/")
    if MCP_DIR_SEGMENT in parts and MCP_SERVERS_SEGMENT in parts:
        try:
            servers_idx = parts.index(MCP_SERVERS_SEGMENT)
        except ValueError:
            return None
        if servers_idx + 2 >= len(parts):
            return None
        name = parts[servers_idx + 1]
        if name.endswith(".py"):
            return None
        return name
    return None

def _link_orphan_entry_points(
    delta: GraphDelta,
    host_id: str,
) -> list[Edge]:
    """Link entry_points with no outgoing edges to the single framework agent.

    Preconditions for linking (all must hold):
      - Exactly one agent node with a `framework` attribute exists.
      - At least one entry_point node exists.
      - The entry_point has no outgoing `delegates_to` edge yet.

    Returns an empty list if the preconditions do not hold. This is
    a deliberate no-op in ambiguous cases (0 or ≥2 framework agents).
    """
    # 1. Find framework agents.
    framework_agents = [
        n for n in delta.nodes
        if n.kind == "agent" and n.attributes.get("framework")
    ]
    if len(framework_agents) != 1:
        return []

    agent_node = framework_agents[0]

    # 2. Find entry_points with no outgoing delegates_to edge.
    sources_with_outgoing: set[str] = {
        e.source_id for e in delta.edges if e.kind == "delegates_to"
    }
    orphan_entries = [
        n for n in delta.nodes
        if n.kind == "entry_point" and n.id not in sources_with_outgoing
    ]
    if not orphan_entries:
        return []

    # 3. Build edges.
    edges: list[Edge] = []
    for entry in orphan_entries:
        edge_id = _edge_id(entry.id, agent_node.id, "delegates_to")
        edges.append(Edge(
            id=edge_id,
            source_id=entry.id,
            target_id=agent_node.id,
            kind="delegates_to",
            host_id=host_id,
            attributes={
                "framework": agent_node.attributes.get("framework", ""),
                "resolved_via": "orphan_entry_to_single_agent",
            },
            **CONTROL_FLOW,
        ))
    return edges

def _link_entry_points_to_sinks(
    entry_points: EntryPointOutput,
    sinks_by_file: dict[str, list[Node]],
    entry_node_by_handler: dict[str, str],
    imports_by_file: dict[str, list[str]],
) -> list[Edge]:
    """Resolve entry_point → sink edges via same-file call reachability.

    For HTTP routes and Streamlit entries: BFS from the handler (or
    module for module-level entries) through the file's call graph.

    For CLI entries: the handler is the module itself. BFS starts at
    every top-level function and every name imported from another
    module, so a CLI script that calls `run()` imported from a
    helper module can reach sinks in that helper.

    Control-flow reachability, not dataflow. False positives accepted
    (ADR-0009).
    """
    edges: list[Edge] = []

    for ep in entry_points.entry_points:
        entry_id = entry_node_by_handler.get(ep.handler)
        if entry_id is None:
            continue

        sinks_here = sinks_by_file.get(ep.file_path, [])
        if not sinks_here:
            continue

        # Determine BFS start nodes.
        if ep.method in ("CLI", "STREAMLIT"):
            # Module-level entry: start from every function defined
            # in the file and every imported name that could resolve
            # to a function in another module.
            starts: set[str] = set(entry_points.call_graph.keys())
            for module in imports_by_file.get(ep.file_path, []):
                # Imported module name; functions called via
                # `module.func(...)` are recorded by their last segment
                # in the call graph. Add the module's last segment too,
                # in case the call graph recorded it as a Name call.
                starts.add(module.split(".")[-1])
        else:
            starts = {ep.handler}

        reachable: set[str] = set()
        for start in starts:
            reachable |= _reachable_functions(
                start=start,
                call_graph=entry_points.call_graph,
            )

        for sink_node in sinks_here:
            if sink_node.name in reachable:
                edges.append(Edge(
                    id=_edge_id(entry_id, sink_node.id, "has_access_to"),
                    source_id=entry_id,
                    target_id=sink_node.id,
                    kind="has_access_to",
                    host_id=HOST_ID,
                    # Control-flow reachability, not dataflow (ADR-0015):
                    # the call graph is name-based, so nothing is known
                    # about what data reaches the sink.
                    **CONTROL_FLOW,
                ))
    # Fallback for CLI and Streamlit entries with no same-file sinks:
    # link to all sinks in the project. The rationale is the same as
    # for the orphan-entry resolver: a CLI script can call into any
    # module, and we do not track cross-file calls here. False
    # positives are accepted (ADR-0009).
    all_sinks: list[Node] = []
    for sinks in sinks_by_file.values():
        all_sinks.extend(sinks)

    if all_sinks:
        for ep in entry_points.entry_points:
            if ep.method not in ("CLI", "STREAMLIT"):
                continue
            entry_id = entry_node_by_handler.get(ep.handler)
            if entry_id is None:
                continue
            # Only add fallback edges if this entry has no outgoing
            # has_access_to edge yet.
            has_edge = any(
                e.source_id == entry_id and e.kind == "has_access_to"
                for e in edges
            )
            if has_edge:
                continue
            for sink_node in all_sinks:
                edges.append(Edge(
                    id=_edge_id(entry_id, sink_node.id, "has_access_to"),
                    source_id=entry_id,
                    target_id=sink_node.id,
                    kind="has_access_to",
                    host_id=HOST_ID,
                    attributes={"resolved_via": "cli_fallback"},
                    **CONTROL_FLOW,
                ))
    return edges

def _link_mcp_entries_to_tools(delta: GraphDelta) -> list[Edge]:
    """Create virtual entry points for framework-detected MCP servers
    and link them to the tools they expose.

    Side effects: appends the new entry_point nodes to `delta.nodes`.
    Returns only the new edges (the caller appends them).
    """
    edges: list[Edge] = []

    # Index tools by parent_server, only those with framework provenance.
    tools_by_parent: dict[str, list[Node]] = {}
    for node in delta.nodes:
        if node.kind != "tool":
            continue
        parent = node.attributes.get("parent_server")
        if parent:
            tools_by_parent.setdefault(parent, []).append(node)

    for mcp_node in delta.nodes:
        if mcp_node.kind != "mcp_server":
            continue
        framework = mcp_node.attributes.get("framework")
        if not framework:
            continue

        entry_id = _mcp_entry_point_id(mcp_node.name)

        delta.nodes.append(Node(
            id=entry_id,
            kind="entry_point",
            name=f"MCP:{mcp_node.name}",
            host_id=HOST_ID,
            attributes={
                "source_file": mcp_node.attributes.get("source_file", ""),
                "mcp_server_name": mcp_node.name,
                "framework": framework,
                "entry_kind": "mcp",
            },
        ))

        for tool_node in tools_by_parent.get(mcp_node.name, []):
            edges.append(Edge(
                id=_edge_id(entry_id, tool_node.id, "delegates_to"),
                source_id=entry_id,
                target_id=tool_node.id,
                kind="delegates_to",
                host_id=HOST_ID,
                attributes={"resolved_via": "mcp_entry"},
                # The MCP client → tool path is the protocol channel:
                # the detector recorded whether the tool takes arguments
                # (1.2a.2). Without that attribute the edge stays the
                # control default (ADR-0015, decisions 4 and 5).
                **_flow_for_tool_node(tool_node),
            ))

    return edges

def _reachable_functions(
    start: str,
    call_graph: dict[str, set[str]],
) -> set[str]:
    """BFS from `start` through the call graph, including `start` itself.

    The call graph is a name-keyed mapping: function name → set of
    called function names. Only `ast.Name` calls are recorded (see
    `entry_points._collect_call_names`), so attribute calls like
    `self._helper()` are not followed.
    """
    visited: set[str] = set()
    queue: list[str] = [start]
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        for next_name in call_graph.get(current, ()):
            if next_name not in visited:
                queue.append(next_name)
    return visited