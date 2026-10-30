"""FastMCP middleware framework detector.

Detects the FastMCP SDK's middleware in Python code:

  - class AuditMiddleware(Middleware):                     a tool-call hook
  - class AuditMiddleware(fastmcp.server.middleware.Middleware):

`Middleware` is the SDK's own base for a middleware — the object FastMCP
calls around a request in the server's lifecycle (`on_call_tool`,
`on_list_tools`, ...). A project subclass is a hook into that lifecycle,
and ADR-0024 §3 makes it a `guardrail` node: the AST sees the base, not
whether the hook validates (a guardrail) or logs (an audit trail).
ADR-0024 §2's rule decides that: accept the false positive, the payload
is a signal the model weighs, not a filter.

Extracted nodes:

  - guardrail — one per `Middleware` subclass. `name` is the class name
                 (`AuditMiddleware`). `observed_by` is
                 `["ast", "framework:fastmcp_middleware"]` (ADR-0024 §1:
                 the detector's provenance, distinguishable from the
                 capability's `["ast"]` and the LLM classifier's).

Edges: none. This detector emits nodes and only nodes; B.4a
(`guardrail_edges`, stage 21) emits whatever `guards` edge its own
source-level evidence supports.

**What B.4a does with this node — unchanged here, pinned by the tests.**
The registration the SDK documents, `mcp.add_middleware(AuditMiddleware())`
at module level, is not evidence: the call sits in no component's body,
and B.4a's evidence is a call site inside a component's body or a literal
`@guardrail` decorator (ADR-0019 §1 — co-location is deliberately not
evidence). `COMPONENT_KINDS` in `guardrail_edges.py` is
`{"agent", "tool"}`: a `mcp_server` is deliberately not one of them (a
server is the container the tools live in, not a body a guardrail is
called from). So a `Middleware` subclass in a file with a `mcp_server`
node — the realistic FastMCP shape — yields a **node and no edge**. The
edge fires when a component's own body calls the middleware by bare name,
i.e. a `tool` whose body constructs it (`resolved_via="call_graph"`).
Both shapes are pinned in
`tests/test_normalizer_stages/test_fastmcp_middleware_detector.py`.

Per-file contract (ADR-0011): the detector reads one file and returns
nodes from that file alone.

**Contested points, decided here and pinned by the tests.**

  - **The base class is the trigger, not the methods.** What a class does
    with `on_call_tool` / `on_list_tools` is the method's body; whether
    the class *is* a middleware is the base. Every subclass is accepted —
    one that overrides four hooks and one that overrides none are both
    subclasses, and the detector does not rank them.
  - **No hook-name table.** The hook names are the SDK's (ADR-0024 §3)
    and are not read: a list of `on_*` names is the name-grants-a-kind
    table ADR-0016 decision 7 removed, whatever SDK supplies the names.
    The base class is the SDK's own convention and the detector reads it,
    the same posture as `BaseCallbackHandler` (B.4-gap-4), `LLMRails`
    (B.4-gap-3) and `Guard` (B.4-gap-2).
  - **The registration is not a second node.** `@mcp.middleware(...)`
    and `mcp.add_middleware(AuditMiddleware())` name the *server* the
    class is registered on, not a second thing the class is. The class is
    the node; the registration is B.4a's business, and this detector does
    not read it.
  - **Every base is read, not one.** ADR-0020's single-base rule answers
    which base a class inherits *capabilities* from — a different
    question. `class X(Middleware, ABC)` is a subclass of the SDK base
    and gets a node.
  - **The base reads by its last dotted segment.** `Middleware` and
    `fastmcp.server.middleware.Middleware` are the same base. A base that
    is neither a name nor an attribute (a subscript, a call, a string
    forward reference) is not read, and neither is an aliased import
    (`from fastmcp.server.middleware import Middleware as MW` then
    `class X(MW)`) — both false negatives, documented. The import itself
    is the fast path.
  - **A grandchild is a false negative.** `class Child(AuditMiddleware)`
    where `AuditMiddleware` subclasses `Middleware` is not a node: the
    detector is per-file (ADR-0011) and does not resolve the transitive
    chain, because the base it sees is a project name, not the SDK's
    base. Resolving that chain is a separate mechanism.
  - **An annotation is not a base.** `mw: Middleware` is a type, not a
    subclass, and gets no node.
  - **A bare instance is not a subclass.** `Middleware()` with no class
    statement is not a subclass and gets no node — the shape is the
    `class` statement, exactly as in the sibling detectors.
  - **The class name is not read.** `class AuditMiddleware(SomeOtherBase)`
    is not a node however the class is named: ADR-0016 decision 7 removed
    name-grants-a-kind, and the base is the SDK's own convention.
  - **Two same-name classes in one file collide on one id.** The id
    hashes framework + kind + file + name + host (the existing
    detectors' rule) and *not* the line, so a class name shadowed by a
    second definition in the same file produces one row: the accepted
    false negative of the sibling detectors. `line_start` is in
    `attributes`, so a reader can still see which definition each node
    came from.
"""

from __future__ import annotations

import ast
import hashlib
import logging

from ...graph.models import Node
from .base import DetectionResult, FrameworkDetector


log = logging.getLogger(__name__)


