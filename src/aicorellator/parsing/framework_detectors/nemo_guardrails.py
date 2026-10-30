"""NeMo Guardrails framework detector.

Detects the NeMo Guardrails SDK's rails construction in Python code:

  - LLMRails(config)                       the rails object itself
  - LLMRails(config=config)                the keyword form
  - nemoguardrails.rails.LLMRails(config)  the qualified form

`LLMRails` is the SDK's enforcement object: it wraps a generation call
and runs the rails' input / output flows around it. That is the shape
ADR-0024 §3 names, and the one this detector reads.

Two of the SDK's other shapes are deliberately not nodes:

  - `RailsConfig(...)` / `RailsConfig.from_path(...)` /
    `RailsConfig.from_content(...)` — a **config loader**. The config is
    a value passed *to* the rails, not the thing that enforces. The
    detector's rule is the target's last dotted segment (`LLMRails`), so
    a `RailsConfig` call is skipped structurally, not by a deny-list.
  - `rails.generate(...)` / `rails.generate_async(...)` — an instance
    method. Its target's last segment is `generate`, so the same rule
    rejects it.

Extracted nodes:

  - guardrail — one per `LLMRails(...)` construction call site. `name`
                 is the assignment target when the call is the value of
                 an `Assign` / `AnnAssign`
                 (`rails = LLMRails(config)` → `rails`), and the class
                 name `LLMRails` otherwise. `observed_by` is
                 `["ast", "framework:nemo_guardrails"]` (ADR-0024 §1:
                 the detector's provenance, distinguishable from the
                 capability's `["ast"]` and the LLM classifier's).

Edges: none. An `LLMRails` wraps a generation call at runtime; the AST
holds no static component for the rails to protect, so this detector has
nothing to link. `guardrail_edges` (B.4a, stage 21) reads the node like
any other `guardrail` node and emits what *its* source-level evidence
supports — for the usual shape that is nothing, because the call that
binds the rails (`rails.generate(...)`) is an attribute call and the
call graph records `ast.Name` targets only. A file with no visible
component yields a node and no edge; the node is in the graph either
way.

Per-file contract (ADR-0011): the detector reads one file and returns
nodes from that file alone.

**Contested points, decided here and pinned by the tests.**

  - **`LLMRails` is the guardrail; `RailsConfig` is not.** The config
    loader produces a configuration value; the rails instance is what
    enforcement runs through. ADR-0024 §3 names the `LLMRails(...)`
    construction as the node and the `RailsConfig` factories as part of
    the same SDK's surface — the one without enforcement is skipped.
  - **`@action` is not a node.** The decorator registers a project
    function in the rails runtime; what it does (validation, formatting,
    output processing) is the function's body, not the decorator's
    shape. ADR-0024 §3 names the class construction, and this step
    produces that. `@action` and `@nemoguardrails.action(...)` are not
    calls to `LLMRails`, so the rule skips them structurally.
  - **The rule is the *target's* last dotted segment.** `LLMRails(...)`
    and `nemoguardrails.rails.LLMRails(config)` are the same
    construction. `LLMRails` is a plain constructor — the SDK's
    factories live on `RailsConfig`, which is not a node — so a
    hypothetical `LLMRails.<factory>(...)` is a **false negative**:
    documented, and a one-line widening if the SDK grows one. The rule
    is also what makes an attribute named `LLMRails` on anything a call
    site; the fast path (a file that imports the SDK) is what keeps
    that from being a false positive in practice.
  - **The arguments are not read.** Positional, keyword or none, the
    call is the shape: `LLMRails(config)` and `LLMRails(config=config)`
    are the SDK's signature, and an arity the SDK would reject at
    runtime is not the detector's call to make (ADR-0016 decision 7's
    posture: the shape is the call, not a table of accepted argument
    lists).
  - **An unassigned construction is named after the class.** The id
    hashes framework + kind + file + name + host (the existing
    detectors' rule) and *not* the line, so two unnamed
    `LLMRails(config)` calls in one file produce one id: the detector
    emits two nodes, the graph keeps one row. That is an accepted false
    negative — a second name would distinguish them, and an
    `LLMRails(...)` with no binding has none to give. `line_start` is in
    `attributes`, so a reader can still see which call each node came
    from.
  - **An aliased import is not matched.** `from nemoguardrails import
    LLMRails as LR` then `LR(config)` is a false negative: the detector
    matches the class name `LLMRails`, and an ADR-0011 per-file detector
    does not carry an alias table. The import itself is the fast path,
    and it matches.
  - **A construction inside a class body or a method body counts.**
    `ast.walk` reaches the whole tree; `source_file` is the file and
    `line_start` the call's own line — the same treatment a module-level
    construction gets.
"""

