"""Cross-file edge resolution for framework detectors.

Framework detectors (ADR-0011) operate per-file: they see one AST
and emit nodes and edges from that AST alone. This is by design —
a detector must not read other files, or it becomes a project-wide
analyzer with its own import resolution, duplicating work that the
normalizer already does.

Some edges, however, are inherently cross-file:

  - An agent created in agent.py uses tools imported from tools.py.
    The detector in agent.py sees `tools=USER_TOOLS`, but USER_TOOLS
    is defined in tools.py. Without import resolution, the edge is
    lost.

  - A route in app.py delegates to an agent defined in agent.py.
    Handled separately (entry_points + ast_normalizer).

This module resolves the first case: `uses_tool` edges from an agent
module to tool modules it imports.

Design choices:

  - Link to ALL tools in an imported module, not just the ones the
    agent actually references. This is a deliberate false-positive
    trade-off: ADR-0009 accepts false positives because the LLM
    ensemble in Phase 3 filters them. Better a spurious edge than a
    missing one.

  - Only follow direct imports (`from X import Y`). Transitive
    imports (X imports Y imports Z) are not followed — that would
    require a real import graph, which is out of scope here.

  - Only Python files. Non-Python sources are handled by TSA and do
    not carry framework provenance in the same way.
"""

from __future__ import annotations

import ast
import hashlib
import logging
from pathlib import Path

from ...graph.models import Edge, Node
from ...ingestion.discovery import FileManifest


log = logging.getLogger(__name__)


def resolve_agent_tool_edges(
    manifest: FileManifest,
    detector_nodes: list[Node],
    host_id: str = "localhost",
) -> list[Edge]:
    """Build cross-file uses_tool edges from agent modules to tool modules.

    Args:
        manifest: file list from ingestion.discover().
        detector_nodes: nodes produced by framework detectors.
        host_id: host identifier for edge IDs.

    Returns:
        Edges of kind `uses_tool`. Empty if no cross-file relations
        are found.
    """
    # --- Index tools by source file ---
    tools_by_file: dict[str, list[Node]] = {}
    for node in detector_nodes:
        if node.kind != "tool":
            continue
        source_file = node.attributes.get("source_file")
        if not source_file:
            continue
        tools_by_file.setdefault(source_file, []).append(node)

    if not tools_by_file:
        return []

    # --- Index agents with framework provenance ---
    agents: list[Node] = [
        n for n in detector_nodes
        if n.kind == "agent" and n.attributes.get("framework")
    ]
    if not agents:
        return []

    # --- Collect per-file imports ---
    # {file_path: [module_dotted_name, ...]}
    imports_by_file = _collect_imports(manifest)

    # --- Build edges ---
    edges: list[Edge] = []
    seen: set[str] = set()

    for agent in agents:
        agent_file = agent.attributes.get("source_file")
        if not agent_file:
            continue

        modules = imports_by_file.get(agent_file, [])
        if not modules:
            continue

        # For each imported module, find the source file(s) that
        # could correspond to it, and link to their tools.
        for module in modules:
            for source_file, tools in tools_by_file.items():
                if source_file == agent_file:
                    # Same file: handled by the per-file detector.
                    continue
                if not _module_matches_file(module, source_file):
                    continue
                for tool in tools:
                    edge_id = _edge_id(agent.id, tool.id, "uses_tool", host_id)
                    if edge_id in seen:
                        continue
                    seen.add(edge_id)
                    edges.append(Edge(
                        id=edge_id,
                        source_id=agent.id,
                        target_id=tool.id,
                        kind="uses_tool",
                        host_id=host_id,
                        # Control flow, data flow unconfirmed (ADR-0015,
                        # decision 4): import resolution says the agent
                        # *can* reach the tool, not that data does.
                        flow_kind="control",
                        data_flow_confirmed="unknown",
                        attributes={
                            "framework": agent.attributes.get("framework", ""),
                            "resolved_via": "cross_file_import",
                            "imported_module": module,
                        },
                    ))

    if edges:
        log.debug(
            "cross-file: built %d uses_tool edges from %d agents",
            len(edges), len(agents),
        )

    return edges


# ============================================================
# Imports collection
# ============================================================

def _collect_imports(manifest: FileManifest) -> dict[str, list[str]]:
    """Collect the dotted module names imported by each Python file.

    Handles:
        import tools                    → "tools"
        import tools.submod             → "tools.submod"
        from tools import USER_TOOLS    → "tools"
        from tools.submod import X      → "tools.submod"
        from tools import X as Y        → "tools"

    Relative imports (`from . import x`, `from .tools import x`) are
    skipped: resolving them requires knowing the package root, which
    the manifest alone does not provide.
    """
    out: dict[str, list[str]] = {}

    for entry in manifest.files:
        if entry.language != "python":
            continue
        try:
            source = entry.abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning("cross-file: cannot read %s: %s", entry.path, e)
            continue
        try:
            tree = ast.parse(source, filename=str(entry.abs_path))
        except SyntaxError as e:
            log.warning("cross-file: syntax error in %s: %s", entry.path, e)
            continue

        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                # Skip relative imports — no package root information.
                if node.level > 0:
                    continue
                if node.module:
                    modules.append(node.module)

        if modules:
            # Deduplicate but preserve first-seen order for determinism.
            out[entry.path] = list(dict.fromkeys(modules))

    return out


def _module_matches_file(module: str, file_path: str) -> bool:
    """True if a dotted module name plausibly corresponds to a file path.

    Matches:
        "tools"            → "tools.py"
        "tools"            → "pkg/tools.py"
        "mcp.servers.foo"  → "mcp/servers/foo.py"
        "mcp.servers.foo"  → "pkg/mcp/servers/foo.py"

    Does not match:
        "tools"            → "tools/sub.py"       (that's tools.sub)
        "other"            → "tools.py"

    The match is on the trailing path segments: the module's dotted
    path must equal the file's path with slashes and the .py suffix.
    """
    if not module:
        return False
    module_path = module.replace(".", "/") + ".py"
    return (
        file_path == module_path
        or file_path.endswith("/" + module_path)
    )


def _edge_id(source_id: str, target_id: str, kind: str, host_id: str) -> str:
    h = hashlib.sha256(
        f"{source_id}:{target_id}:{kind}:{host_id}".encode("utf-8")
    ).hexdigest()
    return f"edge:{h}"