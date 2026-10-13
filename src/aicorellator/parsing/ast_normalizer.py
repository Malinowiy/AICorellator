"""AST Normalizer — turns TSA symbols into Node/Edge objects.

Responsibilities:
  1. Create Node objects for classified symbols.
  2. Create edges between nodes using simple heuristics.
  3. Attach capabilities (ADR-0009) to nodes.
  4. Merge framework detector nodes and edges (ADR-0011).

Edges derived here:
  - provides_tool  (mcp_server → tool)
  - has_access_to  (tool → sink)
  - delegates_to   (entry_point → agent)
  - delegates_to   (agent → agent, orchestrator pattern)
  - has_access_to  (agent → mcp_server, via create_mcp_server)

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

import hashlib
from dataclasses import dataclass, field

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
from .framework_detectors import DetectionResult
from .llm_extractor import LLMExtractorOutput
from .models import TSAOutput, TSASymbol
from .framework_detectors.cross_file import resolve_agent_tool_edges
from ..patterns import MCP_DIR_SEGMENT, MCP_SERVERS_SEGMENT, SINK_CAPABILITIES


HOST_ID = "localhost"       # Phase 0/1 — single host


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
            ))

    # --- Edges: has_access_to (tool -> sink) ---
    for file_path, tools in tools_by_file.items():
        sinks = sinks_by_file.get(file_path, [])
        for tool_node in tools:
            for sink_node in sinks:
                delta.edges.append(Edge(
                    id=_edge_id(tool_node.id, sink_node.id, "has_access_to"),
                    source_id=tool_node.id,
                    target_id=sink_node.id,
                    kind="has_access_to",
                    host_id=HOST_ID,
                ))


    # --- Entry points ---
    entry_node_by_handler: dict[str, str] = {}
    for ep in entry_points.entry_points:
        node_id = _entry_point_id(ep)
        entry_node_by_handler[ep.handler] = node_id
        delta.nodes.append(Node(
            id=node_id,
            kind="entry_point",
            name=f"{ep.method} {ep.path}",
            host_id=HOST_ID,
            attributes={
                "source_file": ep.file_path,
                "line_start": ep.line,
                "method": ep.method,
                "path": ep.path,
                "handler": ep.handler,
            },
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
        ))
    return edges

def _link_entry_points_to_sinks(
    entry_points: EntryPointOutput,
    sinks_by_file: dict[str, list[Node]],
    entry_node_by_handler: dict[str, str],
) -> list[Edge]:
    """Resolve entry_point → sink edges via same-file call reachability.

    For each entry point, BFS from the handler through the file's
    call graph. For each sink node in the same file whose name is
    reachable, emit a has_access_to edge.

    Control-flow reachability, not dataflow. Same-file only. False
    positives accepted (ADR-0009).
    """
    edges: list[Edge] = []

    for ep in entry_points.entry_points:
        entry_id = entry_node_by_handler.get(ep.handler)
        if entry_id is None:
            continue

        sinks_here = sinks_by_file.get(ep.file_path, [])
        if not sinks_here:
            continue

        reachable = _reachable_functions(
            start=ep.handler,
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