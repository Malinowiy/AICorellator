"""Unit tests for the NeMo Guardrails detector (B.4-gap-3).

ADR-0024 §3 decides the SDK detectors; this is the second. The shape is
the SDK's own **rails construction** — `LLMRails(config)` — and the node
it produces is a `guardrail` with
`observed_by=["ast", "framework:nemo_guardrails"]` (ADR-0024 §1: the
detector's provenance, distinguishable from the capability's `["ast"]`).

S1–S7 are the shapes, P1/P2 the provenance, E1 the empty edge list, R1/R2
the registry, and the rest are the decisions the module docstring states
and the brief left open: the fast path, the id collision of two unnamed
constructions, the qualified and keyword forms, the `@action` decorator,
and the alias.

The merge (M1) and B.4a's behaviour on a detector node (X1) need a
`NormalizeContext` and the file-backed `project` fixture, which lives in
`tests/test_normalizer_stages/conftest.py`; they are in
`tests/test_normalizer_stages/test_nemo_guardrails_detector.py`.
"""

from __future__ import annotations

import ast

from aicorellator.parsing.framework_detectors.nemo_guardrails import (
    NeMoGuardrailsDetector,
)
from aicorellator.parsing.framework_detectors.registry import (
    all_names,
    resolve,
)

IMPORT = "from nemoguardrails import LLMRails\n"
CONFIG_IMPORT = "from nemoguardrails.rails import RailsConfig\n"

# A rails built where the SDK's own example builds one: inside the class
# that owns it, bound to an attribute.
METHOD_SOURCE = """\
from nemoguardrails import LLMRails


class Bot:
    def setup(self, config):
        self.rails = LLMRails(config)
"""


def _detect(code: str):
    """One detector run over `code`, as the runner calls it."""
    tree = ast.parse(code)
    return NeMoGuardrailsDetector().detect("test.py", tree, code)


def _names(result) -> list[str]:
    return [node.name for node in result.nodes]


# ============================================================
# The fast path
# ============================================================

def test_no_nemoguardrails_import_returns_empty() -> None:
    """No import, no detection — even when the shape is present.

    A project that calls `LLMRails(config)` without importing
    `nemoguardrails` is calling its own name, not the SDK's.
    """
    result = _detect("rails = LLMRails(config)\n")
    assert result.nodes == []
    assert result.edges == []


def test_an_unrelated_import_returns_empty() -> None:
    result = _detect("import json\n\nrails = LLMRails(config)\n")
    assert result.nodes == []


def test_each_import_form_of_the_sdk_is_the_fast_path() -> None:
    """`import nemoguardrails` / `from nemoguardrails...` / `.rails`.

    `_has_import_prefix` is the base helper every detector uses; the
    SDK's distribution imports as `nemoguardrails` and its rails class
    lives under `nemoguardrails.rails`.
    """
    for header in (
        "import nemoguardrails",
        "import nemoguardrails.rails",
        "from nemoguardrails import LLMRails",
        "from nemoguardrails.rails import LLMRails",
        "from nemoguardrails.rails.llm import LLMRails",
    ):
        result = _detect(f"{header}\n\nrails = LLMRails(config)\n")
        assert len(result.nodes) == 1, header


# ============================================================
# S1 — LLMRails(config)
# ============================================================

def test_llmrails_assigned_names_the_node_after_the_variable() -> None:
    result = _detect(IMPORT + "\nrails = LLMRails(config)\n")
    assert _names(result) == ["rails"]
    node = result.nodes[0]
    assert node.kind == "guardrail"
    assert node.attributes["framework"] == "nemo_guardrails"
    assert node.attributes["source_file"] == "test.py"
    assert node.attributes["line_start"] == 3


def test_llmrails_unassigned_is_named_after_the_class() -> None:
    """A bare-expression construction has no name of its own."""
    result = _detect(IMPORT + "\nLLMRails(config)\n")
    assert _names(result) == ["LLMRails"]
    assert result.nodes[0].kind == "guardrail"


def test_llmrails_inside_a_method_body_counts() -> None:
    """`ast.walk` reaches the whole tree; a method body is a call site.

    The construction is the rails the class wires up, and `source_file`
    plus `line_start` are the same treatment a module-level construction
    gets.
    """
    result = _detect(METHOD_SOURCE)
    assert _names(result) == ["rails"]
    line = METHOD_SOURCE.splitlines().index(
        "        self.rails = LLMRails(config)"
    ) + 1
    assert result.nodes[0].attributes["line_start"] == line


