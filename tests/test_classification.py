"""Unit tests for classification.

Two inputs decide a kind, and only two: the capabilities the extractor
found in the symbol's body (ADR-0009), and the framework provenance a
detector recorded for it (ADR-0006 layer 1, ADR-0016 decision 5 — the
detector merge is authoritative for kind, so this module never has to
guess what a detector already knows).

Class names, method names and provider names never *grant* a kind
(ADR-0016, decision 7). They still withhold one where they are
exclusions: `BaseAgent`, `*Detector`, `*Schema`, `_private` methods,
`create_*_server` factories.

These tests are symbol/path pairs, without parsing any real files. Fast
and deterministic — the extractor that produces the capabilities has its
own tests (`test_capability_extractor.py`).
"""

from __future__ import annotations

from aicorellator.parsing.classification import (
    classify_class,
    classify_file,
    classify_method,
)


# ============================================================
# class classification — capability → kind
# ============================================================

def test_sink_capability_makes_a_class_a_sink() -> None:
    """`executes_shell` in the body is what makes it a sink."""
    c = classify_class("RandomHelper", "core/util.py", ["executes_shell"])
    assert c is not None
    assert c.kind == "sink"
    assert c.attributes["category"] == "code_execution"


def test_sink_category_comes_from_the_capability() -> None:
    """The category is the capability, not a substring of the name."""
    cases = (
        (["executes_code"], "code_execution"),
        (["writes_db"], "database"),
        (["writes_filesystem"], "filesystem"),
        (["http_egress"], "network"),
    )
    for caps, expected in cases:
        c = classify_class("Whatever", "a.py", caps)
        assert c is not None and c.kind == "sink", caps
        assert c.attributes["category"] == expected, caps


def test_agent_capabilities_make_a_class_an_agent() -> None:
    """`calls_llm` + `invokes_mcp_tool` — the agent trigger."""
    c = classify_class(
        "Helper", "core/util.py", ["calls_llm", "invokes_mcp_tool"]
    )
    assert c is not None
    assert c.kind == "agent"


def test_llm_capability_alone_is_an_agent() -> None:
    """AGENT_CAPABILITIES is the union of the LLM and MCP triggers.

    A class that only calls an LLM is an agent, not an `llm_endpoint`:
    the endpoint node kind is the config extractor's and the framework
    detectors' to produce (ADR-0009).
    """
    c = classify_class("Handler", "core/handler.py", ["calls_llm"])
    assert c is not None
    assert c.kind == "agent"


def test_class_without_a_capability_is_not_classified() -> None:
    """The class-A name rules are gone (ADR-0016, decision 7).

    Every name below used to decide a kind. None of them does now: no
    capability, no node. This is the whole point of the removal — a
    project the detectors do not cover gets a user detector, not a guess.
    """
    for name, path in (
        ("InvoiceAgent", "agents/specialized/invoice.py"),
        ("OrchestratorAgent", "agents/orchestrator.py"),
        ("CoPilotAssistant", "agents/chat.py"),
        ("LLMClient", "core/llm/client.py"),
        ("OllamaClient", "core/llm/ollama_client.py"),
        ("OpenAIClient", "core/llm/openai_client.py"),
        ("LLMJudge", "core/llm/judge.py"),
        ("GuardrailHookService", "guardrails/service.py"),
        ("FinDriveServer", "mcp/servers/findrive/server.py"),
    ):
        assert classify_class(name, path) is None, name


def test_excluded_name_stays_excluded_with_a_capability() -> None:
    """Exclusions are read first, and they withhold, never grant.

    `RCEDetector` is excluded by suffix, so it stays out even when its
    body carries a sink capability.
    """
    assert classify_class(
        "RCEDetector", "ctf/detectors/rce.py", ["executes_shell"]
    ) is None


# ============================================================
# class classification — exclusions (a name withholds, never grants)
# ============================================================

def test_base_agent_excluded() -> None:
    assert classify_class("BaseAgent", "agents/base.py") is None


def test_detector_excluded() -> None:
    assert classify_class("RCEDetector", "ctf/detectors/rce.py") is None
    assert classify_class(
        "GuardrailPreventionDetector", "ctf/detectors/x.py"
    ) is None


