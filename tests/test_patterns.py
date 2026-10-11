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