"""Framework detector interface.

A detector recognizes a specific AI framework in a file and extracts
nodes and edges from that framework's conventions.

Detectors work on Python AST only. Non-Python files are handled by
TSA (structural) and capability extraction (behavioral).

Each detector declares:

  - name: short identifier ("langchain", "langgraph", ...)
  - supported_versions: a version range string; if the detected
    framework version is outside the range, detection proceeds
    but a warning is logged (ADR-0011)
  - detect(file_path, tree, source) -> DetectionResult

DetectionResult contains nodes and edges. The orchestrator merges
them with results from other detectors and from name/capability
classification.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from ...graph.models import Edge, Node


@dataclass
class DetectionResult:
    """Nodes and edges extracted by a framework detector."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    # Provenance: which framework produced this.
    framework: str = ""
    # Version detected (if inferable from imports).
    detected_version: str | None = None


class FrameworkDetector:
    """Base class for framework detectors."""

    name: str = ""
    supported_versions: str = "*"

    def detect(
        self,
        file_path: str,
        tree: ast.Module,
        source: str,
        host_id: str = "localhost",
    ) -> DetectionResult:
        """Detect framework usage in one file.

        Returns an empty DetectionResult if the framework is not
        present. Never raises — detection failures are logged and
        return an empty result.
        """
        raise NotImplementedError

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------

    def _has_import(self, tree: ast.Module, module_prefix: str) -> bool:
        """True if the file imports from a module with this prefix.

        Matches:
            import langchain
            import langchain.agents
            from langchain.agents import AgentExecutor
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == module_prefix or alias.name.startswith(module_prefix + "."):
                        return True
            elif isinstance(node, ast.ImportFrom):
                if node.module and (
                    node.module == module_prefix
                    or node.module.startswith(module_prefix + ".")
                ):
                    return True
        return False

    def _imported_names(self, tree: ast.Module, module_prefix: str) -> set[str]:
        """Return names imported from a module prefix.

        Example:
            from langchain.agents import AgentExecutor, initialize_agent
            → {"AgentExecutor", "initialize_agent"}
        """
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and (
                    node.module == module_prefix
                    or node.module.startswith(module_prefix + ".")
                ):
                    for alias in node.names:
                        names.add(alias.asname or alias.name)
        return names
    def _has_import_prefix(self, tree: ast.Module, prefix: str) -> bool:
        """True if the file imports from a module starting with `prefix`.

        Matches:
            import langchain
            import langchain.agents
            import langchain_anthropic
            from langchain.tools import tool
            from langchain_community.llms import Ollama
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == prefix or alias.name.startswith(prefix + ".") or alias.name.startswith(prefix + "_"):
                        return True
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    if (
                        node.module == prefix
                        or node.module.startswith(prefix + ".")
                        or node.module.startswith(prefix + "_")
                    ):
                        return True
        return False    