def test_evaluator_excluded() -> None:
    assert classify_class("InvoiceAmountEvaluator", "ctf/evaluators/x.py") is None


def test_schema_excluded() -> None:
    assert classify_class("BadgeSchema", "ctf/schemas/badge.py") is None


def test_hook_kind_not_guardrail() -> None:
    """HookKind is a schema, not a guardrail."""
    assert classify_class("HookKind", "guardrails/schemas.py") is None


def test_hook_envelope_not_guardrail() -> None:
    assert classify_class("HookEnvelope", "guardrails/schemas.py") is None


def test_hook_outcome_not_guardrail() -> None:
    assert classify_class("HookOutcome", "guardrails/schemas.py") is None


def test_random_class_not_classified() -> None:
    assert classify_class("VendorRepository", "core/data/repositories.py") is None
    assert classify_class("UserProfile", "core/data/models.py") is None


# ============================================================
# method classification — capability → kind
# ============================================================

SERVER = "mcp/servers/systemutils/server.py"


def test_sink_capability_makes_a_method_a_sink() -> None:
    c = classify_method(
        "delete_file", SERVER, "SystemUtilsServer", ["writes_filesystem"],
    )
    assert c is not None
    assert c.kind == "sink"
    assert c.attributes["category"] == "filesystem"


def test_sink_name_without_a_capability_is_not_a_sink() -> None:
    """`execute_script` in an MCP server is a tool, not a sink.

    The name table that made it a sink is gone (ADR-0016, decision 7).
    Its body carries `executes_shell`, and that is what makes it a sink —
    here it carries nothing, so the MCP server layout decides: a tool.
    """
    c = classify_method("execute_script", SERVER, "SystemUtilsServer")
    assert c is not None
    assert c.kind == "tool"
    assert c.attributes["parent_class"] == "SystemUtilsServer"


def test_sink_capability_beats_the_mcp_server_layout() -> None:
    """Both signals present: the behaviour wins."""
    c = classify_method(
        "execute_script", SERVER, "SystemUtilsServer", ["executes_shell"],
    )
    assert c is not None
    assert c.kind == "sink"


def test_tool_capability_makes_a_method_a_tool() -> None:
    c = classify_method(
        "read_docs", "agents/chat.py", "ChatAgent", ["invokes_mcp_tool"],
    )
    assert c is not None
    assert c.kind == "tool"
    assert c.attributes["parent_class"] == "ChatAgent"


def test_tool_in_mcp_server() -> None:
    """A public method of an MCP server is a tool — layout, not a name."""
    c = classify_method(
        "upload_file", "mcp/servers/findrive/server.py", "FinDriveServer",
    )
    assert c is not None
    assert c.kind == "tool"


def test_tools_dir_is_no_longer_a_tool_rule() -> None:
    """`tools/` + `get_`/`update_`/... was a class-A rule (ADR-0016).

    The layout that remains is the MCP server one; a plain `tools/`
    directory with no capability and no framework detector yields no node.
    """
    assert classify_method("get_vendor_details", "tools/data/vendor.py", None) is None
    assert classify_method("update_vendor", "tools/data/vendor.py", None) is None


def test_serialization_helper_is_a_tool_in_an_mcp_server() -> None:
    """`SKIP_METHOD_NAMES` is gone; the layout rule has no exceptions.

    A public method of an MCP server is exposed as a tool. `to_dict` on a
    model class under `mcp/servers/` is therefore a tool node — a known
    false positive of the layout rule, accepted because the alternative
    was a list of names (ADR-0016, decision 7).
    """
    c = classify_method(
        "to_dict", "mcp/servers/findrive/models.py", "FinDriveFile",
    )
    assert c is not None
    assert c.kind == "tool"


def test_private_method_not_classified() -> None:
    assert classify_method(
        "_execute_tool",
        "agents/chat.py",
        "ChatAgent",
    ) is None


def test_create_server_not_tool() -> None:
    """Server factories are not tools."""
    assert classify_method(
        "create_findrive_server",
        "mcp/servers/findrive/server.py",
        None,
    ) is None


# ============================================================
# file classification — layout
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


def test_mcp_servers_init_not_classified() -> None:
    """mcp/servers/__init__.py is a package file, not a server."""
    assert classify_file("mcp/servers/__init__.py") is None

