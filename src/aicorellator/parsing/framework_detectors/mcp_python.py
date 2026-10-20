"""MCP Python SDK detector.

Detects MCP servers written against the low-level `mcp.server.Server`
API (as opposed to FastMCP, which is handled by `fastmcp.py`).

  - Server instances:   self.server = Server("name")
                        server = Server(...)
  - Tool decorators:    @self.server.call_tool()
                        @server.call_tool()
  - Other decorators registered on the server object are recognized
    but not emitted as nodes (list_resources, read_resource, etc.).

Extracted nodes:

  - mcp_server — one per `Server(...)` call, carrying the server name.
  - tool       — one per `@<server>.call_tool()` decorator, with the
                 parent server recorded in `parent_server`,
                 capabilities extracted from the decorated function, and
                 `tool_data_flow` from its signature (1.2a.2).

Edges: none from this detector. Cross-file resolution and the virtual
MCP entry point (MCP client → tool) are produced by the normalizer
(ADR-0013, A3). The normalizer copies `tool_data_flow` onto the edges
it builds, which is where the enrichment becomes an edge attribute.

Per-file contract (ADR-0011).
"""

from __future__ import annotations

import ast
import hashlib
import logging

from ...graph.models import Node
from .base import DetectionResult, FrameworkDetector
from .enrichment import enrich_provides_tool


log = logging.getLogger(__name__)


# Classes that construct a low-level MCP server.
MCP_SERVER_CLASSES: frozenset[str] = frozenset({
    "Server",
})

# Decorator methods that register a tool on the server.
# `call_tool` is the MCP Python SDK's decorator for tool handlers.
MCP_TOOL_DECORATORS: frozenset[str] = frozenset({
    "call_tool",
})


