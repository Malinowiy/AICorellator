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
    # `provides_tool` is deliberately absent: it is control, because the
    # access path to a tool is the candidate attack path (ADR-0015).
    assert patterns.STRUCTURAL_EDGE_KINDS == {"guards"}
    assert patterns.STRUCTURAL_EDGE_KINDS <= patterns.EDGE_KINDS


def test_control_edge_kinds_subset_of_edge_kinds() -> None:
    """Control edge kinds are real edge kinds (ADR-0015)."""
    assert patterns.CONTROL_EDGE_KINDS <= patterns.EDGE_KINDS
    assert "provides_tool" in patterns.CONTROL_EDGE_KINDS
    assert "uses_model" in patterns.CONTROL_EDGE_KINDS
    assert "uses_tool" in patterns.CONTROL_EDGE_KINDS
    assert "delegates_to" in patterns.CONTROL_EDGE_KINDS
    # `writes_to` (1.3a.1) is control: a declaration that the writer
    # produces the source, not a traced dataflow.
    assert "writes_to" in patterns.CONTROL_EDGE_KINDS
    # No edge kind is both control and structural.
    assert not (patterns.CONTROL_EDGE_KINDS & patterns.STRUCTURAL_EDGE_KINDS)


def test_writes_to_is_a_registered_edge_kind() -> None:
    """The 1.3a.1 writer edge is in the registry and not structural."""
    assert "writes_to" in patterns.EDGE_KINDS
    assert "writes_to" not in patterns.STRUCTURAL_EDGE_KINDS


def test_class_a_name_tables_are_gone() -> None:
    """No name in the registry grants a kind (ADR-0016, decision 7).

    Kinds come from capabilities and framework provenance. A table that
    maps a class name, method name or provider name to a kind is a
    class-A heuristic, and there are none left here.
    """
    for name in (
        "CLASS_SUFFIX_KINDS",
        "EXTRA_AGENT_CLASSES",
        "EXTRA_LLM_CLASSES",
        "PROVIDER_HINTS",
        "SINK_METHOD_NAMES",
        "SINK_CATEGORY_HINTS",
        "TOOL_METHOD_PREFIXES",
        "SKIP_METHOD_NAMES",
        "RUNNER_TO_AGENT",
        "DELEGATE_METHOD_PREFIX",
    ):
        assert not hasattr(patterns, name), name


def test_exclusions_remain() -> None:
    """A name may still withhold a node — the removal covered grants."""
    assert "BaseAgent" in patterns.EXCLUDED_CLASSES
    assert "Detector" in patterns.EXCLUDED_CLASS_SUFFIXES


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


def test_lethal_trifecta_caps_needs_all_three_sets() -> None:
    """The combination, not any one half (ADR-0009, 1.3d)."""
    assert patterns.lethal_trifecta_caps(
        ["reads_untrusted", "reads_env", "executes_shell"]
    ) == ("executes_shell", "reads_env", "reads_untrusted")

    for caps in (
        [],
        ["reads_untrusted"],
        ["reads_untrusted", "reads_env"],
        ["reads_untrusted", "executes_shell"],
        ["reads_env", "executes_shell"],
    ):
        assert patterns.lethal_trifecta_caps(caps) == ()


def test_lethal_trifecta_caps_returns_sorted_members_of_the_sets() -> None:
    """Every returned capability comes from one of the three sets."""
    caps = [
        "reads_untrusted",
        "reads_secrets",
        "reads_env",
        "http_egress",
        "writes_filesystem",
        "executes_shell",
        "executes_code",
        "reads_filesystem",  # neither half
    ]

    matched = patterns.lethal_trifecta_caps(caps)

    assert list(matched) == sorted(matched)
    assert "reads_filesystem" not in matched
    assert set(matched) == set(caps) - {"reads_filesystem"}


def test_lethal_trifecta_caps_accepts_every_egress_capability() -> None:
    """The egress half is the constant, all four of it."""
    for egress in patterns.LETHAL_TRIFECTA_EGRESS_CAPS:
        assert patterns.lethal_trifecta_caps(
            ["reads_untrusted", "reads_env", egress]
        )
