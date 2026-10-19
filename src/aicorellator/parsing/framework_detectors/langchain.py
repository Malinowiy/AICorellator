"""LangChain framework detector.

Detects LangChain usage in Python code:

  - Agents: AgentExecutor, initialize_agent, create_*_agent,
            create_agent (LangChain 1.0), ConversationalChatAgent,
            and other agent classes
  - Tools: @tool decorator, Tool(...) instances,
           StructuredTool.from_function(...), BaseTool subclasses
  - LLM clients: ChatOpenAI, ChatAnthropic, Ollama, ChatOllama,
                 ChatLiteLLM, and other chat models

Extracted nodes carry capabilities from the capability extractor
(ADR-0009): a @tool-decorated function with exec() gets the
executes_code capability.

Extracted nodes:
  - agent (from AgentExecutor / initialize_agent / create_*_agent)
  - tool (from @tool / Tool / StructuredTool.from_function)
  - llm_endpoint (from ChatOpenAI / ChatAnthropic / Ollama / OpenAI)

Edges (best-effort, from the same file):
  - uses_tool  (agent → tool, from tools=[...] in create_agent /
                AgentExecutor / initialize_agent)
  - uses_model (agent → llm_endpoint, from model=... or llm=...)

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
# Includes both legacy langchain.llms.* and modern
# langchain_<provider>.* packages.
LANGCHAIN_LLM_CLASSES: frozenset[str] = frozenset({
    # OpenAI family
    "ChatOpenAI",
    "OpenAI",
    "AzureChatOpenAI",
    # Anthropic
    "ChatAnthropic",
    "Anthropic",
    # Google
    "ChatGoogleGenerativeAI",
    "ChatVertexAI",
    # Other providers
    "ChatCohere",
    "ChatMistralAI",
    "ChatGroq",
    "ChatBedrock",
    "ChatFireworks",
    "ChatTogether",
    "ChatDeepSeek",
    # Local
    "Ollama",
    "OllamaLLM",
    "ChatOllama",
    # LiteLLM and gateways
    "ChatLiteLLM",
    "ChatOpenRouter",
})


# Known LangChain agent class names / factory functions.
LANGCHAIN_AGENT_CLASSES: frozenset[str] = frozenset({
    # Modern AgentExecutor
    "AgentExecutor",
    # Factory functions
    "initialize_agent",
    "create_react_agent",
    "create_openai_tools_agent",
    "create_openai_functions_agent",
    "create_structured_chat_agent",
    "create_tool_calling_agent",
    "create_json_chat_agent",
    "create_xml_agent",
    "create_self_ask_with_search_agent",
    "create_conversational_retrieval_agent",
    # LangChain 1.0 unified factory
    "create_agent",
    # Agent base classes
    "ConversationalChatAgent",
    "ChatAgent",
    "ZeroShotAgent",
    "ReActDocstoreAgent",
    "ReActAgent",
    "StructuredChatAgent",
    "OpenAIFunctionsAgent",
    "OpenAIToolsAgent",
    "XMLAgent",
    "SelfAskWithSearchAgent",
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
    supported_versions = ">=0.1,<2.0"

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
        agent_calls = self._find_calls(tree, LANGCHAIN_AGENT_CLASSES)
        for cls_name, node in agent_calls:
            result.nodes.append(self._make_node(
                kind="agent",
                name=cls_name,
                file_path=file_path,
                host_id=host_id,
                extra_attrs={"framework": "langchain"},
            ))

        # 2. Tool nodes — from @tool decorator.
        # Capabilities are extracted from the function body, because
        # the detector knows exactly which functions are tools.
        for func_name, func_node in self._find_tool_decorated_functions(tree):
            caps = self._caps_for_function(func_node)
            node = self._make_node(
                kind="tool",
                name=func_name,
                file_path=file_path,
                host_id=host_id,
                extra_attrs={"framework": "langchain"},
            )
            node.capabilities = caps
            result.nodes.append(node)

        # 3. Tool nodes — from Tool(...) / StructuredTool.from_function(...).
        for tool_name, call_node in self._find_tool_instances(tree):
            result.nodes.append(self._make_node(
                kind="tool",
                name=tool_name,
                file_path=file_path,
                host_id=host_id,
                extra_attrs={"framework": "langchain"},
            ))

        # 4. LLM endpoints.
        llm_calls = self._find_calls(tree, LANGCHAIN_LLM_CLASSES)
        for cls_name, node in llm_calls:
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

        # 5. Edges — agent → tool (uses_tool) and agent → llm (uses_model).
        #    Best-effort, same-file only. Uses module-level assignments
        #    to resolve symbolic arguments (e.g. tools=USER_TOOLS).
        result.edges.extend(self._find_agent_edges(
            tree=tree,
            file_path=file_path,
            host_id=host_id,
            agent_calls=agent_calls,
            result=result,
        ))

        return result

    # --------------------------------------------------------
    # Edges
    # --------------------------------------------------------

    def _find_agent_edges(
        self,
        tree: ast.Module,
        file_path: str,
        host_id: str,
        agent_calls: list[tuple[str, ast.Call]],
        result: DetectionResult,
    ) -> list[Edge]:
        """Build uses_tool / uses_model edges from agent constructor calls.

        For each call in agent_calls, look at the `tools=` and `model=` /
        `llm=` arguments. Resolve symbolic names through module-level
        assignments, then link the corresponding agent node to each
        resolved tool / llm node in `result`.

        Fallback: if tools= is symbolic and cannot be resolved, link to
        ALL tool nodes in this file (with a warning). Better than no edges.
        """
        edges: list[Edge] = []

        # Index existing nodes from this same detect() call by name.
        # Note: node names may repeat within a file; we keep the last one
        # (same as the rest of the codebase does for classification keys).
        tool_node_by_name: dict[str, Node] = {
            n.name: n for n in result.nodes if n.kind == "tool"
        }
        llm_node_by_name: dict[str, Node] = {
            n.name: n for n in result.nodes if n.kind == "llm_endpoint"
        }
        agent_node_by_name: dict[str, Node] = {
            n.name: n for n in result.nodes if n.kind == "agent"
        }

        # Collect module-level assignments:
        #   USER_TOOLS = [read_ticket, search_docs, ...]
        #   llm = ChatOllama(model=..., base_url=...)
        # Maps name -> ast node (List / Tuple / Call / Name).
        assignments = self._collect_module_assignments(tree)

        for agent_name, call in agent_calls:
            agent_node = agent_node_by_name.get(agent_name)
            if agent_node is None:
                continue

            # --- tools= ---
            tools_arg = self._keyword_arg(call, "tools")
            if tools_arg is not None:
                tool_names = self._resolve_tool_names(tools_arg, assignments)
                if not tool_names:
                    # Fallback: link to all tools in this file.
                    log.debug(
                        "langchain: could not resolve tools= in %s, "
                        "linking agent %s to all %d tools",
                        file_path, agent_name, len(tool_node_by_name),
                    )
                    tool_names = list(tool_node_by_name.keys())

                for tname in tool_names:
                    target = tool_node_by_name.get(tname)
                    if target is None:
                        continue
                    edges.append(self._make_edge(
                        source_id=agent_node.id,
                        target_id=target.id,
                        kind="uses_tool",
                        host_id=host_id,
                    ))

            # --- model= / llm= ---
            for key in ("model", "llm"):
                llm_arg = self._keyword_arg(call, key)
                if llm_arg is None:
                    continue
                llm_names = self._resolve_llm_names(llm_arg, assignments)
                for lname in llm_names:
                    target = llm_node_by_name.get(lname)
                    if target is None:
                        continue
                    edges.append(self._make_edge(
                        source_id=agent_node.id,
                        target_id=target.id,
                        kind="uses_model",
                        host_id=host_id,
                    ))

        return edges

    @staticmethod
    def _collect_module_assignments(tree: ast.Module) -> dict[str, ast.expr]:
        """Collect `NAME = <expr>` at module level.

        Only simple Name targets. Later assignments win — this matches
        the runtime behavior in practice (last binding is what's visible
        at the call site, in most real code).
        """
        out: dict[str, ast.expr] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
        return out

    def _resolve_tool_names(
        self,
        node: ast.expr,
        assignments: dict[str, ast.expr],
        _visited: set[str] | None = None,
    ) -> list[str]:
        """Resolve an expression to a list of tool names.

        Guards against cycles in the assignment table (e.g. `X = Y`,
        `Y = X`): a name already visited is not followed again.
        """
        if _visited is None:
            _visited = set()

        # Symbolic reference → follow the assignment.
        if isinstance(node, ast.Name):
            if node.id in _visited:
                return []
            _visited.add(node.id)
            target = assignments.get(node.id)
            if target is None:
                return []
            return self._resolve_tool_names(target, assignments, _visited)

        # Literal list or tuple.
        if isinstance(node, (ast.List, ast.Tuple)):
            names: list[str] = []
            for elt in node.elts:
                names.extend(self._resolve_tool_names(elt, assignments, _visited))
            return names

        if isinstance(node, ast.Name):
            return [node.id]

        if isinstance(node, ast.Call):
            name = self._call_name(node.func)
            if name in LANGCHAIN_TOOL_CLASSES:
                tname = self._string_keyword(node, "name")
                if tname is None and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        tname = first.value
                if tname:
                    return [tname]
                if isinstance(node.func, ast.Attribute) and node.func.attr == "from_function":
                    if node.args:
                        first = node.args[0]
                        if isinstance(first, ast.Name):
                            return [first.id]
                        if isinstance(first, ast.Attribute):
                            return [first.attr]
            return []

        return []
    
    def _resolve_llm_names(
        self,
        node: ast.expr,
        assignments: dict[str, ast.expr],
        _visited: set[str] | None = None,
    ) -> list[str]:
        """Resolve an expression to a list of LLM class names.

        Guards against cycles in the assignment table.
        """
        if _visited is None:
            _visited = set()

        if isinstance(node, ast.Name):
            if node.id in _visited:
                return []
            _visited.add(node.id)
            target = assignments.get(node.id)
            if target is None:
                return []
            return self._resolve_llm_names(target, assignments, _visited)

        if isinstance(node, ast.Call):
            name = self._call_name(node.func)
            if name in LANGCHAIN_LLM_CLASSES:
                return [name]
            return []

        if isinstance(node, ast.Attribute):
            if node.attr in LANGCHAIN_LLM_CLASSES:
                return [node.attr]
            return []

        return []

    @staticmethod
    def _keyword_arg(call: ast.Call, key: str) -> ast.expr | None:
        """Return the value of `key=` in a call, or None."""
        for kw in call.keywords:
            if kw.arg == key:
                return kw.value
        return None

    def _make_edge(
        self,
        source_id: str,
        target_id: str,
        kind: str,
        host_id: str,
    ) -> Edge:
        h = hashlib.sha256(
            f"{source_id}:{target_id}:{kind}:{host_id}".encode("utf-8")
        ).hexdigest()
        edge_id = f"edge:{h}"
        return Edge(
            id=edge_id,
            source_id=source_id,
            target_id=target_id,
            kind=kind,
            host_id=host_id,
            # uses_tool / uses_model are control flow with no confirmed
            # data flow (ADR-0015, decision 4).
            flow_kind="control",
            data_flow_confirmed="unknown",
            attributes={"framework": self.name},
        )

    # --------------------------------------------------------
    # Helpers (node extraction)
    # --------------------------------------------------------

    def _find_calls(
        self,
        tree: ast.Module,
        names: frozenset[str],
    ) -> list[tuple[str, ast.Call]]:
        """Find Call nodes whose func resolves to one of `names`.

        `names` matches either:
          - the direct name: AgentExecutor(...)
          - a method whose name begins with `from_`:
            AgentExecutor.from_agent_and_tools(...)
        In both cases the resolved name is the class name.
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
    def _caps_for_function(
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> list[str]:
        """Extract capabilities from a function body (ADR-0009)."""
        from ..capability_extractor import _scan_function
        return sorted(_scan_function(func_node))

    @staticmethod
    def _call_name(node: ast.expr) -> str | None:
        """Return the resolved name of a call target.

        Resolution rules:
          - Name:        AgentExecutor              → "AgentExecutor"
          - Attribute:   langchain.agents.Foo       → "Foo"
          - Classmethod: AgentExecutor.from_x(...)  → "AgentExecutor"
          - Chained:     obj.method.attr            → attr
        """
        if isinstance(node, ast.Name):
            return node.id

        if isinstance(node, ast.Attribute):
            if node.attr.startswith("from_"):
                if isinstance(node.value, ast.Name):
                    return node.value.id
                if isinstance(node.value, ast.Attribute):
                    return node.value.attr
            return node.attr

        return None

    @staticmethod
    def _decorator_name(node: ast.expr) -> str | None:
        """Return the last segment of a decorator name."""
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
            ("gemini", "google"),
            ("vertex", "google"),
            ("cohere", "cohere"),
            ("mistral", "mistral"),
            ("groq", "groq"),
            ("bedrock", "aws"),
            ("fireworks", "fireworks"),
            ("together", "together"),
            ("deepseek", "deepseek"),
            ("ollama", "ollama"),
            ("litellm", "litellm"),
            ("openrouter", "openrouter"),
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