def test_a_self_attribute_target_names_the_node() -> None:
    """`self.rails = LLMRails(...)` names the node after the attribute."""
    result = _detect(METHOD_SOURCE)
    assert _names(result) == ["rails"]


def test_an_annotated_assignment_names_the_node() -> None:
    result = _detect(IMPORT + "\nrails: LLMRails = LLMRails(config)\n")
    assert _names(result) == ["rails"]


def test_a_multiple_target_assignment_falls_back_to_the_class() -> None:
    """`a = b = LLMRails(...)` has two targets and no single name."""
    result = _detect(IMPORT + "\na = b = LLMRails(config)\n")
    assert _names(result) == ["LLMRails"]


def test_a_tuple_target_falls_back_to_the_class() -> None:
    result = _detect(IMPORT + "\nrails, other = LLMRails(config), None\n")
    assert _names(result) == ["LLMRails"]


def test_a_subscript_target_falls_back_to_the_class() -> None:
    """A list element is not a name."""
    result = _detect(IMPORT + "\nrails[0] = LLMRails(config)\n")
    assert _names(result) == ["LLMRails"]


def test_a_construction_in_a_list_is_named_after_the_class() -> None:
    result = _detect(IMPORT + "\nrails = [LLMRails(config)]\n")
    assert _names(result) == ["LLMRails"]


def test_a_construction_passed_to_a_call_is_named_after_the_class() -> None:
    result = _detect(IMPORT + "\nsetup(LLMRails(config))\n")
    assert _names(result) == ["LLMRails"]


def test_a_returned_construction_is_named_after_the_class() -> None:
    result = _detect(IMPORT + """

def build(config):
    return LLMRails(config)
""")
    assert _names(result) == ["LLMRails"]


# ============================================================
# S2 — the keyword form, and the arguments in general
# ============================================================

def test_the_keyword_form_is_a_construction() -> None:
    """`LLMRails(config=config)` — the SDK's own parameter name."""
    result = _detect(IMPORT + "\nrails = LLMRails(config=config)\n")
    assert _names(result) == ["rails"]
    assert result.nodes[0].kind == "guardrail"


def test_the_arguments_are_not_read() -> None:
    """The shape is the call, not its argument list.

    An arity the SDK would reject at runtime is not the detector's call
    to make (module docstring): `LLMRails()` and
    `LLMRails(config, extra=1)` are the same construction to a reader of
    the AST.
    """
    for call in ("LLMRails()", 'LLMRails(config, extra=1)',
                 'LLMRails("config.yml")'):
        result = _detect(f"{IMPORT}\nrails = {call}\n")
        assert _names(result) == ["rails"], call


# ============================================================
# S3 — the qualified form
# ============================================================

def test_the_qualified_form_is_a_construction() -> None:
    """`nemoguardrails.rails.LLMRails(config)` — the rule is the name."""
    source = (
        "import nemoguardrails\n"
        "\n"
        "rails = nemoguardrails.rails.LLMRails(config)\n"
    )
    result = _detect(source)
    assert _names(result) == ["rails"]
    assert result.nodes[0].kind == "guardrail"


def test_a_module_alias_prefix_is_the_same_construction() -> None:
    """`import nemoguardrails as ng` then `ng.rails.LLMRails(...)`.

    The last dotted segment is `LLMRails` either way; the fast path
    matches the import, and the module alias is not needed to read the
    call.
    """
    source = (
        "import nemoguardrails as ng\n"
        "\n"
        "rails = ng.rails.LLMRails(config)\n"
    )
    result = _detect(source)
    assert _names(result) == ["rails"]


# ============================================================
# S4, S5 — RailsConfig is a config loader, not a guardrail
# ============================================================

def test_railsconfig_is_not_a_node() -> None:
    """The config loader is not enforcement (module docstring).

    `RailsConfig(...)` builds the configuration value the rails is
    constructed from; the guardrail is the `LLMRails` instance.
    """
    result = _detect(CONFIG_IMPORT + "\nconfig = RailsConfig()\n")
    assert result.nodes == []


def test_railsconfig_from_path_is_not_a_node() -> None:
    result = _detect(
        CONFIG_IMPORT + '\nconfig = RailsConfig.from_path("config")\n'
    )
    assert result.nodes == []


def test_railsconfig_from_content_is_not_a_node() -> None:
    result = _detect(
        CONFIG_IMPORT + '\nconfig = RailsConfig.from_content(yaml="...")\n'
    )
    assert result.nodes == []


