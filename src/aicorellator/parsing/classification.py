"""Classification heuristics for AI infrastructure components.

Heuristics are intentionally conservative: false positives are
cheaper than false negatives here, because Phase 3 (LLM analysis)
filters noise. A missed agent is worse than a spurious one.

Two signals are used:

  1. Name-based heuristics (class names, method names, file paths).
  2. Capability-based refinement (ADR-0009): AST-derived
     capabilities override or reinforce name-based decisions.

Capabilities take precedence when they conflict with names.
A class named `execute_script` without `executes_shell` is not a
sink. A class named `RandomHelper` with `executes_shell` is.

Rules are tuned against real projects (FinBot CTF). Update when
new patterns emerge.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..patterns import (
    AGENT_CAPABILITIES,
    EXCLUDED_CLASSES,
    EXCLUDED_CLASS_SUFFIXES,
    EXTRA_AGENT_CLASSES,
    LLM_ENDPOINT_CAPABILITIES,
    PROVIDER_HINTS,
    SINK_CAPABILITIES,
    SINK_CATEGORY_HINTS,
    SINK_METHOD_NAMES,
    SKIP_METHOD_NAMES,
    TOOL_CAPABILITIES,
    TOOL_METHOD_PREFIXES,
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
    """Classify a class symbol.

    Args:
        class_name: name of the class.
        file_path: relative path.
        capabilities: optional list of capabilities extracted from
                      the class body (ADR-0009). When provided, they
                      take precedence over name-based heuristics.

    Returns None if the class is not an AI infrastructure component.
    """
    caps = set(capabilities or ())

    # --- Exclusions (never classify, regardless of capabilities) ---
    if class_name in EXCLUDED_CLASSES:
        return None
    if class_name.endswith(EXCLUDED_CLASS_SUFFIXES):
        return None

    # --- Capability-based refinement (highest priority) ---
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
        # calls_llm alone, without MCP → llm_endpoint.
        provider = _provider_from_name(class_name)
        return Classification(
            kind="llm_endpoint",
            attributes={"provider": provider},
        )

    # --- Name-based heuristics (fall through if capabilities empty) ---

    # Agents
    if class_name.endswith(("Agent", "Orchestrator")):
        return Classification(kind="agent")
    if class_name in EXTRA_AGENT_CLASSES:
        return Classification(kind="agent")

    # LLM endpoints (by explicit name)
    if class_name in (
        "LLMClient",
        "OllamaClient",
        "OpenAIClient",
        "ContextualLLMClient",
        "MockLLMClient",
        "LLMJudge",
    ):
        provider = _provider_from_name(class_name)
        return Classification(
            kind="llm_endpoint",
            attributes={"provider": provider},
        )

    # LLM endpoints (by path + suffix)
    if _has_path_segment(file_path, "llm") and class_name.endswith(("Client", "Judge")):
        provider = _provider_from_name(class_name)
        return Classification(
            kind="llm_endpoint",
            attributes={"provider": provider},
        )

    # MCP servers (by path)
    if _has_path_segment(file_path, "servers") and _has_path_segment(file_path, "mcp"):
        if class_name.endswith(("Server", "Service")):
            return Classification(kind="mcp_server")

    # Guardrails (by path + name keyword)
    if _has_path_segment(file_path, "guardrails"):
        if any(kw in class_name for kw in ("Guardrail", "Guard", "Hook")):
            return Classification(kind="guardrail")

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
    """Classify a method symbol.

    Args:
        method_name: name of the method or function.
        file_path: relative path.
        class_name: enclosing class name, or None for module-level.
        capabilities: optional list of capabilities (ADR-0009).
                      When provided, they take precedence over names.

    Returns None if the method is not a tool or sink.
    """
    caps = set(capabilities or ())

    # --- Exclusions ---
    if method_name.startswith("_"):
        return None

    # Skip serialization helpers — not tools.
    if method_name in SKIP_METHOD_NAMES:
        return None

    # Skip server factories — create_*_server.
    if method_name.startswith("create_") and method_name.endswith("_server"):
        return None

    # --- Capability-based refinement (highest priority) ---
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

    # --- Name-based heuristics ---

    # Sinks (by explicit name)
    if method_name in SINK_METHOD_NAMES:
        category = _sink_category(method_name)
        return Classification(
            kind="sink",
            attributes={"category": category},
        )

    # Tools (in tools/ directory, by prefix)
    if _has_path_segment(file_path, "tools") and method_name.startswith(TOOL_METHOD_PREFIXES):
        attrs = {}
        if class_name:
            attrs["parent_class"] = class_name
        return Classification(kind="tool", attributes=attrs)

    # Tools (in MCP servers, non-private methods)
    if (
        _has_path_segment(file_path, "mcp")
        and _has_path_segment(file_path, "servers")
        and not method_name.startswith("_")
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

def _provider_from_name(class_name: str) -> str:
    name = class_name.lower()
    for hint, provider in PROVIDER_HINTS:
        if hint in name:
            return provider
    return "unknown"


def _sink_category(method_name: str) -> str:
    for hint, category in SINK_CATEGORY_HINTS:
        if hint in method_name:
            return category
    return "unknown"


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