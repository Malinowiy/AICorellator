"""Sanity tests for the patterns registry."""

from __future__ import annotations

from aicorellator import patterns


def test_node_kinds_match_models() -> None:
    """NODE_KINDS mirrors VALID_NODE_KINDS in graph models."""
    from aicorellator.graph.models import VALID_NODE_KINDS
    assert patterns.NODE_KINDS == VALID_NODE_KINDS


def test_edge_kinds_match_models() -> None:
    """EDGE_KINDS mirrors VALID_EDGE_KINDS in graph models."""
    from aicorellator.graph.models import VALID_EDGE_KINDS
    assert patterns.EDGE_KINDS == VALID_EDGE_KINDS


def test_flow_kinds_match_models() -> None:
    """FLOW_KINDS mirrors VALID_FLOW_KINDS in graph models (ADR-0015)."""
    from aicorellator.graph.models import VALID_FLOW_KINDS
    assert patterns.FLOW_KINDS == VALID_FLOW_KINDS
    assert patterns.FLOW_KINDS == {"control", "data", "structural"}


def test_structural_edge_kinds_subset_of_edge_kinds() -> None:
    """Structural edge kinds are real edge kinds (ADR-0015)."""
    assert patterns.STRUCTURAL_EDGE_KINDS == {"provides_tool", "guards"}
    assert patterns.STRUCTURAL_EDGE_KINDS <= patterns.EDGE_KINDS


def test_class_suffix_kinds_unique() -> None:
    """No duplicate suffixes in CLASS_SUFFIX_KINDS."""
    suffixes = [s for s, _ in patterns.CLASS_SUFFIX_KINDS]
    assert len(suffixes) == len(set(suffixes))


def test_sink_method_names_not_empty() -> None:
    assert len(patterns.SINK_METHOD_NAMES) > 0


def test_runner_to_agent_known() -> None:
    assert patterns.RUNNER_TO_AGENT["run_invoice_agent"] == "InvoiceAgent"


def test_provider_by_key_known() -> None:
    assert patterns.PROVIDER_BY_KEY["OPENAI_API_KEY"] == "openai"
    assert patterns.PROVIDER_BY_KEY["OLLAMA_BASE_URL"] == "ollama"


def test_exclude_dirs_include_git() -> None:
    assert ".git" in patterns.EXCLUDE_DIRS
    assert ".venv" in patterns.EXCLUDE_DIRS


def test_tsa_languages_include_python() -> None:
    assert "python" in patterns.TSA_SUPPORTED_LANGUAGES

def test_excluded_classes_include_base_agent() -> None:
    assert "BaseAgent" in patterns.EXCLUDED_CLASSES

def test_capability_sets_not_empty() -> None:
    assert len(patterns.SINK_CAPABILITIES) > 0
    assert len(patterns.AGENT_CAPABILITIES) > 0
    assert len(patterns.LLM_ENDPOINT_CAPABILITIES) > 0
    assert len(patterns.TOOL_CAPABILITIES) > 0


def test_sink_capabilities_include_egress() -> None:
    """http_egress is a sink capability (ADR-0009: exfiltration)."""
    assert "http_egress" in patterns.SINK_CAPABILITIES


def test_agent_capabilities_subset_of_llm_or_mcp() -> None:
    """Agent capabilities are a union of LLM and MCP triggers."""
    expected = (
        patterns.LLM_ENDPOINT_CAPABILITIES
        | patterns.TOOL_CAPABILITIES
    )
    assert patterns.AGENT_CAPABILITIES == expected