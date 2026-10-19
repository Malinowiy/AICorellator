"""LangGraph framework detector.

LangGraph models agents as graphs: nodes are agents/tools, edges
are control flow. The detector extracts this topology directly.

Detects:
  - StateGraph(...) constructor
  - graph.add_node(name, func)      → node
  - graph.add_edge(src, dst)        → edge
  - graph.add_conditional_edges(...) → edge (conditional)
  - graph.set_entry_point(name)     → entry_point

Node kind is inferred from the name:
  - "agent", "*_agent", "model", "llm" → agent
  - "tools", "*_tools", "tool_node"    → tool
  - other                              → agent (default)

Edges are `delegates_to` between the named nodes. Entry points
are marked as `entry_point` nodes with `framework=langgraph`.

This detection does not require full data flow: LangGraph edges
are declared explicitly in code.
"""

from __future__ import annotations

import ast
import hashlib
import logging

from ...graph.models import Edge, Node
from .base import DetectionResult, FrameworkDetector


log = logging.getLogger(__name__)


# Variable names that hold a StateGraph instance. We detect them
# by the call to StateGraph(...) and track the assigned name.
STATEGRAPH_NAMES: frozenset[str] = frozenset({
    "StateGraph",
    "MessageGraph",
})


class LangGraphDetector(FrameworkDetector):
    """Detector for the LangGraph framework."""

    name = "langgraph"
    supported_versions = ">=0.1,<1.0"

    def detect(
        self,
        file_path: str,
        tree: ast.Module,
        source: str,
        host_id: str = "localhost",
    ) -> DetectionResult:
        result = DetectionResult(framework=self.name)

        if not self._has_import_prefix(tree, "langgraph"):
            return result

        # 1. Find the graph variable name(s).
        graph_vars = self._find_graph_variables(tree)
        if not graph_vars:
            return result

        # 2. Extract nodes, edges, entry points from method calls
        #    on those variables.
        node_names: set[str] = set()
        edges_pending: list[tuple[str, str, str]] = []   # (src, dst, kind)
        entry_points: list[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id not in graph_vars:
                continue

            method = node.func.attr

            if method == "add_node":
                name = self._first_string_arg(node)
                if name:
                    node_names.add(name)

            elif method == "add_edge":
                src = self._first_string_arg(node)
                dst = self._second_string_arg(node)
                if src and dst:
                    node_names.add(src)
                    node_names.add(dst)
                    edges_pending.append((src, dst, "delegates_to"))

            elif method == "add_conditional_edges":
                src = self._first_string_arg(node)
                if src:
                    node_names.add(src)
                    # Target nodes are either a list in args[2] or
                    # a mapping in kwargs["path_map"]. We extract
                    # known node names when possible.
                    targets = self._conditional_targets(node)
                    for dst in targets:
                        node_names.add(dst)
                        edges_pending.append((src, dst, "delegates_to"))

            elif method == "set_entry_point":
                name = self._first_string_arg(node)
                if name:
                    node_names.add(name)
                    entry_points.append(name)

        # 3. Build nodes.
        node_id_by_name: dict[str, str] = {}
        for name in sorted(node_names):
            kind = self._kind_for_node_name(name)
            node_id = self._make_node_id(kind, name, file_path, host_id)
            node_id_by_name[name] = node_id

            attrs = {
                "source_file": file_path,
                "framework": "langgraph",
                "graph_node_name": name,
            }
            result.nodes.append(Node(
                id=node_id,
                kind=kind,
                name=name,
                host_id=host_id,
                attributes=attrs,
            ))

        # 4. Mark entry points.
        for name in entry_points:
            node_id = node_id_by_name.get(name)
            if node_id is None:
                continue
            # Mark as entry point via attribute; do not create a
            # separate node (the graph node is the entry).
            for n in result.nodes:
                if n.id == node_id:
                    n.attributes["entry_point"] = "true"
                    break

        # 5. Build edges.
        for src, dst, kind in edges_pending:
            src_id = node_id_by_name.get(src)
            dst_id = node_id_by_name.get(dst)
            if src_id is None or dst_id is None:
                continue
            result.edges.append(Edge(
                id=self._make_edge_id(src_id, dst_id, kind, host_id),
                source_id=src_id,
                target_id=dst_id,
                kind=kind,
                host_id=host_id,
                # delegates_to is control flow with no confirmed data
                # flow (ADR-0015, decision 4).
                flow_kind="control",
                data_flow_confirmed="unknown",
                attributes={"framework": "langgraph"},
            ))

        return result

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------

    def _find_graph_variables(self, tree: ast.Module) -> set[str]:
        """Return names assigned to StateGraph(...) / MessageGraph(...)."""
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            call_name = self._call_name(node.value.func)
            if call_name in STATEGRAPH_NAMES:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
        return names

    def _conditional_targets(self, call: ast.Call) -> list[str]:
        """Extract target node names from add_conditional_edges.

        Supports two forms:
          - graph.add_conditional_edges(src, router, ["a", "b"])
          - graph.add_conditional_edges(src, router, {"x": "a", "y": "b"})
        """
        targets: list[str] = []

        # Positional: third argument (index 2) is the list or dict.
        if len(call.args) >= 3:
            third = call.args[2]
            if isinstance(third, ast.List):
                for elt in third.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        targets.append(elt.value)
            elif isinstance(third, ast.Dict):
                for value in third.values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        targets.append(value.value)

        # Keyword: path_map={...}
        for kw in call.keywords:
            if kw.arg == "path_map" and isinstance(kw.value, ast.Dict):
                for value in kw.value.values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        targets.append(value.value)

        return targets

    @staticmethod
    def _kind_for_node_name(name: str) -> str:
        """Infer node kind from name.

        This is a heuristic. LangGraph does not tag node kinds.
        """
        lower = name.lower()
        if lower in ("tools", "tool", "tool_node", "tools_node"):
            return "tool"
        if lower.endswith("_tools") or lower.endswith("_tool"):
            return "tool"
        if lower in ("agent", "model", "llm"):
            return "agent"
        if lower.endswith("_agent") or lower.endswith("_model"):
            return "agent"
        # Default: treat as agent (the common case in LangGraph).
        return "agent"

    @staticmethod
    def _first_string_arg(call: ast.Call) -> str | None:
        if call.args:
            first = call.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                return first.value
        return None

    @staticmethod
    def _second_string_arg(call: ast.Call) -> str | None:
        if len(call.args) >= 2:
            second = call.args[1]
            if isinstance(second, ast.Constant) and isinstance(second.value, str):
                return second.value
        return None

    @staticmethod
    def _call_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def _make_node_id(
        self,
        kind: str,
        name: str,
        file_path: str,
        host_id: str,
    ) -> str:
        h = hashlib.sha256(
            f"framework:{self.name}:{kind}:{file_path}:{name}:{host_id}".encode("utf-8")
        ).hexdigest()
        return f"{kind}:{h}"

    def _make_edge_id(
        self,
        src_id: str,
        dst_id: str,
        kind: str,
        host_id: str,
    ) -> str:
        h = hashlib.sha256(
            f"{src_id}:{dst_id}:{kind}:{host_id}".encode("utf-8")
        ).hexdigest()
        return f"edge:{h}"