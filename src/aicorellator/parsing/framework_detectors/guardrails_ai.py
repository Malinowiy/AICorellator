"""Guardrails AI framework detector.

Detects the Guardrails AI SDK's guardrail constructions in Python code:

  - Guard(...)                     a guard built from validators in code
  - Guard.from_pydantic(MyModel)   a guard from a Pydantic output model
  - Guard.for_string(...)          a guard for a string-shaped payload
  - Guard.for_rail(...)            a guard from a `.rail` specification

The SDK's own convention is the **class-level** call: `Guard(...)` and
`Guard.<factory>(...)` construct a guard. `guard.parse(...)` /
`guard.validate(...)` *use* one — an attribute call on an instance, not a
construction — and the detector skips them. The distinction is the base
of the attribute: `Guard` (the class) constructs, `guard` (the value a
construction was assigned to) does not.

Extracted nodes:

  - guardrail — one per construction call site. `name` is the assignment
                 target when the call is the value of an `Assign` /
                 `AnnAssign` (`guard = Guard(...)` → `guard`), and the
                 class name `Guard` otherwise. `observed_by` is
                 `["ast", "framework:guardrails_ai"]` (ADR-0024 §1: the
                 detector's provenance, distinguishable from the
                 capability's `["ast"]` and the LLM classifier's).

Edges: none. A `Guard` wraps an LLM call at runtime; the AST has no
static component the guard guards, so this detector has nothing to link.
`guardrail_edges` (B.4a, stage 21) reads the node like any other
`guardrail` node and emits what *its* source-level evidence supports —
which for this shape is usually nothing, because the call that binds the
guard (`guard.parse(text)`) is an attribute call and the call graph
records `ast.Name` targets only. A file with no visible component yields
a node and no edge; the node is in the graph either way.

Per-file contract (ADR-0011): the detector reads one file and returns
nodes from that file alone.

**Contested points, decided here and pinned by the tests.**

  - **Any class-level `Guard.<attr>(...)` is a construction, not a
    closed set of factory names.** Which factories the SDK grows is the
    SDK's business; the detector reads the class, not a method name
    (ADR-0016 decision 7 forbids a name table that grants a kind).
  - **An unassigned construction is named after the class.** The id
    hashes framework + kind + file + name + host (the existing
    detectors' rule) and *not* the line, so two unnamed `Guard()` calls
    in one file produce one id: the detector emits two nodes, the graph
    keeps one row. That is an accepted false negative — a second name
    would distinguish them, and a `Guard()` with no binding has none to
    give. `line_start` is in `attributes`, so a reader can still see
    which call each node came from.
  - **An aliased import is not matched.** `from guardrails import Guard
    as G` then `G(...)` is a false negative: the detector matches the
    class name `Guard`, and an ADR-0011 per-file detector does not carry
    an alias table. The import itself is the fast path, and it matches.
  - **A construction inside a class body or a method body counts.**
    `ast.walk` reaches the whole tree; `source_file` is the file and
    `line_start` the call's own line — the same treatment a module-level
    construction gets.
  - **The `@guard` decorator and `guard.parse` / `guard.validate` are
    not constructions.** ADR-0024 §3 names them among the SDK's shapes;
    this step produces the construction. `@guard` is a bare name, not a
    call, so there is nothing to build a node from — a shape of its own,
    not detected here. A class-level call *used* as a decorator
    (`@Guard.for_string(...)`) is a call site like any other and is
    detected, named after the class, because a decorator binds nothing.
"""

from __future__ import annotations

import ast
import hashlib
import logging

from ...graph.models import Node
from .base import DetectionResult, FrameworkDetector


log = logging.getLogger(__name__)


# The class that constructs a guard. The name is the SDK's own: a
# detector may read the SDK's convention (ADR-0011), and ADR-0016's ban
# is on a table that grants a *kind* from a project's name, not on
# recognizing the SDK one imports.
GUARD_CLASS = "Guard"


