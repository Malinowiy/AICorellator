"""Unit tests for the Guardrails AI detector (B.4-gap-2).

ADR-0024 §3 decides the SDK detectors; this is the first. The shape is
the SDK's own **class-level construction** — `Guard(...)` and
`Guard.<factory>(...)` — and the node it produces is a `guardrail` with
`observed_by=["ast", "framework:guardrails_ai"]` (ADR-0024 §1: the
detector's provenance, distinguishable from the capability's `["ast"]`).

S1–S5 are the shapes, P1/P2 the provenance, E1 the empty edge list, R1/R2
the registry, and the rest are the decisions the module docstring states
and the brief left open: the fast path, the id collision of two unnamed
constructions, the class-level/dotted forms, the decorator, and the
fallback name.

The merge (M1) and B.4a's behaviour on a detector node (X1) need a
`NormalizeContext` and the file-backed `project` fixture, which lives in
`tests/test_normalizer_stages/conftest.py`; they are in
`tests/test_normalizer_stages/test_guardrails_ai_detector.py`.
"""

from __future__ import annotations

import ast

from aicorellator.parsing.framework_detectors.guardrails_ai import (
    GuardrailsAIDetector,
)
from aicorellator.parsing.framework_detectors.registry import (
    all_names,
    resolve,
)

IMPORT = "from guardrails import Guard\n"


def _detect(code: str):
    """One detector run over `code`, as the runner calls it."""
    tree = ast.parse(code)
    return GuardrailsAIDetector().detect("test.py", tree, code)


def _names(result) -> list[str]:
    return [node.name for node in result.nodes]


# ============================================================
# The fast path
# ============================================================

def test_no_guardrails_import_returns_empty() -> None:
    """No import, no detection — even when the shape is present.

    A project that calls `Guard(...)` without importing `guardrails` is
    calling its own name, not the SDK's.
    """
    result = _detect("guard = Guard()\n")
    assert result.nodes == []
    assert result.edges == []


def test_an_unrelated_import_returns_empty() -> None:
    result = _detect("import json\n\nguard = Guard()\n")
    assert result.nodes == []


def test_each_import_form_of_the_sdk_is_the_fast_path() -> None:
    """`import guardrails` / `from guardrails import ...` / `.hub`.

    `_has_import_prefix` is the base helper every detector uses; the
    SDK's distribution imports as `guardrails` and its validators live
    under `guardrails.hub`.
    """
    for header in (
        "import guardrails",
        "import guardrails.hub",
        "from guardrails import Guard",
        "from guardrails.hub import Guard",
    ):
        result = _detect(f"{header}\n\nguard = Guard()\n")
        assert len(result.nodes) == 1, header


# ============================================================
# S1 — Guard(...)
# ============================================================

def test_guard_assigned_names_the_node_after_the_variable() -> None:
    result = _detect(IMPORT + "\nguard = Guard()\n")
    assert _names(result) == ["guard"]
    node = result.nodes[0]
    assert node.kind == "guardrail"
    assert node.attributes["framework"] == "guardrails_ai"
    assert node.attributes["source_file"] == "test.py"
    assert node.attributes["line_start"] == 3


def test_guard_unassigned_is_named_after_the_class() -> None:
    """A bare-expression construction has no name of its own."""
    result = _detect(IMPORT + "\nGuard()\n")
    assert _names(result) == ["Guard"]


def test_a_guard_inside_a_call_is_named_after_the_class() -> None:
    """`setup(Guard())` — the call is not an assignment value."""
    result = _detect(IMPORT + "\nsetup(Guard())\n")
    assert _names(result) == ["Guard"]


def test_a_guard_inside_a_list_is_named_after_the_class() -> None:
    result = _detect(IMPORT + "\nguards = [Guard(), Guard()]\n")
    assert _names(result) == ["Guard", "Guard"]


def test_a_guard_inside_a_method_body_counts() -> None:
    """`ast.walk` reaches the whole tree; the line is the call's own."""
    result = _detect(IMPORT + """
class Service:
    def build(self, text):
        guard = Guard()
        return guard
""")
    assert _names(result) == ["guard"]
    assert result.nodes[0].attributes["line_start"] == 5


def test_a_self_attribute_target_names_the_node() -> None:
    result = _detect(IMPORT + """
class Service:
    def __init__(self):
        self.guard = Guard()
""")
    assert _names(result) == ["guard"]


# ============================================================
# S2–S4 — Guard.<factory>(...)
# ============================================================

def test_from_pydantic_is_a_construction() -> None:
    result = _detect(IMPORT + """
from pydantic import BaseModel


class Invoice(BaseModel):
    total: float


guard = Guard.from_pydantic(Invoice)
""")
    assert _names(result) == ["guard"]
    assert result.nodes[0].attributes["framework"] == "guardrails_ai"


