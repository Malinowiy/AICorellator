"""FastMCP framework detector.

Detects FastMCP servers and their tools in Python code:

  - Server instances:  mcp = FastMCP("name", ...)
                       app = FastMCP(...)
  - Tool decorators:   @mcp.tool()
                       @mcp.tool
                       @mcp.tool(description=...)

FastMCP (from `mcp.server.fastmcp`) is the declarative MCP server SDK.
It is distinct from the lower-level `mcp.server.Server` API, which is
handled by a separate detector (`mcp_python.py`).

Extracted nodes:

  - mcp_server — one per `FastMCP(...)` call, carrying the server name.
  - tool       — one per `@<var>.tool(...)` decorator, with the parent
                 server recorded in `parent_server`. Capabilities are
                 extracted from the decorated function body (ADR-0009).

Edges: none from this detector. Cross-file resolution and the virtual
MCP entry point (MCP client → tool) are produced by the normalizer
(ADR-0013, A3 variant).

Per-file contract (ADR-0011): the detector reads one file and returns
nodes from that file alone.
"""

from __future__ import annotations

import ast
import hashlib
import logging

from ...graph.models import Node
from .base import DetectionResult, FrameworkDetector


log = logging.getLogger(__name__)


# Classes that construct a FastMCP server. Only the FastMCP class from
# `mcp.server.fastmcp` is matched; the lower-level `mcp.server.Server`
# is handled by mcp_python.py.
FASTMCP_CLASSES: frozenset[str] = frozenset({
    "FastMCP",
})


class FastMCPDetector(FrameworkDetector):
    """Detector for the FastMCP server SDK."""

    name = "fastmcp"
    supported_versions = ">=0.1"

    def detect(
        self,
        file_path: str,
        tree: ast.Module,
        source: str,
        host_id: str = "localhost",
    ) -> DetectionResult:
        result = DetectionResult(framework=self.name)

        # Fast path: no FastMCP import, no detection.
        if not self._has_fastmcp_import(tree):
            return result

        # 1. Find server variables: NAME = FastMCP(...).
        #    Map variable name → (server_name, call_node).
        servers = self._find_server_variables(tree)
        if not servers:
            return result

        # 2. Emit mcp_server nodes.
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

        # 3. Find tools: @<var>.tool(...) where <var> is a known server.
        #    Emit tool nodes with parent_server set.
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
                },
            )
            node.capabilities = caps
            result.nodes.append(node)

        return result

    # --------------------------------------------------------
    # Import detection
    # --------------------------------------------------------

    @staticmethod
    def _has_fastmcp_import(tree: ast.Module) -> bool:
        """True if the file imports from `mcp.server.fastmcp`.

        Matches:
            from mcp.server.fastmcp import FastMCP
            from mcp.server.fastmcp import FastMCP, tool
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("mcp.server.fastmcp"):
                    return True
            # Also accept plain `import mcp.server.fastmcp`.
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("mcp.server.fastmcp"):
                        return True
        return False

    # --------------------------------------------------------
    # Server variables
    # --------------------------------------------------------

    def _find_server_variables(
        self,
        tree: ast.Module,
    ) -> dict[str, tuple[str, ast.Call]]:
        """Find `NAME = FastMCP(...)` assignments.

        Returns a mapping variable name → (server_name, call node).
        Only module-level and class-body assignments are considered:
        a FastMCP instance is conventionally created once per module.
        """
        out: dict[str, tuple[str, ast.Call]] = {}

        # Module-level: `mcp = FastMCP(...)`.
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            if self._call_name(node.value.func) not in FASTMCP_CLASSES:
                continue
            server_name = self._extract_server_name(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = (server_name, node.value)

        # Class-level: rare, but some layouts put the server on the class.
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, ast.Assign):
                    continue
                if not isinstance(item.value, ast.Call):
                    continue
                if self._call_name(item.value.func) not in FASTMCP_CLASSES:
                    continue
                server_name = self._extract_server_name(item.value)
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = (server_name, item.value)

        return out

    @staticmethod
    def _extract_server_name(call: ast.Call) -> str:
        """Server name from `FastMCP(...)`.

        Priority:
          1. `name=` keyword if it is a string literal.
          2. First positional string literal.
          3. Fallback: 'unnamed_mcp_server'.
        """
        # name= keyword.
        for kw in call.keywords:
            if kw.arg == "name":
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    return kw.value.value
                break
        # First positional string.
        for arg in call.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
            break  # only the first positional is a candidate
        return "unnamed_mcp_server"

    # --------------------------------------------------------
    # Tool decorators
    # --------------------------------------------------------

    def _find_tool_decorated_functions(
        self,
        tree: ast.Module,
        servers: dict[str, tuple[str, ast.Call]],
    ) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, str]]:
        """Find functions decorated with `@<server_var>.tool(...)`.

        Returns tuples of (function name, function node, parent server name).
        The decorator may be written as:
            @mcp.tool()
            @mcp.tool
            @mcp.tool(description="...")
        where `mcp` is a variable bound to a FastMCP instance.
        """
        found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, str]] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                parent_var = self._tool_decorator_parent(dec)
                if parent_var is None:
                    continue
                if parent_var not in servers:
                    continue
                server_name = servers[parent_var][0]
                found.append((node.name, node, server_name))
                break  # one decorator match per function

        return found

    @staticmethod
    def _tool_decorator_parent(dec: ast.expr) -> str | None:
        """Return the server variable name from a `@<var>.tool` decorator.

        Accepts:
            @mcp.tool()             → ast.Call(Attribute(value=Name('mcp'), attr='tool'))
            @mcp.tool               → ast.Attribute(value=Name('mcp'), attr='tool')
            @mcp.tool(description=…) → same as the first
        Returns None if the decorator is not a `.tool` attribute of a Name.
        """
        target: ast.expr = dec
        if isinstance(target, ast.Call):
            target = target.func
        if not isinstance(target, ast.Attribute):
            return None
        if target.attr != "tool":
            return None
        if not isinstance(target.value, ast.Name):
            return None
        return target.value.id

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