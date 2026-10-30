"""Unit tests for the FastMCP middleware detector (B.4-gap-5).

ADR-0024 §3 decides the SDK detectors; this is the fourth and last. The
shape is the SDK's own **base class** — a `Middleware` subclass — and the
node it produces is a `guardrail` with
`observed_by=["ast", "framework:fastmcp_middleware"]` (ADR-0024 §1: the
detector's provenance, distinguishable from the capability's `["ast"]`).

S1–S6 are the shapes, P1/P2 the provenance, E1/E3 the empty edge list, R1
and R2 the registry, and the rest are the decisions the module docstring
states and the brief left open: the two-part fast path, all subclasses
accepted (a hook name is never read), every base read rather than one, the
registration (`@mcp.middleware(...)`, `mcp.add_middleware(...)`) as no
second node, the grandchild and the alias (both false negatives), and the
id collision of two same-name classes in one file.

The merge (M1) and B.4a's behaviour on a detector node (X1) need a
`NormalizeContext` and the file-backed `project` fixture, which lives in
`tests/test_normalizer_stages/conftest.py`; they are in
`tests/test_normalizer_stages/test_fastmcp_middleware_detector.py`.
"""

from __future__ import annotations

import ast

from aicorellator.parsing.framework_detectors.fastmcp_middleware import (
    FastMCPMiddlewareDetector,
)
from aicorellator.parsing.framework_detectors.registry import (
    all_names,
    resolve,
)

IMPORT = "from fastmcp.server.middleware import Middleware\n"

# A middleware built where a project builds one: the SDK's base, the hook
# the SDK calls, the project's own work in the body.
MIDDLEWARE_SOURCE = """\
from fastmcp.server.middleware import Middleware


class AuditMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        audit(context)
        return await call_next(context)
"""


def _detect(code: str):
    """One detector run over `code`, as the runner calls it."""
    tree = ast.parse(code)
    return FastMCPMiddlewareDetector().detect("test.py", tree, code)


def _names(result) -> list[str]:
    return [node.name for node in result.nodes]


# ============================================================
# The fast path
# ============================================================

def test_no_fastmcp_import_returns_empty() -> None:
    """No import, no detection — even when the shape is present.

    A project that subclasses `Middleware` without importing `fastmcp`
    is subclassing its own name, not the SDK's.
    """
    result = _detect(
        "class AuditMiddleware(Middleware):\n    pass\n"
    )
    assert result.nodes == []
    assert result.edges == []


def test_a_fastmcp_import_without_the_base_returns_empty() -> None:
    """FastMCP, but no middleware: the second half of the fast path.

    A project that registers tools on a `FastMCP` server and declares no
    middleware has none; the file is skipped without a walk.
    """
    source = (
        "from fastmcp import FastMCP\n"
        "\n"
        'mcp = FastMCP("audit")\n'
        "\n"
        "\n"
        '@mcp.tool\n'
        "def search(query):\n"
        "    return query\n"
    )
    result = _detect(source)
    assert result.nodes == []


# ============================================================
# S1, S2 — the shapes that are nodes
# ============================================================

def test_a_middleware_subclass_is_a_node() -> None:
    """S1: `class AuditMiddleware(Middleware)` → one `guardrail` node.

    The hook the class overrides is the body; the base is the shape. A
    subclass that overrides nothing is a node too (see below).
    """
    result = _detect(MIDDLEWARE_SOURCE)
    assert _names(result) == ["AuditMiddleware"]
    node = result.nodes[0]
    assert node.kind == "guardrail"
    assert node.attributes["framework"] == "fastmcp_middleware"
    assert node.attributes["source_file"] == "test.py"


def test_a_subclass_that_overrides_nothing_is_still_a_node() -> None:
    """The base is the trigger, not the methods (the module docstring).

    An empty subclass is a middleware the SDK calls; the detector does
    not rank subclasses by what their bodies do.
    """
    source = IMPORT + "\n\nclass MetricsMiddleware(Middleware):\n    pass\n"
    assert _names(_detect(source)) == ["MetricsMiddleware"]


def test_a_dotted_base_is_a_node() -> None:
    """S2: `fastmcp.server.middleware.Middleware` is the same base."""
    source = (
        "import fastmcp.server.middleware\n"
        "\n"
        "\n"
        "class AuditMiddleware(fastmcp.server.middleware.Middleware):\n"
        "    pass\n"
    )
    assert _names(_detect(source)) == ["AuditMiddleware"]