def test_for_string_is_a_construction() -> None:
    result = _detect("""\
from guardrails import Guard

guard = Guard.for_string(validators=[], description="x")
""")
    assert _names(result) == ["guard"]


def test_for_rail_is_a_construction() -> None:
    result = _detect(IMPORT + "\nguard = Guard.for_rail(\"pii.rail\")\n")
    assert _names(result) == ["guard"]


def test_any_class_level_factory_is_a_construction() -> None:
    """The set of factories is the SDK's, not the detector's.

    ADR-0016 decision 7 forbids a table that grants a kind from a name;
    the detector reads the class (`Guard.<attr>(...)`), so a factory the
    SDK adds later is covered without a line here.
    """
    result = _detect(IMPORT + "\nguard = Guard.from_yaml(\"config.yaml\")\n")
    assert _names(result) == ["guard"]


def test_a_dotted_construction_is_a_construction() -> None:
    """`import guardrails` + `guardrails.Guard.for_string(...)`."""
    result = _detect(
        "import guardrails\n"
        "\nguard = guardrails.Guard.for_string(validators=[])\n"
    )
    assert _names(result) == ["guard"]


def test_an_unassigned_factory_call_is_named_after_the_class() -> None:
    result = _detect(IMPORT + "\nGuard.for_rail(\"pii.rail\")\n")
    assert _names(result) == ["Guard"]


# ============================================================
# S5 — an instance method is not a construction
# ============================================================

def test_an_instance_method_call_is_not_a_construction() -> None:
    """`guard.parse` / `guard.validate` use a guard, they do not build one.

    One construction, one node — the two calls add nothing. The base of
    the attribute is `guard` (the value), not `Guard` (the class).
    """
    result = _detect(IMPORT + """
guard = Guard()

guard.parse("text")
guard.validate("text")
guard("text")
""")
    assert _names(result) == ["guard"]


def test_a_construction_that_is_used_is_still_one_node() -> None:
    result = _detect(IMPORT + """
guard = Guard.for_pydantic(Invoice)
result = guard.parse(text)
""")
    assert _names(result) == ["guard"]


# ============================================================
# The assignment forms
# ============================================================

def test_an_annotated_assignment_names_the_node() -> None:
    result = _detect(
        IMPORT + "\nguard: Guard = Guard.for_rail(\"pii.rail\")\n"
    )
    assert _names(result) == ["guard"]


def test_a_multiple_target_assignment_falls_back_to_the_class() -> None:
    """`first = second = Guard()` — two names, one value: no name wins."""
    result = _detect(IMPORT + "\nfirst = second = Guard()\n")
    assert _names(result) == ["Guard"]


def test_a_tuple_target_falls_back_to_the_class() -> None:
    """`left, right = Guard()` — one target, and it is a tuple."""
    result = _detect(IMPORT + "\nleft, right = Guard()\n")
    assert _names(result) == ["Guard"]


def test_a_tuple_value_goes_through_the_call_sites() -> None:
    """`left, right = Guard(), Guard(...)` — the value is not a call.

    Neither construction is an assignment *value* (`node.value` is the
    tuple), so both are found the second way: as call sites named after
    the class.
    """
    result = _detect(
        IMPORT + "\nleft, right = Guard(), Guard.for_rail(\"x\")\n"
    )
    assert _names(result) == ["Guard", "Guard"]


def test_a_subscript_target_falls_back_to_the_class() -> None:
    result = _detect(IMPORT + "\nguards[0] = Guard()\n")
    assert _names(result) == ["Guard"]


# ============================================================
# Names that are not the SDK's class
# ============================================================

def test_a_similarly_named_class_is_not_a_construction() -> None:
    """`MyGuard(...)` is the project's class, not the SDK's."""
    result = _detect(IMPORT + "\nguard = MyGuard()\n")
    assert result.nodes == []


def test_a_lowercase_base_is_not_a_construction() -> None:
    """`guard.Guard(...)` — an attribute of an instance, not the class."""
    result = _detect(IMPORT + """
guard = Guard()

other = guard.Guard()
""")
    assert _names(result) == ["guard"]


# ============================================================
# The decorator
# ============================================================

def test_the_bare_decorator_is_not_a_construction() -> None:
    """ADR-0024 §3 names `@guard`; the node here is the construction.

    `@guard` is a `Name`, not a call, so there is nothing to build a
    node from — a separate shape, not detected in B.4-gap-2.
    """
    result = _detect(IMPORT + """
from guardrails import guard


@guard
def answer(text):
    return text
""")
    assert result.nodes == []


def test_a_class_level_call_as_a_decorator_is_a_call_site() -> None:
    """`@Guard.for_string(...)` applies a guard this detector cannot name.

    It is a `Guard` class-level call in the tree, so the rule that
    "a class-level call is a construction" emits a node — named after
    the class, because a decorator binds nothing.
    """
    result = _detect(IMPORT + """

@Guard.for_string(validators=[], description="x")
def answer(text):
    return text
""")
    assert _names(result) == ["Guard"]




