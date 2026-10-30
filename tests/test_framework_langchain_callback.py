"""Unit tests for the LangChain callbacks detector (B.4-gap-4).

ADR-0024 §3 decides the SDK detectors; this is the third. The shape is
the SDK's own **base class** — a `BaseCallbackHandler` subclass — and the
node it produces is a `guardrail` with
`observed_by=["ast", "framework:langchain_callback"]` (ADR-0024 §1: the
detector's provenance, distinguishable from the capability's `["ast"]`).

S1–S6 are the shapes, P1/P2 the provenance, E1 the empty edge list, R1/R2
the registry, and the rest are the decisions the module docstring states
and the brief left open: the two-part fast path, all subclasses accepted
(a hook name is never read), every base read rather than one, the
grandchild and the alias (both false negatives), and the id collision of
two same-name classes in one file.

The merge (M1) and B.4a's behaviour on a detector node (X1) need a
`NormalizeContext` and the file-backed `project` fixture, which lives in
`tests/test_normalizer_stages/conftest.py`; they are in
`tests/test_normalizer_stages/test_langchain_callback_detector.py`.
"""

from __future__ import annotations

import ast

from aicorellator.parsing.framework_detectors.langchain_callback import (
    LangChainCallbackDetector,
)
from aicorellator.parsing.framework_detectors.registry import (
    all_names,
    resolve,
)

IMPORT = "from langchain_core.callbacks import BaseCallbackHandler\n"

# A handler built where a project builds one: the SDK's base, the hook
# the SDK calls, the project's own work in the body.
HANDLER_SOURCE = """\
from langchain_core.callbacks import BaseCallbackHandler


class AuditHook(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        audit(prompts)
"""


def _detect(code: str):
    """One detector run over `code`, as the runner calls it."""
    tree = ast.parse(code)
    return LangChainCallbackDetector().detect("test.py", tree, code)


def _names(result) -> list[str]:
    return [node.name for node in result.nodes]


# ============================================================
# The fast path
# ============================================================

def test_no_langchain_import_returns_empty() -> None:
    """No import, no detection — even when the shape is present.

    A project that subclasses `BaseCallbackHandler` without importing
    `langchain` is subclassing its own name, not the SDK's.
    """
    result = _detect(
        "class MyHandler(BaseCallbackHandler):\n    pass\n"
    )
    assert result.nodes == []
    assert result.edges == []