from __future__ import annotations

import ast
import hashlib
import logging

from ...graph.models import Node
from .base import DetectionResult, FrameworkDetector


log = logging.getLogger(__name__)


# The class that constructs the rails. The name is the SDK's own: a
# detector may read the SDK's convention (ADR-0011), and ADR-0016's ban
# is on a table that grants a *kind* from a project's name, not on
# recognizing the SDK one imports.
RAILS_CLASS = "LLMRails"


class NeMoGuardrailsDetector(FrameworkDetector):
    """Detector for the NeMo Guardrails SDK's `LLMRails` constructions."""

    name = "nemo_guardrails"
    # No version is inferable here: an import line carries none
    # (`import nemoguardrails`, `from nemoguardrails.rails import ...`),
    # and the detector does not read a lock file or an installed
    # distribution. ADR-0011 makes version support optional; a range is
    # a later step.
    supported_versions = "*"

    def detect(
        self,
        file_path: str,
        tree: ast.Module,
        source: str,
        host_id: str = "localhost",
    ) -> DetectionResult:
        result = DetectionResult(framework=self.name)

        # Fast path: no NeMo Guardrails import, no detection. A project
        # that imports `nemoguardrails` — `import nemoguardrails`,
        # `from nemoguardrails import LLMRails`, `from
        # nemoguardrails.rails import RailsConfig` — has the SDK.
        if not self._has_import_prefix(tree, "nemoguardrails"):
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
        """Every `LLMRails` construction call site, in source order.

        Two passes. First the calls that are the value of an `Assign` /
        `AnnAssign`, which carry the name a reader wants — the variable
        the rails is bound to. Then the remaining calls anywhere in the
        tree, named after the class: an unassigned construction is not
        always a bare statement (`return LLMRails(config)`,
        `setup(LLMRails(config))`), and none of those has a name of its
        own.

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
            found.append((_target_name(target) or RAILS_CLASS, call))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if id(node) in named_calls:
                continue
            if not self._is_construction(node):
                continue
            found.append((RAILS_CLASS, node))

        return sorted(
            found, key=lambda entry: (entry[1].lineno, entry[1].col_offset)
        )

    @staticmethod
    def _is_construction(call: ast.Call) -> bool:
        """True when the call constructs the SDK's rails.

        One rule, the target's last dotted segment:

            LLMRails(config)                       Name("LLMRails")
            nemoguardrails.rails.LLMRails(config)  dotted, same name
            LLMRails(config=config)                the keyword form

        and the SDK's neighbouring shapes, which the same rule rejects:

            RailsConfig.from_path("config")        the config loader
            rails.generate(messages=[])            an instance method
            @nemoguardrails.action()               a custom action

        A call whose target has any other last segment (`RailsConfig`,
        `from_path`, `from_content`, `generate`, `action`) is not this
        construction — the test is the SDK's class name, and `LLMRails`
        is the class that enforces. A target that is neither a name nor
        an attribute (a subscript, a call returning a callable) answers
        None and is not a construction either.
        """
        return _last_segment(call.func) == RAILS_CLASS

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

        rails = LLMRails(config)         → "rails"
        self.rails = LLMRails(config)    → "rails"
        x[0] = LLMRails(config)          → None  (an element, not a name)
        a, b = LLMRails(c), LLMRails(c)  → None  (a tuple target)

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

        Name("LLMRails")                             → "LLMRails"
        Attribute(value=Name("n"), attr="LLMRails")  → "LLMRails"
        Call(...)                                    → None
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None

