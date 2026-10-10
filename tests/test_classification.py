"""Unit tests for classification heuristics.

These test the rules against symbol/path pairs, without parsing
any real files. Fast and deterministic.
"""

from __future__ import annotations

from aicorellator.parsing.classification import (
    classify_class,
    classify_file,
    classify_method,
)


# ============================================================
# class classification
# ============================================================

def test_agent_by_suffix() -> None:
    c = classify_class("InvoiceAgent", "agents/specialized/invoice.py")
    assert c is not None
    assert c.kind == "agent"


def test_orchestrator_is_agent() -> None:
    c = classify_class("OrchestratorAgent", "agents/orchestrator.py")
    assert c is not None
    assert c.kind == "agent"


def test_base_agent_excluded() -> None:
    assert classify_class("BaseAgent", "agents/base.py") is None


def test_detector_excluded() -> None:
    assert classify_class("RCEDetector", "ctf/detectors/rce.py") is None
    assert classify_class("GuardrailPreventionDetector", "ctf/detectors/x.py") is None


def test_evaluator_excluded() -> None:
    assert classify_class("InvoiceAmountEvaluator", "ctf/evaluators/x.py") is None


def test_schema_excluded() -> None:
    assert classify_class("BadgeSchema", "ctf/schemas/badge.py") is None


def test_llm_client_by_name() -> None:
    c = classify_class("LLMClient", "core/llm/client.py")
    assert c is not None
    assert c.kind == "llm_endpoint"
    assert c.attributes["provider"] == "unknown"


def test_ollama_client_provider() -> None:
    c = classify_class("OllamaClient", "core/llm/ollama_client.py")
    assert c is not None
    assert c.kind == "llm_endpoint"
    assert c.attributes["provider"] == "ollama"


def test_openai_client_provider() -> None:
    c = classify_class("OpenAIClient", "core/llm/openai_client.py")
    assert c is not None
    assert c.kind == "llm_endpoint"
    assert c.attributes["provider"] == "openai"


def test_guardrail_service() -> None:
    c = classify_class("GuardrailHookService", "guardrails/service.py")
    assert c is not None
    assert c.kind == "guardrail"


def test_random_class_not_classified() -> None:
    assert classify_class("VendorRepository", "core/data/repositories.py") is None
    assert classify_class("UserProfile", "core/data/models.py") is None


# ============================================================
# method classification
# ============================================================

def test_sink_by_name_execute_script() -> None:
    c = classify_method(
        "execute_script",
        "mcp/servers/systemutils/server.py",
        "SystemUtilsServer",
    )
    assert c is not None
    assert c.kind == "sink"
    assert c.attributes["category"] == "code_execution"


def test_sink_by_name_network_request() -> None:
    c = classify_method(
        "network_request",
        "mcp/servers/systemutils/server.py",
        "SystemUtilsServer",
    )
    assert c is not None
    assert c.kind == "sink"
    assert c.attributes["category"] == "network"


def test_sink_by_name_upload_file() -> None:
    c = classify_method(
        "upload_file",
        "mcp/servers/findrive/server.py",
        "FinDriveServer",
    )
    assert c is not None
    assert c.kind == "sink"
    assert c.attributes["category"] == "filesystem"


def test_tool_in_tools_dir() -> None:
    c = classify_method(
        "get_vendor_details",
        "tools/data/vendor.py",
        None,
    )
    assert c is not None
    assert c.kind == "tool"


def test_tool_in_mcp_server() -> None:
    c = classify_method(
        "upload_file",
        "mcp/servers/findrive/server.py",
        "FinDriveServer",
    )
    # This is both a sink (by name) and a tool (by path).
    # Sink wins — dangerous action takes priority.
    assert c is not None
    assert c.kind == "sink"


def test_private_method_not_classified() -> None:
    assert classify_method(
        "_execute_tool",
        "agents/chat.py",
        "ChatAgent",
    ) is None


def test_regular_method_not_classified() -> None:
    assert classify_method(
        "get_tool_definitions",
        "agents/base.py",
        "BaseAgent",
    ) is None


# ============================================================
# file classification
# ============================================================

def test_mcp_server_file() -> None:
    c = classify_file("mcp/servers/findrive/server.py")
    assert c is not None
    assert c.kind == "mcp_server"
    assert c.attributes["server_name"] == "findrive"


def test_mcp_server_finmail() -> None:
    c = classify_file("mcp/servers/finmail/repositories.py")
    assert c is not None
    assert c.kind == "mcp_server"
    assert c.attributes["server_name"] == "finmail"


def test_non_mcp_file_not_classified() -> None:
    assert classify_file("agents/invoice.py") is None
    assert classify_file("core/llm/client.py") is None

    # ============================================================
# Additional edge cases from FinBot analysis
# ============================================================

def test_mcp_servers_init_not_classified() -> None:
    """mcp/servers/__init__.py is a package file, not a server."""
    assert classify_file("mcp/servers/__init__.py") is None


def test_hook_kind_not_guardrail() -> None:
    """HookKind is a schema, not a guardrail."""
    assert classify_class("HookKind", "guardrails/schemas.py") is None


def test_hook_envelope_not_guardrail() -> None:
    assert classify_class("HookEnvelope", "guardrails/schemas.py") is None


def test_hook_outcome_not_guardrail() -> None:
    assert classify_class("HookOutcome", "guardrails/schemas.py") is None


def test_to_dict_not_tool() -> None:
    """Serialization helpers are not tools."""
    assert classify_method(
        "to_dict",
        "mcp/servers/findrive/models.py",
        "FinDriveFile",
    ) is None


def test_create_server_not_tool() -> None:
    """Server factories are not tools."""
    assert classify_method(
        "create_findrive_server",
        "mcp/servers/findrive/server.py",
        None,
    ) is None