def test_a_langchain_import_without_the_base_returns_empty() -> None:
    """LangChain, but no callbacks: the second half of the fast path.

    A project using `AgentExecutor` and no handler has no callback; the
    file is skipped without a walk.
    """
    source = (
        "from langchain.agents import AgentExecutor\n"
        "\n"
        "\n"
        "class MyHandler(SomeOtherBase):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert result.nodes == []


def test_each_import_form_of_the_sdk_is_the_fast_path() -> None:
    """`import langchain` / `langchain_core` / the callback modules.

    `_has_import_prefix` is the base helper every detector uses; the
    SDK's distributions import as `langchain` and `langchain_core`, and
    the base class lives under `.callbacks`.
    """
    for header in (
        "import langchain",
        "import langchain_core.callbacks",
        "from langchain.callbacks import BaseCallbackHandler",
        "from langchain.callbacks.base import BaseCallbackHandler",
        "from langchain_core.callbacks import BaseCallbackHandler",
    ):
        code = (
            f"{header}\n"
            "\n\nclass MyHandler(BaseCallbackHandler):\n"
            "    pass\n"
        )
        result = _detect(code)
        assert _names(result) == ["MyHandler"], header


# ============================================================
# S1 — a BaseCallbackHandler subclass
# ============================================================

def test_a_subclass_is_one_guardrail_node_named_after_the_class() -> None:
    result = _detect(HANDLER_SOURCE)
    assert _names(result) == ["AuditHook"]
    node = result.nodes[0]
    assert node.kind == "guardrail"
    assert node.attributes["framework"] == "langchain_callback"
    assert node.attributes["source_file"] == "test.py"


def test_a_subclass_that_overrides_no_hook_is_still_a_node() -> None:
    """The base is the trigger; the methods are not read.

    A callback that overrides nothing is a hook with a default
    behaviour — a weak guardrail, and ADR-0024 §2 accepts it. There is
    no hook-name table and no "does it override" test.
    """
    source = (
        IMPORT
        + "\n\nclass Noop(BaseCallbackHandler):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert _names(result) == ["Noop"]


def test_a_hook_method_without_the_base_is_not_a_node() -> None:
    """A class that *looks* like a handler but is not one.

    `on_llm_start` on a class that does not subclass the SDK's base is
    the project's own method. The method names are the SDK's (ADR-0024
    §3) and are deliberately not read (ADR-0016 decision 7).
    """
    source = (
        IMPORT
        + "\n\nclass Logger:\n"
        "    def on_llm_start(self, serialized, prompts, **kwargs):\n"
        "        audit(prompts)\n"
    )
    result = _detect(source)
    assert result.nodes == []


# ============================================================
# S2 — a dotted base
# ============================================================

def test_a_dotted_base_is_a_subclass() -> None:
    """`class MyHandler(langchain.callbacks.base.BaseCallbackHandler)`.

    The rule is the base's last dotted segment, so the fully qualified
    form is the same base (ADR-0024 §3 reads the SDK's own convention).
    """
    source = (
        "import langchain.callbacks.base\n"
        "\n\n"
        "class MyHandler("
        "langchain.callbacks.base.BaseCallbackHandler):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert _names(result) == ["MyHandler"]


def test_a_module_alias_prefix_is_the_same_base() -> None:
    source = (
        "import langchain_core.callbacks as callbacks\n"
        "\n\n"
        "class MyHandler(callbacks.BaseCallbackHandler):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert _names(result) == ["MyHandler"]


# ============================================================
# S3 — a Handler-shaped class that is not a subclass
# ============================================================

def test_a_handler_shaped_class_is_not_a_subclass() -> None:
    """The name is not read — ADR-0016 decision 7.

    A `*Handler` class whose base is something else is not a LangChain
    callback, whatever it is called. `BaseCallbackHandlerLike` is the
    near miss worth pinning: the same prefix, not the same class.
    """
    source = (
        IMPORT
        + "\n\nclass NotAHandler(SomeOtherBase):\n"
        "    pass\n"
        "\n\nclass BaseCallbackHandlerLike(BaseCallback):\n"
        "    pass\n"
        "\n\nclass Plain:\n"
        "    pass\n"
    )
    result = _detect(source)
    assert result.nodes == []


# ============================================================
# S4 — an annotation
# ============================================================

def test_an_annotation_is_not_a_subclass() -> None:
    """`handler: BaseCallbackHandler` is a type, not a base.

    The file names the base — so the fast path passes — and no class
    statement subclasses it.
    """
    source = (
        IMPORT
        + "\n\nhandler: BaseCallbackHandler = build_handler()\n"
        "\n\ndef register(h: BaseCallbackHandler) -> None:\n"
        "    handlers.append(h)\n"
    )
    result = _detect(source)
    assert result.nodes == []


# ============================================================
# S5 — a grandchild
# ============================================================

def test_a_grandchild_is_not_a_subclass() -> None:
    """A false negative, documented and pinned.

    `class Child(MyHandler)` where `MyHandler` subclasses the SDK's base
    is not a node: the base the child names is a project class, and a
    per-file detector (ADR-0011) does not resolve the chain. Resolving
    it is a separate mechanism, and this test is the pin that says so.
    """
    source = (
        IMPORT
        + "\n\nclass MyHandler(BaseCallbackHandler):\n"
        "    pass\n"
        "\n\nclass Child(MyHandler):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert _names(result) == ["MyHandler"]


# ============================================================
# S6 — a bare instance
# ============================================================

def test_a_bare_instance_is_not_a_subclass() -> None:
    """`BaseCallbackHandler()` with no subclass: no hook, no node.

    A false negative — a handler built from the base itself overrides
    no hook, which is unusual enough to leave unmodelled.
    """
    source = IMPORT + "\nhandler = BaseCallbackHandler()\n"
    result = _detect(source)
    assert result.nodes == []


# ============================================================
# The bases the rule reads
# ============================================================

def test_a_mixin_alongside_the_base_does_not_hide_it() -> None:
    """Every base is read, not one (ADR-0020's rule is a different one)."""
    source = (
        IMPORT
        + "\n\nclass MyHandler(abc.ABC, BaseCallbackHandler):\n"
        "    pass\n"
        "\n\nclass OtherHandler(BaseCallbackHandler, AbcMixin):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert _names(result) == ["MyHandler", "OtherHandler"]


def test_a_subscripted_base_is_not_read() -> None:
    """`class X(BaseCallbackHandler[T])` — a false negative, pinned.

    `_last_segment` answers Name and Attribute, and None for anything
    else, the same posture as the sibling detectors' helper of the same
    name.
    """
    source = (
        IMPORT
        + "\n\nclass MyHandler(BaseCallbackHandler[AuditEvent]):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert result.nodes == []


# ============================================================
# Nesting
# ============================================================

def test_a_class_nested_in_a_function_or_a_class_counts() -> None:
    """`ast.walk` reaches the whole tree, the sibling detectors' rule."""
    source = (
        IMPORT
        + "\n\ndef build():\n"
        "    class Inner(BaseCallbackHandler):\n"
        "        pass\n"
        "    return Inner\n"
        "\n\nclass Outer:\n"
        "    class Nested(BaseCallbackHandler):\n"
        "        pass\n"
    )
    result = _detect(source)
    assert _names(result) == ["Inner", "Nested"]


# ============================================================
# P1, P2 — provenance
# ============================================================

def test_the_node_names_the_detector_and_the_ast() -> None:
    """P1 — ADR-0024 §1's value for a detector-derived guardrail."""
    result = _detect(HANDLER_SOURCE)
    assert result.nodes[0].observed_by == [
        "ast", "framework:langchain_callback",
    ]


def test_the_provenance_is_a_fresh_list() -> None:
    """Two nodes must not share one list instance.

    `Node.observed_by` defaults to a new list per node; assigning the
    same object twice would let one node's provenance be edited through
    another's.
    """
    source = (
        IMPORT
        + "\n\nclass First(BaseCallbackHandler):\n"
        "    pass\n"
        "\n\nclass Second(BaseCallbackHandler):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert result.nodes[0].observed_by is not result.nodes[1].observed_by
    result.nodes[0].observed_by.append("scratch")
    assert result.nodes[1].observed_by == [
        "ast", "framework:langchain_callback",
    ]


def test_the_framework_attribute_is_the_registry_name() -> None:
    """P2 — the same string the registry and the provenance use."""
    result = _detect(HANDLER_SOURCE)
    node = result.nodes[0]
    assert node.attributes["framework"] == "langchain_callback"
    assert node.attributes["framework"] == LangChainCallbackDetector.name


def test_the_attributes_carry_the_class_line_and_the_file() -> None:
    """`line_start` is the class statement's line, and informational."""
    result = _detect(HANDLER_SOURCE)
    assert result.nodes[0].attributes["line_start"] == 4
    assert result.nodes[0].attributes["source_file"] == "test.py"


def test_the_detection_result_names_the_framework() -> None:
    assert _detect(HANDLER_SOURCE).framework == "langchain_callback"
    assert _detect("import json\n").framework == "langchain_callback"


def test_the_detector_declares_the_wildcard_version() -> None:
    """No version is inferable from an import line (ADR-0011)."""
    detector = LangChainCallbackDetector()
    assert detector.supported_versions == "*"
    assert detector.name == "langchain_callback"


# ============================================================
# E1 — no edges
# ============================================================

def test_the_detector_returns_no_edges() -> None:
    """Node-only. A callback is a hook, not a component a guard wraps.

    B.4a's `guardrail_edges` decides what a `guards` edge needs, and it
    runs on this node like on any other `guardrail` node.
    """
    result = _detect(HANDLER_SOURCE)
    assert result.nodes != []
    assert result.edges == []


# ============================================================
# The id
# ============================================================

def test_two_subclasses_are_two_nodes_with_two_ids() -> None:
    source = (
        IMPORT
        + "\n\nclass First(BaseCallbackHandler):\n"
        "    pass\n"
        "\n\nclass Second(BaseCallbackHandler):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert _names(result) == ["First", "Second"]
    assert len({node.id for node in result.nodes}) == 2


def test_the_id_depends_on_the_file_and_on_the_host() -> None:
    """Per-file and per-host, like the other detectors (ADR-0011)."""
    detector = LangChainCallbackDetector()
    tree = ast.parse(HANDLER_SOURCE)
    here = detector.detect("a.py", tree, "")
    there = detector.detect("b.py", tree, "")
    other_host = detector.detect("a.py", tree, "", host_id="other")
    assert here.nodes[0].id != there.nodes[0].id
    assert here.nodes[0].id != other_host.nodes[0].id


def test_the_id_does_not_depend_on_the_line() -> None:
    """The same class at two lines is the same id, not two."""
    one = _detect(HANDLER_SOURCE)
    two = _detect(
        "from langchain_core.callbacks import BaseCallbackHandler\n"
        "\n\n\n\nclass AuditHook(BaseCallbackHandler):\n"
        "    pass\n"
    )
    assert one.nodes[0].id == two.nodes[0].id


def test_two_classes_of_one_name_in_one_file_share_one_id() -> None:
    """The id hashes the name, not the line: an accepted false negative.

    Two definitions of one class name in one file — a shadowed
    redefinition, or a nested class named like a module-level one — are
    two nodes with one id, so the graph keeps one row. `line_start`
    distinguishes them for a reader.
    """
    source = (
        IMPORT
        + "\n\nclass MyHandler(BaseCallbackHandler):\n"
        "    pass\n"
        "\n\ndef build():\n"
        "    class MyHandler(BaseCallbackHandler):\n"
        "        pass\n"
        "    return MyHandler\n"
    )
    result = _detect(source)
    assert _names(result) == ["MyHandler", "MyHandler"]
    assert len({node.id for node in result.nodes}) == 1
    assert [n.attributes["line_start"] for n in result.nodes] == [4, 9]


# ============================================================
# Order
# ============================================================

def test_the_nodes_are_in_source_order() -> None:
    """A breadth-first walk, one source-ordered result.

    `ast.walk` is breadth-first, so it reaches the module's second class
    before a class nested in a function body above it; the detector
    sorts by `(lineno, col_offset)`.
    """
    source = (
        IMPORT
        + "\n\nclass First(BaseCallbackHandler):\n"
        "    pass\n"
        "\n\ndef build():\n"
        "    class Nested(BaseCallbackHandler):\n"
        "        pass\n"
        "\n\nclass Second(BaseCallbackHandler):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert _names(result) == ["First", "Nested", "Second"]


# ============================================================
# The boundaries the module docstring states
# ============================================================

def test_an_aliased_import_is_not_matched() -> None:
    """`BaseCallbackHandler as BCH` is a false negative, pinned.

    The detector matches the base's name; an ADR-0011 per-file detector
    does not carry an alias table. The import is still the fast path:
    the file mentions the base, and the shape then declines to match.
    """
    source = (
        "from langchain_core.callbacks import BaseCallbackHandler as BCH\n"
        "\n\nclass MyHandler(BCH):\n"
        "    pass\n"
    )
    result = _detect(source)
    assert result.nodes == []


def test_a_string_base_is_not_a_subclass() -> None:
    """`class MyHandler("BaseCallbackHandler")` — a forward reference."""
    source = (
        IMPORT
        + '\n\nclass MyHandler("BaseCallbackHandler"):\n'
        "    pass\n"
    )
    result = _detect(source)
    assert result.nodes == []


# ============================================================
# R1, R2 — registration
# ============================================================

def test_all_names_includes_langchain_callback() -> None:
    assert "langchain_callback" in all_names()
    assert all_names() == sorted(all_names())


def test_resolve_returns_an_instance() -> None:
    resolved = resolve(["langchain_callback"])
    assert len(resolved) == 1
    assert isinstance(resolved[0], LangChainCallbackDetector)
    assert resolved[0].name == "langchain_callback"


def test_the_registry_class_is_the_detector() -> None:
    from aicorellator.parsing.framework_detectors.registry import (
        KNOWN_DETECTORS,
    )
    assert KNOWN_DETECTORS["langchain_callback"] is LangChainCallbackDetector