class GuardrailsAIDetector(FrameworkDetector):
    """Detector for the Guardrails AI SDK's `Guard` constructions."""

    name = "guardrails_ai"
    # No version is inferable here: an import line carries none
    # (`import guardrails`, `from guardrails.hub import ...`), and the
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

        # Fast path: no Guardrails AI import, no detection. A project
        # that imports `guardrails` — `import guardrails`, `from
        # guardrails import Guard`, `from guardrails.hub import ...` —
        # has the SDK.
        if not self._has_import_prefix(tree, "guardrails"):
            return result

        for name, call in self._find_constructions(tree):
            node = self._make_node(
                kind="guardrail",
                name=name,
                file_path=file_path,
                host_id=host_id,
                extra_attrs={
                    "framework": self.name,
                    # For a reader: the construction's own line. Not
                    # part of the id (see the module docstring).
                    "line_start": call.lineno,
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

    # --------------------------------------------------------
    # Constructions
    # --------------------------------------------------------

    def _find_constructions(
        self,
        tree: ast.Module,
    ) -> list[tuple[str, ast.Call]]:
        """Every `Guard` construction call site, in source order.

        Two passes. First the calls that are the value of an `Assign` /
        `AnnAssign`, which carry the name a reader wants — the variable
        the guard is bound to. Then the remaining calls anywhere in the
        tree, named after the class: an unassigned construction is not
        always a bare statement (`guards = [Guard()]` builds a list,
        `setup(Guard())` passes one to a call), and none of those has a
        name of its own.

        Sorted by `(lineno, col_offset)`, so the node order is the
        file's order regardless of `ast.walk`'s traversal.
        """
        found: list[tuple[str, ast.Call]] = []
        named_calls: set[int] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                call = node.value
                target = node.targets[0] if len(node.targets) == 1 else None
            elif isinstance(node, ast.AnnAssign):
                call = node.value
                target = node.target
            else:
                continue
            if not isinstance(call, ast.Call):
                continue
            if not self._is_construction(call):
                continue
            named_calls.add(id(call))
            found.append((_target_name(target) or GUARD_CLASS, call))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if id(node) in named_calls:
                continue
            if not self._is_construction(node):
                continue
            found.append((GUARD_CLASS, node))

        return sorted(
            found, key=lambda entry: (entry[1].lineno, entry[1].col_offset)
        )

    @staticmethod
    def _is_construction(call: ast.Call) -> bool:
        """True when the call constructs a `Guard`.

        Two forms, both class-level:

            Guard(...)                     Name("Guard")
            Guard.from_pydantic(...)       an attribute of the class
            guardrails.Guard.for_string(…) dotted, the same rule

        and one that is deliberately not:

            guard.parse(text)              an attribute of an *instance*

        The test is the base's last dotted segment: `Guard` constructs,
        `guard` uses. A `Guard` instance's method call is not a
        construction, and neither is a call whose base is anything else
        (an expression, a subscript, an instance).
        """
        func = call.func
        if isinstance(func, ast.Name):
            return func.id == GUARD_CLASS
        if isinstance(func, ast.Attribute):
            return _last_segment(func.value) == GUARD_CLASS
        return False

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


def _target_name(target: ast.expr | None) -> str | None:
    """The name a construction is bound to, or None.

        guard = Guard(...)        → "guard"
        self.guard = Guard(...)   → "guard"
        x[0] = Guard(...)         → None  (an element, not a name)
        a, b = Guard(), Guard()   → None  (a tuple target)

    `Assign` with a single `Name` or `Attribute` target is the shape the
    name comes from; a tuple target and a subscript answer None, and the
    caller falls back to the class name.
    """
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _last_segment(node: ast.expr) -> str | None:
    """The last dotted segment of an expression, or None.

        Name("Guard")                            → "Guard"
        Attribute(value=Name("g"), attr="Guard") → "Guard"
        Call(...)                                → None
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None