# The SDK's base class for a middleware. The name is the SDK's own: a
# detector may read the SDK's convention (ADR-0011), and ADR-0016's ban
# is on a table that grants a *kind* from a project's name, not on
# recognizing the base class one imports.
BASE_CLASS = "Middleware"


class FastMCPMiddlewareDetector(FrameworkDetector):
    """Detector for FastMCP's `Middleware` subclasses."""

    name = "fastmcp_middleware"
    # No version is inferable here: an import line carries none
    # (`from fastmcp.server.middleware import Middleware`), and the
    # detector does not read a lock file or an installed distribution.
    # ADR-0011 makes version support optional; a range is a later step.
    supported_versions = "*"

    def detect(
        self,
        file_path: str,
        tree: ast.Module,
        source: str,
        host_id: str = "localhost",
    ) -> DetectionResult:
        result = DetectionResult(framework=self.name)

        # Fast path: no FastMCP import, no detection. The SDK's
        # distribution imports as `fastmcp`, and its middleware lives at
        # `fastmcp.server.middleware` (`from fastmcp.server.middleware
        # import Middleware`), which is what `_has_import_prefix`
        # matches.
        if not self._has_import_prefix(tree, "fastmcp"):
            return result

        # Second gate: the file mentions the base class at all — in a
        # base, an annotation, an import. It is implied by the shape (a
        # base named `Middleware` is a mention) and can only narrow the
        # result, so it changes no node; it states the precondition in
        # one place and skips the walk for a file that imports FastMCP
        # but declares no middleware.
        if not _mentions_base_class(tree):
            return result

        for cls in self._find_subclasses(tree):
            node = self._make_node(
                kind="guardrail",
                name=cls.name,
                file_path=file_path,
                host_id=host_id,
                extra_attrs={
                    "framework": self.name,
                    # For a reader: the class statement's own line. Not
                    # part of the id (see the module docstring).
                    "line_start": cls.lineno,
                },
            )
            node.observed_by = self.provenance
            result.nodes.append(node)

        return result

    # --------------------------------------------------------
    # Provenance
    # --------------------------------------------------------

    @property
    def provenance(self) -> list[str]:
        """The `observed_by` of a node this detector emits.

        ADR-0024 §1: `["ast", "framework:<name>"]` for a
        detector-derived `guardrail`. A fresh list per call — `Node`
        would otherwise share one mutable default.
        """
        return ["ast", f"framework:{self.name}"]
        return ["ast", f"framework:{self.name}"]

    # --------------------------------------------------------
    # Subclasses
    # --------------------------------------------------------

    def _find_subclasses(self, tree: ast.Module) -> list[ast.ClassDef]:
        """Every `Middleware` subclass, in source order.

        `ast.walk` reaches the whole tree, so a class nested in another
        class or in a function counts — the same treatment a module-level
        class gets. Sorted by `(lineno, col_offset)`, so the node order
        is the file's order rather than `ast.walk`'s breadth-first
        traversal.
        """
        found = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and self._is_subclass(node)
        ]
        return sorted(found, key=lambda cls: (cls.lineno, cls.col_offset))

    @staticmethod
    def _is_subclass(cls: ast.ClassDef) -> bool:
        """True when one of the class's bases is the SDK's base class.

        One rule, the base's last dotted segment:

            class AuditMiddleware(Middleware):                    Name
            class AuditMiddleware(fastmcp.server.middleware.Middleware)
            class AuditMiddleware(Middleware, ABC):               one of two

        and the neighbouring shapes the same rule rejects:

            class NotAMiddleware(SomeOtherBase):                  another base
            mw: Middleware                                        a type
            Middleware()                                          an instance

        Every base is read, so a mixin alongside the SDK base does not
        hide it (ADR-0020's single-base rule answers a different
        question: which base a class inherits *capabilities* from). A
        base that is neither a name nor an attribute — a subscript, a
        call, a string forward reference — answers None and is not the
        base either.
        """
        return any(_last_segment(base) == BASE_CLASS for base in cls.bases)

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------

    def _make_node(
        self,
        kind: str,
        name: str,
        file_path: str,
        host_id: str,
        extra_attrs: dict | None = None,
    ) -> Node:
        h = hashlib.sha256(
            (
                f"framework:{self.name}:{kind}:{file_path}"
                f":{name}:{host_id}"
            ).encode("utf-8")
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


def _mentions_base_class(tree: ast.Module) -> bool:
    """True when `Middleware` appears anywhere in the file.

    A name (a base, an annotation, an argument), an attribute
    (`fastmcp.server.middleware.Middleware`), or an import
    (`from ... import Middleware`, ... `as MW`). The alias counts here
    even though the shape does not read it: the gate asks whether the
    SDK's base is in play at all.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == BASE_CLASS:
            return True
        if isinstance(node, ast.Attribute) and node.attr == BASE_CLASS:
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == BASE_CLASS:
                    return True
    return False


def _last_segment(node: ast.expr) -> str | None:
    """The last dotted segment of an expression, or None.

        Name("Middleware")                             → its own name
        Attribute(value=Name("middleware"), attr=...)  → the same name
        Subscript(...)                                 → None
        Call(...)                                      → None
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None