# ============================================================
# P1, P2 — provenance
# ============================================================

def test_the_node_names_the_detector_and_the_ast() -> None:
    """ADR-0024 §1: a detector-derived guardrail says who saw it.

    `["ast", "framework:guardrails_ai"]`, which is what distinguishes it
    from a capability-derived `guardrail` (`["ast"]`) and from the LLM
    classifier's (`["ast", "static_llm:<model>"]`).
    """
    result = _detect(IMPORT + "\nguard = Guard()\n")
    assert result.nodes[0].observed_by == [
        "ast", "framework:guardrails_ai",
    ]


def test_the_framework_attribute_is_the_registry_name() -> None:
    result = _detect(IMPORT + "\nguard = Guard()\n")
    assert result.nodes[0].attributes["framework"] == "guardrails_ai"


def test_the_provenance_is_a_fresh_list() -> None:
    """Two nodes must not share one mutable list."""
    result = _detect(IMPORT + "\ng1 = Guard()\ng2 = Guard.for_rail(\"x\")\n")
    assert result.nodes[0].observed_by is not result.nodes[1].observed_by


def test_the_detection_result_names_the_framework() -> None:
    result = _detect(IMPORT + "\nguard = Guard()\n")
    assert result.framework == "guardrails_ai"
    assert result.detected_version is None


def test_supported_versions_is_the_wildcard() -> None:
    """No version is inferable from an import line (ADR-0011)."""
    assert GuardrailsAIDetector.supported_versions == "*"


# ============================================================
# E1 — no edges
# ============================================================

def test_the_detector_returns_no_edges() -> None:
    """Node-only. A `Guard` wraps an LLM call; the AST links nothing.

    B.4a's `guardrail_edges` decides what a `guards` edge needs, and it
    runs on this node like on any other `guardrail` node.
    """
    result = _detect(IMPORT + "\nguard = Guard()\n\nguard.parse(\"x\")\n")
    assert result.nodes != []
    assert result.edges == []


# ============================================================
# The id — two unnamed constructions collide
# ============================================================

def test_two_named_constructions_are_two_nodes_with_two_ids() -> None:
    result = _detect(IMPORT + "\ng1 = Guard()\ng2 = Guard.for_rail(\"x\")\n")
    assert _names(result) == ["g1", "g2"]
    assert len({node.id for node in result.nodes}) == 2


def test_two_unnamed_constructions_share_one_id() -> None:
    """The id hashes the name, not the line: an accepted false negative.

    The detector emits one node per call site; the id is
    `framework + kind + file + name + host` (the existing detectors'
    rule), so two `Guard()` calls with no binding produce the same id and
    the graph keeps one row. `line_start` distinguishes them for a reader.
    """
    result = _detect(IMPORT + "\nGuard()\nGuard()\n")
    assert _names(result) == ["Guard", "Guard"]
    assert len({node.id for node in result.nodes}) == 1
    assert [n.attributes["line_start"] for n in result.nodes] == [3, 4]


def test_the_id_does_not_depend_on_the_line() -> None:
    """The same construction at two lines is the same id, not two."""
    one = _detect(IMPORT + "\nguard = Guard()\n")
    two = _detect(IMPORT + "\n\n\n\nguard = Guard()\n")
    assert one.nodes[0].id == two.nodes[0].id


def test_the_id_depends_on_the_file() -> None:
    """Per-file and per-host, like the other detectors (ADR-0011)."""
    detector = GuardrailsAIDetector()
    tree = ast.parse(IMPORT + "\nguard = Guard()\n")
    here = detector.detect("a.py", tree, "")
    there = detector.detect("b.py", tree, "")
    other_host = detector.detect("a.py", tree, "", host_id="other")
    assert here.nodes[0].id != there.nodes[0].id
    assert here.nodes[0].id != other_host.nodes[0].id


# ============================================================
# Order
# ============================================================

def test_the_nodes_are_in_source_order() -> None:
    """Two passes over the tree, one ordered result."""
    result = _detect(IMPORT + """
first = Guard()
Guard.for_rail("x")
second = Guard.for_pydantic(Invoice)
""")
    assert _names(result) == ["first", "Guard", "second"]


# ============================================================
# R1, R2 — registration
# ============================================================

def test_all_names_includes_guardrails_ai() -> None:
    assert "guardrails_ai" in all_names()
    assert all_names() == sorted(all_names())


def test_resolve_returns_an_instance() -> None:
    resolved = resolve(["guardrails_ai"])
    assert len(resolved) == 1
    assert isinstance(resolved[0], GuardrailsAIDetector)
    assert resolved[0].name == "guardrails_ai"


def test_the_registry_class_is_the_detector() -> None:
    from aicorellator.parsing.framework_detectors.registry import (
        KNOWN_DETECTORS,
    )
    assert KNOWN_DETECTORS["guardrails_ai"] is GuardrailsAIDetector