def test_a_mixin_alongside_the_base_does_not_hide_it() -> None:
    """Every base is read, not one: `(Middleware, ABC)` is a subclass."""
    source = (
        IMPORT
        + "from abc import ABC\n"
        "\n\nclass AuditMiddleware(Middleware, ABC):\n"
        "    pass\n"
    )
    assert _names(_detect(source)) == ["AuditMiddleware"]


# ============================================================
# S3–S6 — the shapes that are not nodes
# ============================================================

def test_a_class_named_middleware_but_not_subclassing_it_is_not_a_node(
) -> None:
    """S3: the name is not read (ADR-0016 decision 7).

    `AuditMiddleware` ending in `Middleware` grants nothing; the base is
    the SDK's own convention and that is what the detector reads.
    """
    source = (
        IMPORT
        + "\n\nclass AuditMiddleware(SomeOtherBase):\n"
        "    pass\n"
    )
    assert _detect(source).nodes == []


def test_an_annotation_is_not_a_node() -> None:
    """S4: `mw: Middleware` is a type, not a subclass."""
    source = (
        IMPORT
        + "\n\ndef configure(mw: Middleware):\n"
        "    return mw\n"
    )
    assert _detect(source).nodes == []


def test_a_grandchild_is_not_a_node() -> None:
    """S5: a false negative, documented and pinned.

    The detector is per-file (ADR-0011) and does not resolve the chain:
    `AuditMiddleware` is a project name here, not the SDK's base.
    """
    source = (
        IMPORT
        + "\n\nclass AuditMiddleware(Middleware):\n"
        "    pass\n"
        "\n\nclass Child(AuditMiddleware):\n"
        "    pass\n"
    )
    assert _names(_detect(source)) == ["AuditMiddleware"]


def test_a_bare_instance_is_not_a_node() -> None:
    """`Middleware()` with no class statement is not a subclass."""
    source = (
        IMPORT
        + "\n\nMIDDLEWARE = Middleware()\n"
    )
    assert _detect(source).nodes == []


def test_a_middleware_decorator_is_not_a_second_node() -> None:
    """S6: `@mcp.middleware(...)` registers a factory; the class is the node.

    The decorator names the server the middleware is added to. It is not
    a class statement and not a second thing the middleware is, so the
    detector emits one node — the subclass — and nothing for the
    decorator.
    """
    source = (
        IMPORT
        + "from fastmcp import FastMCP\n"
        "\n\nmcp = FastMCP(\"audit\")\n"
        "\n\nclass AuditMiddleware(Middleware):\n"
        "    pass\n"
        "\n\n@mcp.middleware\n"
        "def middleware_factory():\n"
        "    return AuditMiddleware()\n"
    )
    result = _detect(source)
    assert _names(result) == ["AuditMiddleware"]


def test_the_registration_is_not_a_second_node() -> None:
    """S6: the class is the node; the registration names the server.

    `mcp.add_middleware(AuditMiddleware())` registers the class on a
    server — the server is B.4a's business, and the detector emits the
    class and nothing else.
    """
    source = (
        IMPORT
        + "from fastmcp import FastMCP\n"
        "\n\nmcp = FastMCP(\"audit\")\n"
        "\n\nclass AuditMiddleware(Middleware):\n"
        "    pass\n"
        "\n\nmcp.add_middleware(AuditMiddleware())\n"
    )
    result = _detect(source)
    assert _names(result) == ["AuditMiddleware"]


# ============================================================
# P1, P2, E1, E3 — provenance and the empty edge list
# ============================================================

def test_the_provenance_is_the_detector() -> None:
    """P1: ADR-0024 §1 — `["ast", "framework:<name>"]`."""
    node = _detect(MIDDLEWARE_SOURCE).nodes[0]
    assert node.observed_by == ["ast", "framework:fastmcp_middleware"]


def test_the_framework_attribute_is_the_registry_name() -> None:
    """P2: `framework="fastmcp_middleware"`, lowercase, and the line."""
    node = _detect(MIDDLEWARE_SOURCE).nodes[0]
    assert node.attributes["framework"] == "fastmcp_middleware"
    assert node.attributes["line_start"] == 4


def test_the_provenance_list_is_fresh_per_node() -> None:
    """Two nodes, two lists — a shared list is one mutation away."""
    source = (
        IMPORT
        + "\n\nclass One(Middleware):\n"
        "    pass\n"
        "\n\nclass Two(Middleware):\n"
        "    pass\n"
    )
    first, second = _detect(source).nodes
    assert first.observed_by == second.observed_by
    assert first.observed_by is not second.observed_by


