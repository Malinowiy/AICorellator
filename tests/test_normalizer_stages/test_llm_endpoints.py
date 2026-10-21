"""LlmEndpointStage: llm_endpoint nodes from configured endpoints."""

from __future__ import annotations

from aicorellator.parsing.llm_extractor import LLMEndpoint, LLMExtractorOutput
from aicorellator.parsing.normalizer_stages import LlmEndpointStage


def test_llm_endpoint_node_from_config(project) -> None:
    """The node is named provider:model and carries the config facts."""
    p = project(
        files={"config.py": ""},
        llm_output=LLMExtractorOutput(endpoints=[LLMEndpoint(
            provider="ollama",
            model="llama3.1",
            base_url="http://localhost:11434",
            source_file="config.py",
        )]),
    )

    delta = LlmEndpointStage().apply(p.delta, p.ctx)

    assert [(n.kind, n.name) for n in delta.nodes] == [
        ("llm_endpoint", "ollama:llama3.1"),
    ]
    assert delta.nodes[0].attributes["provider"] == "ollama"
    assert delta.nodes[0].attributes["base_url"] == "http://localhost:11434"