def test_a_railsconfig_argument_does_not_add_a_node() -> None:
    """The SDK's own three-line setup: exactly one node, the rails."""
    source = (
        "from nemoguardrails import LLMRails, RailsConfig\n"
        "\n"
        'config = RailsConfig.from_path("config")\n'
        "rails = LLMRails(config)\n"
    )
    result = _detect(source)
    assert _names(result) == ["rails"]
    assert result.nodes[0].attributes["line_start"] == 4


# ============================================================
# S6 — the instance methods are not constructions
# ============================================================

def test_the_instance_methods_are_not_constructions() -> None:
    """`rails.generate(...)` is an attribute call on an instance.

    The target's last segment is `generate`, not `LLMRails`: one node
    from the construction, none from the use. The whole SDK example is
    here — config, rails, generate — and the rails is the only node.
    """
    source = """\
from nemoguardrails import LLMRails, RailsConfig

config = RailsConfig.from_path("config")
rails = LLMRails(config)
result = rails.generate(messages=[{"role": "user", "content": "hi"}])
async_result = rails.generate_async(messages=[])
"""
    result = _detect(source)
    assert _names(result) == ["rails"]


def test_a_method_on_another_object_is_not_a_construction() -> None:
    """`bot.LLMRails`-free call sites are not this SDK's class."""
    result = _detect(
        IMPORT + "\nresult = client.generate_async(messages=[])\n"
    )
    assert result.nodes == []


# ============================================================
# S7 — @action is custom logic, not the SDK's guardrail class
# ============================================================

def test_the_bare_action_decorator_is_not_a_node() -> None:
    """`@action` registers a project function in the rails runtime.

    What it does is the function's body, not the decorator's shape, and
    ADR-0024 §3 names the class construction.
    """
    source = IMPORT + """

@action
def check_input(text):
    return text
"""
    result = _detect(source)
    assert result.nodes == []


def test_the_qualified_action_decorator_is_not_a_node() -> None:
    source = (
        "from nemoguardrails import LLMRails\n"
        "from nemoguardrails.actions import action\n"
        "\n"
        "@action\n"
        "def check_input(text):\n"
        "    return text\n"
        "\n"
        '@nemoguardrails.action(name="check")\n'
        "def check_output(text):\n"
        "    return text\n"
    )
    result = _detect(source)
    assert result.nodes == []


def test_an_action_that_builds_rails_yields_only_the_rails() -> None:
    """The action is not a node; the construction inside it is."""
    source = """\
from nemoguardrails import LLMRails, RailsConfig
from nemoguardrails.actions import action


@action
def check_input(text):
    config = RailsConfig.from_path("config")
    return LLMRails(config)
"""
    result = _detect(source)
    assert _names(result) == ["LLMRails"]
    assert result.nodes[0].kind == "guardrail"



# ============================================================
# P1, P2 — provenance
# ============================================================

def test_the_node_names_the_detector_and_the_ast() -> None:
    """ADR-0024 §1: a detector-derived guardrail says so.

    `["ast", "framework:nemo_guardrails"]` is what keeps the three
    producers — capability, detector, LLM classifier — tellable apart
    in the graph.
    """
    result = _detect(IMPORT + "\nrails = LLMRails(config)\n")
    assert result.nodes[0].observed_by == [
        "ast", "framework:nemo_guardrails",
    ]


def test_the_framework_attribute_is_the_registry_name() -> None:
    result = _detect(IMPORT + "\nrails = LLMRails(config)\n")
    assert result.nodes[0].attributes["framework"] == "nemo_guardrails"


def test_the_provenance_is_a_fresh_list() -> None:
    """Two nodes must not share one mutable `observed_by`."""
    result = _detect(IMPORT + "\nfirst = LLMRails(c)\nsecond = LLMRails(c)\n")
    first, second = result.nodes
    assert first.observed_by is not second.observed_by
    first.observed_by.append("x")
    assert second.observed_by == ["ast", "framework:nemo_guardrails"]


def test_the_detection_result_names_the_framework() -> None:
    result = _detect(IMPORT + "\nrails = LLMRails(config)\n")
    assert result.framework == "nemo_guardrails"


def test_supported_versions_is_the_wildcard() -> None:
    """No version is inferable from an import line (ADR-0011)."""
    detector = NeMoGuardrailsDetector()
    assert detector.supported_versions == "*"
    assert detector.name == "nemo_guardrails"


