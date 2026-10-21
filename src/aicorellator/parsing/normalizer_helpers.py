"""Shared primitives for the normalizer stages (step 4).

Node constructors, canonical ID derivation, the per-file source indexes
and their resolvers. Every function here was a module-level helper in
`ast_normalizer.py`; they moved so the stages can use them without
importing the pipeline (which imports the stages).

Nothing here decides *what* the graph contains — that is each stage's
job. These are the pieces more than one stage needs, plus the two flow
attribute dictionaries that the edge-building stages merge into their
own keyword arguments.
"""

from __future__ import annotations

import ast
import hashlib
import logging
import re
from pathlib import Path
from typing import TypeVar

from ..graph.models import Edge, Node
from ..patterns import MCP_DIR_SEGMENT, MCP_SERVERS_SEGMENT
from .capability_extractor import FileCapabilities
from .classification import Classification
from .flow import FunctionNode, FlowDecision, VALID_KINDS, flow_kind_for
from .models import TSASymbol


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


def flow_for_tool_node(tool_node: Node) -> dict[str, str]:
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

# ============================================================
# Capability lookup
# ============================================================

def caps_for_symbol(
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

def mcp_server_id(server_name: str) -> str:
    h = sha256(f"mcp_server:{server_name}:{HOST_ID}")
    return f"mcp_server:{h}"

def mcp_entry_point_id(server_name: str) -> str:
    """Canonical ID for the virtual MCP entry point of a server.

    Distinct from `entry_point_id` (HTTP routes) so that an MCP entry
    and an HTTP route can never collide, even if they share a name.
    """
    key = f"entry_point:mcp:{server_name}:{HOST_ID}"
    return f"entry_point:{sha256(key)}"


def entry_point_id(ep) -> str:
    key = f"entry_point:{ep.file_path}:{ep.method}:{ep.path}:{HOST_ID}"
    return f"entry_point:{sha256(key)}"

def cli_entry_point_id(file_path: str) -> str:
    """Canonical ID for a CLI entry point (one per script)."""
    key = f"entry_point:cli:{file_path}:{HOST_ID}"
    return f"entry_point:{sha256(key)}"


def streamlit_entry_point_id(file_path: str, call: str) -> str:
    """Canonical ID for a Streamlit entry point (one per call name)."""
    key = f"entry_point:streamlit:{file_path}:{call}:{HOST_ID}"
    return f"entry_point:{sha256(key)}"

def llm_endpoint_id(llm_ep) -> str:
    """Canonical ID: sha256(provider + model + base_url + host)."""
    key = (
        f"llm_endpoint:{llm_ep.provider}:"
        f"{llm_ep.model or ''}:{llm_ep.base_url or ''}:{HOST_ID}"
    )
    return f"llm_endpoint:{sha256(key)}"


def llm_endpoint_name(llm_ep) -> str:
    """Human-readable name: provider:model, or provider:base_url."""
    if llm_ep.model:
        return f"{llm_ep.provider}:{llm_ep.model}"
    if llm_ep.base_url:
        return f"{llm_ep.provider}:{llm_ep.base_url}"
    return llm_ep.provider


def node_from_class(
    symbol: TSASymbol,
    c: Classification,
    capabilities: dict[str, FileCapabilities] | None = None,
) -> Node:
    h = sha256(f"{c.kind}:{symbol.file_path}:{symbol.name}:{HOST_ID}")
    node_id = f"{c.kind}:{h}"
    symbol_caps = caps_for_symbol(
        capabilities or {}, symbol.file_path, symbol.name
    )
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


def node_from_method(
    symbol: TSASymbol,
    c: Classification,
    capabilities: dict[str, FileCapabilities] | None = None,
) -> Node:
    key_parts = [c.kind, symbol.file_path, symbol.name]
    if symbol.class_name:
        key_parts.append(symbol.class_name)
    key_parts.append(HOST_ID)
    h = sha256(":".join(key_parts))
    node_id = f"{c.kind}:{h}"
    symbol_caps = caps_for_symbol(
        capabilities or {}, symbol.file_path, symbol.name
    )
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


def edge_id(source_id: str, target_id: str, kind: str) -> str:
    h = sha256(f"{source_id}:{target_id}:{kind}:{HOST_ID}")
    return f"edge:{h}"


def sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_python_file(abs_path: Path | None) -> ast.Module | None:
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


def index_functions(
    module: ast.Module,
) -> dict[tuple[str, int], FunctionNode]:
    """Map `(name, line_start)` → node for a parsed module.

    Built once per file. Keyed by position, not by name alone: a module
    may define the same name twice — `toggle_enabled` at
    `finbot/core/data/repositories.py:664` and `:1426`, in two different
    repository classes — and the two bodies are different code. Keying
    by name made the second node read the first body (1.2a.3f); keying
    by position lets `resolve_definition` pick the definition the node
    came from.

    Every definition is kept, nested functions included, so a node
    inside one resolves to it. Insertion order is `ast.walk` order
    (breadth-first, so module-level definitions first) — the order the
    name fallback in `resolve_definition` returns, which is the
    pre-1.2a.3f behaviour for a node with no usable `line_start`.
    """
    index: dict[tuple[str, int], FunctionNode] = {}
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            index.setdefault((node.name, node.lineno), node)
    return index


def index_classes(module: ast.Module) -> dict[tuple[str, int], ast.ClassDef]:
    """Map `(name, line_start)` → node for a parsed module (1.2a.5).

    Built once per file from the module's own top level: TSA reports
    symbols for top-level classes, and a nested class is not a node of
    its own. Keyed by position for the same reason as
    `index_functions` (1.2a.3f); a name defined twice keeps both
    definitions.
    """
    index: dict[tuple[str, int], ast.ClassDef] = {}
    for node in module.body:
        if isinstance(node, ast.ClassDef):
            index.setdefault((node.name, node.lineno), node)
    return index


# A function or class definition — the values of the two indexes above.
Definition = TypeVar("Definition", bound=ast.AST)


def node_line_start(node: Node) -> int | None:
    """The node's `line_start` when it is a usable integer (1.2a.3f).

    TSA nodes always carry one. Detector-created nodes carry none, and a
    hand-built node may carry a placeholder; both resolve by name.
    """
    line_start = node.attributes.get("line_start")
    if isinstance(line_start, int):
        return line_start
    return None


def resolve_definition(
    index: dict[tuple[str, int], Definition],
    name: str,
    line_start: int | None,
) -> Definition | None:
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
        return first_definition(index, name)
    exact = index.get((name, line_start))
    if exact is not None:
        return exact

    containing: Definition | None = None
    preceding: Definition | None = None
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
    return first_definition(index, name)


def first_definition(
    index: dict[tuple[str, int], Definition],
    name: str,
) -> Definition | None:
    """The first definition of `name`, in index (source) order.

    The name-keyed behaviour of the pre-1.2a.3f index: the first
    definition of a name wins.
    """
    for (candidate, _start), definition in index.items():
        if candidate == name:
            return definition
    return None


def find_sink_call(
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


def server_name_for_file(file_path: str) -> str | None:
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
