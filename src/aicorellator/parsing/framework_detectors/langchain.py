"""LangChain framework detector.

Detects LangChain usage in Python code:

  - Agents: AgentExecutor, initialize_agent, create_*_agent
  - Tools: @tool decorator, Tool(...) instances,
           StructuredTool.from_function(...)
  - LLM clients: ChatOpenAI, ChatAnthropic, Ollama, OpenAI

Extracted nodes:
  - agent (from AgentExecutor / initialize_agent / create_*_agent)
  - tool (from @tool / Tool / StructuredTool.from_function)
  - llm_endpoint (from ChatOpenAI / ChatAnthropic / Ollama / OpenAI)

Edges (best-effort, from the same file):
  - agent → tool (uses_tool via Tool list in AgentExecutor)

Edges from other files (agent in one file, tool in another) are
not derived — that requires import resolution, which is out of
scope for Phase 2. Capability extraction covers the behavioral
side.
"""

from __future__ import annotations

import ast
import hashlib
import logging

from ...graph.models import Edge, Node
from .base import DetectionResult, FrameworkDetector


log = logging.getLogger(__name__)


# Known LangChain LLM client class names.
LANGCHAIN_LLM_CLASSES: frozenset[str] = frozenset({
    "ChatOpenAI",
    "ChatAnthropic",
    "ChatGoogleGenerativeAI",
    "ChatCohere",
    "ChatMistralAI",
    "Ollama",
    "OllamaLLM",
    "OpenAI",
    "Anthropic",
})

# Known LangChain agent class names / factory functions.
LANGCHAIN_AGENT_CLASSES: frozenset[str] = frozenset({
    "AgentExecutor",
    "initialize_agent",
    "create_react_agent",
    "create_openai_tools_agent",
    "create_openai_functions_agent",
    "create_structured_chat_agent",
    "create_tool_calling_agent",
    "create_json_chat_agent",
})

# Known LangChain tool-related names.
LANGCHAIN_TOOL_CLASSES: frozenset[str] = frozenset({
    "Tool",
    "StructuredTool",
    "BaseTool",
})


class LangChainDetector(FrameworkDetector):
    """Detector for the LangChain framework."""

    name = "langchain"
    supported_versions = ">=0.1,<1.0"

    def detect(
        self,
        file_path: str,
        tree: ast.Module,
        source: str,
        host_id: str = "localhost",
    ) -> DetectionResult:
        result = DetectionResult(framework=self.name)

        # Fast path: no LangChain import, no detection.
        if not self._has_import_prefix(tree, "langchain"):
            return result

        # 1. Agent nodes.
        for cls_name, node in self._find_classes(tree, LANGCHAIN_AGENT_CLASSES):
            result.nodes.append(self._make_node(
                kind="agent",
                name=cls_name,
                file_path=file_path,
                host_id=host_id,
                extra_attrs={"framework": "langchain"},
            ))

        # 2. Tool nodes — from @tool decorator.
        for func_name, node in self._find_tool_decorated_functions(tree):
            result.nodes.append(self._make_node(
                kind="tool",
                name=func_name,
                file_path=file_path,
                host_id=host_id,
                extra_attrs={"framework": "langchain"},
            ))

        # 3. Tool nodes — from Tool(...) / StructuredTool.from_function(...).
        for tool_name, node in self._find_tool_instances(tree):
            result.nodes.append(self._make_node(
                kind="tool",
                name=tool_name,
                file_path=file_path,
                host_id=host_id,
                extra_attrs={"framework": "langchain"},
            ))

        # 4. LLM endpoints.
        for cls_name, node in self._find_classes(tree, LANGCHAIN_LLM_CLASSES):
            provider = self._provider_from_name(cls_name)
            result.nodes.append(self._make_node(
                kind="llm_endpoint",
                name=cls_name,
                file_path=file_path,
                host_id=host_id,
                extra_attrs={
                    "framework": "langchain",
                    "provider": provider,
                },
            ))

        return result

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------

    def _find_classes(
        self,
        tree: ast.Module,
        names: frozenset[str],
    ) -> list[tuple[str, ast.Call]]:
        """Find Call nodes whose func is one of `names`.

        Returns (call_name, call_node) tuples. `call_name` is the
        source-level name (e.g. `AgentExecutor`).
        """
        found: list[tuple[str, ast.Call]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = self._call_name(node.func)
            if name in names:
                found.append((name, node))
        return found

    def _find_tool_decorated_functions(
        self,
        tree: ast.Module,
    ) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
        """Find functions decorated with @tool."""
        found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                dec_name = self._decorator_name(dec)
                if dec_name == "tool":
                    found.append((node.name, node))
                    break
        return found

    def _find_tool_instances(
        self,
        tree: ast.Module,
    ) -> list[tuple[str, ast.Call]]:
        """Find Tool(...) / StructuredTool.from_function(...) instances.

        For Tool(...) we read the first `name=` keyword, or the first
        positional argument (usually a string).
        For StructuredTool.from_function(...) we read the function name.
        """
        found: list[tuple[str, ast.Call]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func_name = self._call_name(node.func)

            # Tool(name="...", func=...)
            if func_name == "Tool":
                tool_name = self._string_keyword(node, "name")
                if tool_name is None and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        tool_name = first.value
                if tool_name:
                    found.append((tool_name, node))

            # StructuredTool.from_function(func)
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "from_function"
            ):
                if node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Name):
                        found.append((first.id, node))
                    elif isinstance(first, ast.Attribute):
                        found.append((first.attr, node))

        return found

    @staticmethod
    def _call_name(node: ast.expr) -> str | None:
        """Return the last segment of a dotted call target.

        langchain.agents.AgentExecutor  → "AgentExecutor"
        AgentExecutor                   → "AgentExecutor"
        """
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    @staticmethod
    def _decorator_name(node: ast.expr) -> str | None:
        """Return the last segment of a decorator name.

        @tool                           → "tool"
        @langchain.tool                 → "tool"
        @tool(...)                      → "tool"
        """
        if isinstance(node, ast.Call):
            node = node.func
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    @staticmethod
    def _string_keyword(node: ast.Call, key: str) -> str | None:
        for kw in node.keywords:
            if kw.arg == key and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    return kw.value.value
        return None

    @staticmethod
    def _provider_from_name(cls_name: str) -> str:
        lower = cls_name.lower()
        for hint, provider in (
            ("openai", "openai"),
            ("anthropic", "anthropic"),
            ("google", "google"),
            ("cohere", "cohere"),
            ("mistral", "mistral"),
            ("ollama", "ollama"),
        ):
            if hint in lower:
                return provider
        return "unknown"

    def _make_node(
        self,
        kind: str,
        name: str,
        file_path: str,
        host_id: str,
        extra_attrs: dict | None = None,
    ) -> Node:
        h = hashlib.sha256(
            f"framework:{self.name}:{kind}:{file_path}:{name}:{host_id}".encode("utf-8")
        ).hexdigest()
        node_id = f"{kind}:{h}"
        attrs = {"source_file": file_path, **(extra_attrs or {})}
        return Node(
            id=node_id,
            kind=kind,
            name=name,
            host_id=host_id,
            attributes=attrs,
        )