def test_the_detector_emits_no_edges() -> None:
    """E1/E3: node-only. B.4a emits the `guards` edge, not the detector."""
    result = _detect(MIDDLEWARE_SOURCE)
    assert result.edges == []
    assert result.framework == "fastmcp_middleware"


# ============================================================
# R1, R2 — registration
# ============================================================

def test_all_names_includes_fastmcp_middleware() -> None:
    assert "fastmcp_middleware" in all_names()
    assert all_names() == sorted(all_names())


def test_resolve_returns_an_instance() -> None:
    resolved = resolve(["fastmcp_middleware"])
    assert len(resolved) == 1
    assert isinstance(resolved[0], FastMCPMiddlewareDetector)
    assert resolved[0].name == "fastmcp_middleware"


def test_the_registry_class_is_the_detector() -> None:
    from aicorellator.parsing.framework_detectors.registry import (
        KNOWN_DETECTORS,
    )
    assert KNOWN_DETECTORS["fastmcp_middleware"] is FastMCPMiddlewareDetector


# ============================================================
# The boundaries the module docstring states
# ============================================================

def test_an_aliased_import_is_not_matched() -> None:
    """`Middleware as MW` is a false negative, pinned.

    The detector matches the base's name; an ADR-0011 per-file detector
    does not carry an alias table. The import is still the fast path: the
    file mentions the base, and the shape then declines to match.
    """
    source = (
        "from fastmcp.server.middleware import Middleware as MW\n"
        "\n\nclass AuditMiddleware(MW):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert result.nodes == []


def test_a_string_base_is_not_a_subclass() -> None:
    """`class AuditMiddleware("Middleware")` — a forward reference."""
    source = (
        IMPORT
        + '\n\nclass AuditMiddleware("Middleware"):\n'
        "    pass\n"
    )
    assert _detect(source).nodes == []


def test_a_subscript_base_is_not_a_subclass() -> None:
    """`Middleware[int]` is a subscript, not the base."""
    source = (
        IMPORT
        + "\n\nclass AuditMiddleware(Middleware[int]):\n"
        "    pass\n"
    )
    assert _detect(source).nodes == []


def test_two_subclasses_are_two_nodes_with_distinct_ids() -> None:
    """Two classes, two ids — the id hashes the class name."""
    source = (
        IMPORT
        + "\n\nclass AuditMiddleware(Middleware):\n"
        "    pass\n"
        "\n\nclass MetricsMiddleware(Middleware):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert _names(result) == ["AuditMiddleware", "MetricsMiddleware"]
    first, second = result.nodes
    assert first.id != second.id
    assert first.id.startswith("guardrail:")
    assert first.kind == second.kind == "guardrail"


def test_two_same_name_classes_in_one_file_collide_on_one_id() -> None:
    """The id hashes the name, not the line: one row, two lines.

    The accepted false negative of the sibling detectors. `line_start`
    is in `attributes`, so the definition each node came from is still
    visible; the second node's id repeats the first's.
    """
    source = (
        IMPORT
        + "\n\nclass AuditMiddleware(Middleware):\n"
        "    pass\n"
        "\n\nclass AuditMiddleware(Middleware):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert _names(result) == ["AuditMiddleware", "AuditMiddleware"]
    assert result.nodes[0].id == result.nodes[1].id
    assert result.nodes[0].attributes["line_start"] == 4
    assert result.nodes[1].attributes["line_start"] == 8


def test_the_nodes_are_in_source_order() -> None:
    """A breadth-first walk, one source-ordered result.

    `ast.walk` is breadth-first, so it reaches the module's second class
    before a class nested in a function body above it; the detector sorts
    by `(lineno, col_offset)`.
    """
    source = (
        IMPORT
        + "\n\nclass First(Middleware):\n"
        "    pass\n"
        "\n\ndef build():\n"
        "    class Nested(Middleware):\n"
        "        pass\n"
        "\n\nclass Second(Middleware):\n"
        "    pass\n"
    )
    assert _names(_detect(source)) == ["First", "Nested", "Second"]


def test_a_module_alias_prefix_is_matched() -> None:
    """`import fastmcp.server.middleware` — the fast path is the prefix.

    The SDK's middleware module imports as `fastmcp.server.middleware`,
    and the detector reads the base's last dotted segment, so the
    module-level import is enough for the fast path and the qualified
    base is the shape.
    """
    source = (
        "import fastmcp.server.middleware as fw\n"
        "\n\nclass AuditMiddleware(fw.Middleware):\n"
        "    pass\n"
    )
    assert _names(_detect(source)) == ["AuditMiddleware"]