class MCPPythonDetector(FrameworkDetector):
    """Detector for the MCP Python SDK (`mcp.server.Server`)."""

    name = "mcp_python"
    supported_versions = ">=1.0"

    def detect(
        self,
        file_path: str,
        tree: ast.Module,
        source: str,
        host_id: str = "localhost",
    ) -> DetectionResult:
        result = DetectionResult(framework=self.name)

        # Fast path: no low-level MCP import, no detection.
        if not self._has_mcp_server_import(tree):
            return result

        # 1. Find server variables: `self.server = Server(...)` or
        #    `server = Server(...)`. Map dotted name → (server_name, call).
        #    Dotted because the receiver is commonly `self.server`.
        servers = self._find_server_variables(tree)
        if not servers:
            return result

        # 2. Emit mcp_server nodes. The server_name is the primary key
        #    used by the normalizer to link tools via parent_server.
        for var_name, (server_name, _call) in servers.items():
            result.nodes.append(self._make_node(
                kind="mcp_server",
                name=server_name,
                file_path=file_path,
                host_id=host_id,
                extra_attrs={
                    "framework": self.name,
                    "server_variable": var_name,
                },
            ))

        # 3. Find tools: `@<server_var>.call_tool()` on nested functions.
        tool_nodes = self._find_tool_decorated_functions(tree, servers)
        for func_name, func_node, server_name in tool_nodes:
            caps = self._caps_for_function(func_node)
            node = self._make_node(
                kind="tool",
                name=func_name,
                file_path=file_path,
                host_id=host_id,
                extra_attrs={
                    "framework": self.name,
                    "parent_server": server_name,
                    # 1.2a.2: the tool's signature answers "does the MCP
                    # client hand this tool data?". The normalizer copies
                    # this onto the server → tool edge it builds.
                    "tool_data_flow": enrich_provides_tool(func_node),
                },
            )
            node.capabilities = caps
            result.nodes.append(node)

        return result

    # --------------------------------------------------------
    # Import detection
    # --------------------------------------------------------

    @staticmethod
    def _has_mcp_server_import(tree: ast.Module) -> bool:
        """True if the file imports the low-level `Server` class.

        Matches:
            from mcp.server import Server
            from mcp.server import Server, stdio_server
        Does not match:
            from mcp.server.fastmcp import FastMCP  (handled by fastmcp.py)
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "mcp.server":
                    for alias in node.names:
                        if alias.name == "Server":
                            return True
        return False

    # --------------------------------------------------------
    # Server variables
    # --------------------------------------------------------

    def _find_server_variables(
        self,
        tree: ast.Module,
    ) -> dict[str, tuple[str, ast.Call]]:
        """Find `self.server = Server(...)` and `server = Server(...)`.

        Returns a mapping dotted-variable-name → (server_name, call node).
        The dotted name is used as the resolver key for decorators:
        `self.server` resolves to the key `"self.server"`, `server`
        resolves to `"server"`.
        """
        out: dict[str, tuple[str, ast.Call]] = {}

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            if self._call_name(node.value.func) not in MCP_SERVER_CLASSES:
                continue

            server_name = self._extract_server_name(node.value)
            for target in node.targets:
                key = self._target_key(target)
                if key is not None:
                    out[key] = (server_name, node.value)

        return out

    @staticmethod
    def _target_key(target: ast.expr) -> str | None:
        """Return a resolvable name for an assignment target.

        Handles:
            Name('server')                          → "server"
            Attribute(value=Name('self'), attr='server')  → "self.server"
        Returns None for anything else (subscripts, tuples, etc.).
        """
        if isinstance(target, ast.Name):
            return target.id
        if isinstance(target, ast.Attribute):
            if isinstance(target.value, ast.Name):
                return f"{target.value.id}.{target.attr}"
        return None

    @staticmethod
    def _extract_server_name(call: ast.Call) -> str:
        """Server name from `Server(...)`.

        Priority:
          1. First positional string literal.
          2. `name=` keyword if it is a string literal.
          3. Fallback: 'unnamed_mcp_server'.
        """
        for arg in call.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
            break  # only the first positional is a candidate
        for kw in call.keywords:
            if kw.arg == "name":
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    return kw.value.value
                break
        return "unnamed_mcp_server"

    # --------------------------------------------------------
    # Tool decorators
    # --------------------------------------------------------

    def _find_tool_decorated_functions(
        self,
        tree: ast.Module,
        servers: dict[str, tuple[str, ast.Call]],
    ) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, str]]:
        """Find functions decorated with `@<server>.call_tool()`.

        Returns tuples of (function name, function node, server name).
        The server variable may be `self.server` (common in class-based
        SDK usage) or a module-level name.
        """
        found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, str]] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                parent_key = self._tool_decorator_parent(dec)
                if parent_key is None:
                    continue
                if parent_key not in servers:
                    continue
                server_name = servers[parent_key][0]
                found.append((node.name, node, server_name))
                break

        return found

    @staticmethod
    def _tool_decorator_parent(dec: ast.expr) -> str | None:
        """Return the server variable key from a `.call_tool` decorator.

        Accepts:
            @server.call_tool()
            @self.server.call_tool()
        Returns the dotted variable name (`"server"` or `"self.server"`),
        or None if the decorator is not a `.call_tool` attribute.
        """
        target: ast.expr = dec
        if isinstance(target, ast.Call):
            target = target.func
        if not isinstance(target, ast.Attribute):
            return None
        if target.attr not in MCP_TOOL_DECORATORS:
            return None
        return MCPPythonDetector._dotted_name(target.value)

    @staticmethod
    def _dotted_name(node: ast.expr) -> str | None:
        """Return the dotted name of an expression.

        Name('server')                              → "server"
        Attribute(value=Name('self'), attr='server') → "self.server"
        """
        parts: list[str] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            parts.reverse()
            return ".".join(parts)
        return None

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------

    @staticmethod
    def _caps_for_function(
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> list[str]:
        """Extract capabilities from a function body (ADR-0009)."""
        from ..capability_extractor import _scan_function
        return sorted(_scan_function(func_node))

    @staticmethod
    def _call_name(node: ast.expr) -> str | None:
        """Resolve the last segment of a call target."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

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