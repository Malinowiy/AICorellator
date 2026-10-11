# tests/test_llm_extractor.py
"""Unit tests for LLM extractor's config parsing."""

from __future__ import annotations

from aicorellator.parsing.llm_extractor import (
    _scan_toml,
    _scan_yaml,
    _walk_tree,
)


def test_yaml_simple() -> None:
    text = """
llm_provider: openai
llm_default_model: gpt-4o
"""
    result = _scan_yaml(text)
    assert result.get("LLM_PROVIDER") == "openai"
    assert result.get("LLM_DEFAULT_MODEL") == "gpt-4o"


def test_yaml_nested() -> None:
    """Nested YAML keys are flattened: services.llm.model."""
    text = """
services:
  llm:
    model: gpt-4o
    provider: openai
"""
    result = _scan_yaml(text)
    # Keys become SERVICES_LLM_MODEL etc.
    assert "SERVICES_LLM_MODEL" in result
    assert result["SERVICES_LLM_MODEL"] == "gpt-4o"


def test_yaml_docker_compose_style() -> None:
    """Docker-compose environment blocks are flattened."""
    text = """
services:
  app:
    environment:
      LLM_PROVIDER: openai
      OLLAMA_BASE_URL: http://localhost:11434
"""
    result = _scan_yaml(text)
    assert result.get("SERVICES_APP_ENVIRONMENT_LLM_PROVIDER") == "openai"
    assert result.get("SERVICES_APP_ENVIRONMENT_OLLAMA_BASE_URL") == "http://localhost:11434"


def test_toml_simple() -> None:
    text = '''
[llm]
provider = "openai"
model = "gpt-4o"
'''
    result = _scan_toml(text)
    assert result.get("LLM_PROVIDER") == "openai"
    assert result.get("LLM_MODEL") == "gpt-4o"


def test_yaml_invalid_syntax() -> None:
    """Invalid YAML returns empty dict, does not crash."""
    assert _scan_yaml("this is : not : valid : yaml") == {}


def test_toml_invalid_syntax() -> None:
    assert _scan_toml("this is not = valid = toml") == {}


def test_walk_tree_filters_irrelevant_keys() -> None:
    out: dict[str, str] = {}
    _walk_tree({"database_url": "postgres://...", "llm_model": "gpt-4o"}, out)
    assert "LLM_MODEL" in out
    assert "DATABASE_URL" not in out