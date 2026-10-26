"""Classification of AI infrastructure components.

A symbol is classified from two inputs, and only two:

  1. Capabilities (ADR-0009) — AST-derived behaviour, extracted from
     the symbol body by `parsing/capability_extractor.py`.
  2. Framework provenance (ADR-0011, ADR-0016) — a framework detector
     recognized the symbol and emitted a node for it. Detector nodes are
     authoritative for kind (`normalizer_stages/detector_merge.py`), so
     this module does not have to guess what a detector already knows.

Class names and method names are not read to *decide* a kind. The
class-A name heuristics — `SINK_METHOD_NAMES`, `SINK_CATEGORY_HINTS`,
`RUNNER_TO_AGENT`, `EXTRA_AGENT_CLASSES`, `PROVIDER_HINTS`,
`TOOL_METHOD_PREFIXES`, `SKIP_METHOD_NAMES`, and the class-name suffix
table `CLASS_SUFFIX_KINDS` — were removed in ADR-0016, decision 7: they
described FinBot's layout and mislabelled every other project. A project
no built-in detector covers is covered by a user detector (a file in
`parsing/framework_detectors/` plus a line in `registry.py`,
ADR-0016 decision 5) or by the optional LLM classifier.

Names still appear here as *exclusions* — `EXCLUDED_CLASSES`,
`EXCLUDED_CLASS_SUFFIXES`, the `_private` and `create_*_server` guards.
An exclusion withholds a node; it never grants one. The same is true of
the two path rules that remain: `mcp/servers/<name>/` is the structural
convention the MCP framework detectors use (ADR-0013), and it is a
layout, not a class name.

Capabilities take precedence when they conflict with a path: a public
method of an MCP server is a tool unless its body is a sink.

The rules are intentionally conservative: a missed component is worse
than a spurious one, because Phase 3 (LLM analysis) filters noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..patterns import (
    AGENT_CAPABILITIES,
    EXCLUDED_CLASSES,
    EXCLUDED_CLASS_SUFFIXES,
    LLM_ENDPOINT_CAPABILITIES,
    SINK_CAPABILITIES,
    TOOL_CAPABILITIES,
)


@dataclass
class Classification:
    """Result of classifying a symbol: kind + extra attributes."""

    kind: str
    attributes: dict[str, str] = field(default_factory=dict)

# ============================================================
# Class classification
# ============================================================

def classify_class(
    class_name: str,
    file_path: str,
    capabilities: list[str] | None = None,
) -> Classification | None:
    """Classify a class symbol from the capabilities in its body.

    Args:
        class_name: name of the class. Read for exclusions only.
        file_path: relative path. Not read: deciding a kind from a path
                   was a class-A heuristic (ADR-0016, decision 7). The
                   parameter stays because every caller passes the
                   symbol's location and `classify_method` still reads it
                   for the MCP server layout.
        capabilities: capabilities extracted from the class body
                      (ADR-0009). This is the input that decides a kind.

    Returns None when the class carries no classifying capability, or
    when its name is excluded.
    """
    caps = set(capabilities or ())

    # --- Exclusions (never classify, regardless of capabilities) ---
    if class_name in EXCLUDED_CLASSES:
        return None
    if class_name.endswith(EXCLUDED_CLASS_SUFFIXES):
        return None

    # --- Capabilities — the only positive signal ---
    if caps & SINK_CAPABILITIES:
        # executes_shell / executes_code / writes_* → sink.
        # No name is required.
        return Classification(
            kind="sink",
            attributes={"category": _sink_category_from_caps(caps)},
        )

    if caps & AGENT_CAPABILITIES:
        # calls_llm + invokes_mcp_tool → agent.
        return Classification(kind="agent")

    if caps & LLM_ENDPOINT_CAPABILITIES:
        # calls_llm alone, without MCP → llm_endpoint. No `provider`
        # attribute: the provider is a configuration fact, known to the
        # LLM config extractor or to the framework detector that
        # recognized the client class — never to the class name.
        return Classification(kind="llm_endpoint")

    return None


# ============================================================
# Method classification
# ============================================================

def classify_method(
    method_name: str,
    file_path: str,
    class_name: str | None,
    capabilities: list[str] | None = None,
) -> Classification | None:
    """Classify a method symbol from its capabilities and its layout.

    Args:
        method_name: name of the method or function. Read for exclusions
                     only — `_private` methods and `create_*_server`
                     factories are withheld.
        file_path: relative path. Read for one layout rule: a public
                   method of an MCP server is a tool (ADR-0013). The
                   `tools/` + `get_`/`update_`/... rule, the sink-name
                   table and the serialization-helper skip were class-A
                   heuristics (ADR-0016, decision 7) and are gone.
        class_name: enclosing class name, or None for module-level.
        capabilities: capabilities extracted from the method body
                      (ADR-0009). This is the input that decides a kind.

    Returns None if the method is not a tool or sink.
    """
    caps = set(capabilities or ())

    # --- Exclusions (never classify, regardless of capabilities) ---
    if method_name.startswith("_"):
        return None

    # Skip server factories — create_*_server.
    if method_name.startswith("create_") and method_name.endswith("_server"):
        return None

    # --- Capabilities (the only positive signal about behaviour) ---
    if caps & SINK_CAPABILITIES:
        return Classification(
            kind="sink",
            attributes={"category": _sink_category_from_caps(caps)},
        )

    if caps & TOOL_CAPABILITIES:
        attrs: dict[str, str] = {}
        if class_name:
            attrs["parent_class"] = class_name
        return Classification(kind="tool", attributes=attrs)

    # --- Layout (a declaration, not a name guess) ---
    # Every public method of an MCP server is exposed as a tool; the MCP
    # framework detectors declare the same set from the server's shape.
    if _has_path_segment(file_path, "mcp") and _has_path_segment(
        file_path, "servers"
    ):
        attrs = {}
        if class_name:
            attrs["parent_class"] = class_name
        return Classification(kind="tool", attributes=attrs)

    return None


# ============================================================
# File classification
# ============================================================

def classify_file(file_path: str) -> Classification | None:
    """Classify a file as a whole.

    Returns a Classification with kind='mcp_server' and the server
    name in attributes, if the file lives under mcp/servers/<name>/.

    Returns None for other files.
    """
    parts = file_path.split("/")

    if "mcp" in parts and "servers" in parts:
        try:
            servers_idx = parts.index("servers")
        except ValueError:
            return None
        if servers_idx + 2 >= len(parts):
            return None
        server_name = parts[servers_idx + 1]
        if server_name.endswith(".py"):
            return None
        return Classification(
            kind="mcp_server",
            attributes={"server_name": server_name},
        )

    return None


# ============================================================
# Helpers
# ============================================================

def _sink_category_from_caps(caps: set[str]) -> str:
    """Derive a sink category from capabilities."""
    if "executes_shell" in caps or "executes_code" in caps:
        return "code_execution"
    if "writes_db" in caps:
        return "database"
    if "writes_filesystem" in caps:
        return "filesystem"
    if "http_egress" in caps:
        return "network"
    return "unknown"


def _has_path_segment(file_path: str, segment: str) -> bool:
    """True if `segment` is a path component anywhere in file_path.

    'tools/data/vendor.py'         → True for 'tools'
    'finbot/tools/data/vendor.py'  → True for 'tools'
    'my_tools/foo.py'              → False for 'tools'
    """
    return segment in file_path.split("/")