# ============================================================
# E1 — no edges
# ============================================================

def test_the_detector_returns_no_edges() -> None:
    """Node-only. An `LLMRails` wraps a generation call at runtime.

    B.4a's `guardrail_edges` decides what a `guards` edge needs, and it
    runs on this node like on any other `guardrail` node.
    """
    result = _detect(IMPORT + "\nrails = LLMRails(config)\n")
    assert result.nodes != []
    assert result.edges == []


# ============================================================
# The id — two unnamed constructions collide
# ============================================================

def test_two_named_constructions_are_two_nodes_with_two_ids() -> None:
    result = _detect(IMPORT + "\nfirst = LLMRails(c)\nsecond = LLMRails(c)\n")
    assert _names(result) == ["first", "second"]
    assert len({node.id for node in result.nodes}) == 2


def test_two_unnamed_constructions_share_one_id() -> None:
    """The id hashes the name, not the line: an accepted false negative.

    The detector emits one node per call site; the id is
    `framework + kind + file + name + host` (the existing detectors'
    rule), so two `LLMRails(config)` calls with no binding produce the
    same id and the graph keeps one row. `line_start` distinguishes them
    for a reader.
    """
    result = _detect(IMPORT + "\nLLMRails(c)\nLLMRails(c)\n")
    assert _names(result) == ["LLMRails", "LLMRails"]
    assert len({node.id for node in result.nodes}) == 1
    assert [n.attributes["line_start"] for n in result.nodes] == [3, 4]


def test_the_id_does_not_depend_on_the_line() -> None:
    """The same construction at two lines is the same id, not two."""
    one = _detect(IMPORT + "\nrails = LLMRails(config)\n")
    two = _detect(IMPORT + "\n\n\n\nrails = LLMRails(config)\n")
    assert one.nodes[0].id == two.nodes[0].id


def test_the_id_depends_on_the_file() -> None:
    """Per-file and per-host, like the other detectors (ADR-0011)."""
    detector = NeMoGuardrailsDetector()
    tree = ast.parse(IMPORT + "\nrails = LLMRails(config)\n")
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
first = LLMRails(config)
LLMRails(config)
second = LLMRails(config)
""")
    assert _names(result) == ["first", "LLMRails", "second"]


# ============================================================
# The boundaries the module docstring states
# ============================================================

def test_an_aliased_import_is_not_matched() -> None:
    """`LLMRails as LR` is a false negative, documented and pinned.

    The detector matches the class name; an ADR-0011 per-file detector
    does not carry an alias table. The import is still the fast path.
    """
    source = (
        "from nemoguardrails import LLMRails as LR\n"
        "\n"
        "rails = LR(config)\n"
    )
    result = _detect(source)
    assert result.nodes == []


def test_a_class_level_factory_is_not_a_construction() -> None:
    """`LLMRails.from_config(...)` — a false negative, documented.

    `LLMRails` is a plain constructor; the SDK's factories live on
    `RailsConfig`, which is not a node. The rule is the target's last
    dotted segment, so a factory the SDK grows on `LLMRails` itself
    would need a one-line widening.
    """
    result = _detect(IMPORT + '\nrails = LLMRails.from_config("config")\n')
    assert result.nodes == []


def test_a_similarly_named_class_is_not_a_construction() -> None:
    result = _detect(
        IMPORT + "\nrails = LLMRailsConfig(config)\nmy_rails = Rails(c)\n"
    )
    assert result.nodes == []


def test_a_call_returning_a_callable_is_not_a_construction() -> None:
    """A subscript or a call target is not a name: no node."""
    result = _detect(
        IMPORT + "\nrails = factories[0](config)\nother = make()(config)\n"
    )
    assert result.nodes == []


# ============================================================
# R1, R2 — registration
# ============================================================

def test_all_names_includes_nemo_guardrails() -> None:
    assert "nemo_guardrails" in all_names()
    assert all_names() == sorted(all_names())


def test_resolve_returns_an_instance() -> None:
    resolved = resolve(["nemo_guardrails"])
    assert len(resolved) == 1
    assert isinstance(resolved[0], NeMoGuardrailsDetector)
    assert resolved[0].name == "nemo_guardrails"


def test_the_registry_class_is_the_detector() -> None:
    from aicorellator.parsing.framework_detectors.registry import (
        KNOWN_DETECTORS,
    )
    assert KNOWN_DETECTORS["nemo_guardrails"] is NeMoGuardrailsDetector
