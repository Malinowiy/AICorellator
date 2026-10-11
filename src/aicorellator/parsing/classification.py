"""Classification heuristics for AI infrastructure components.

Heuristics are intentionally conservative: false positives are
cheaper than false negatives here, because Phase 3 (LLM analysis)
filters noise. A missed agent is worse than a spurious one.

Rules are tuned against real projects (FinBot CTF). Update when
new patterns emerge.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..patterns import (
    EXCLUDED_CLASSES,
    EXCLUDED_CLASS_SUFFIXES,
    EXTRA_AGENT_CLASSES,
    SINK_METHOD_NAMES,
    TOOL_METHOD_PREFIXES,
    SKIP_METHOD_NAMES,
)

from ..patterns import PROVIDER_HINTS, SINK_CATEGORY_HINTS

@dataclass
class Classification:
    """Result of classifying a symbol: kind + extra attributes."""

    kind: str
    attributes: dict[str, str] = field(default_factory=dict)


# ============================================================
# Class classification
# ============================================================

def classify_class(class_name: str, file_path: str) -> Classification | None:
    """Classify a class symbol.

    Returns None if the class is not an AI infrastructure component.
    """
    if class_name in EXCLUDED_CLASSES:
        return None
    if class_name.endswith(EXCLUDED_CLASS_SUFFIXES):
        return None

    # --- Agents ---
    if class_name.endswith(("Agent", "Orchestrator")):
        return Classification(kind="agent")
    # --- Agents (extra classes) ---
    if class_name in EXTRA_AGENT_CLASSES:
        return Classification(kind="agent")

    # --- LLM endpoints (by explicit name) ---
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

    # --- LLM endpoints (by path + suffix) ---
    if _has_path_segment(file_path, "llm") and class_name.endswith(("Client", "Judge")):
        provider = _provider_from_name(class_name)
        return Classification(
            kind="llm_endpoint",
            attributes={"provider": provider},
        )

    # --- MCP servers (by path) ---
    if _has_path_segment(file_path, "servers") and _has_path_segment(file_path, "mcp"):
        if class_name.endswith(("Server", "Service")):
            return Classification(kind="mcp_server")

    # --- Guardrails (by path + name keyword) ---
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
) -> Classification | None:
    """Classify a method symbol.

    Returns None if the method is not a tool or sink.
    """
    if method_name.startswith("_"):
        return None

    # Skip serialization helpers — not tools
    if method_name in ("to_dict", "to_summary_dict", "to_json", "to_yaml"):
        return None

    # Skip server factories — create_*_server
    if method_name.startswith("create_") and method_name.endswith("_server"):
        return None

    # --- Sinks (by explicit name) ---
    if method_name in SINK_METHOD_NAMES:
        category = _sink_category(method_name)
        return Classification(
            kind="sink",
            attributes={"category": category},
        )

    # --- Tools (in tools/ directory, by prefix) ---
    if _has_path_segment(file_path, "tools") and method_name.startswith(TOOL_METHOD_PREFIXES):
        attrs: dict[str, str] = {}
        if class_name:
            attrs["parent_class"] = class_name
        return Classification(kind="tool", attributes=attrs)

    # --- Tools (in MCP servers, non-private methods) ---
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
        # Ensure there IS a server name segment after "servers"
        if servers_idx + 2 >= len(parts):
            # file is mcp/servers/__init__.py or mcp/servers/foo.py
            # — not a server subdirectory
            return None
        server_name = parts[servers_idx + 1]
        # Exclude non-directory "names" (files at servers level)
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


def _has_path_segment(file_path: str, segment: str) -> bool:
    """True if `segment` is a path component anywhere in file_path.

    'tools/data/vendor.py'         → True for 'tools'
    'finbot/tools/data/vendor.py'  → True for 'tools'
    'my_tools/foo.py'              → False for 'tools'
    """
    return segment in file_path.split("/")