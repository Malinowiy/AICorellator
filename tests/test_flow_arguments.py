"""Unit tests for `flow.arguments` — the shared argument primitives.

`_classify_argument` is the one place the name-based rules live
(ADR-0001): a name is a parameter, a local variable, or neither; a
constant is a literal; an attribute, an f-string or a collection is
looked one step into. The opt-in rule of 1.2a.3j
(`parameter_attributes=True`) is tested here, because it is a property
of the primitive itself — the sink analyzer's file tests the value that
its call sites get out of it.

These tests are about *what an argument is*, never about whether data
flows: no `FlowDecision` appears in this file.
"""

from __future__ import annotations

import ast
import textwrap

from aicorellator.parsing.flow.arguments import (
    FunctionNode,
    _classify_argument,
    _is_literal,
    _local_variable_names,
    _parameter_names,
    _self_attribute_names,
)


# ============================================================
# Helpers
# ============================================================

def _func(source: str) -> FunctionNode:
    """Parse `source` and return its first function definition."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise AssertionError("no function found in source")


def _sink_call(func: FunctionNode, name: str) -> ast.Call:
    """Find the call to `name` (matched on the last dotted segment)."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == name:
            return node
        if isinstance(node.func, ast.Attribute) and node.func.attr == name:
            return node
    raise AssertionError(f"no call to {name} found")


# ============================================================
# Parameter attributes (1.2a.3j)
# ============================================================
# `file_data.filename` is an attribute of a *parameter*, so it is
# parameter-derived by construction: the base name is the evidence, and
# depth does not weaken it (`session_context.current_user.id` is the
# same shape one attribute further out). A base that is a local variable
# of the body counts the same way, because a local is bound in this body
# and `_local_variable_names` does not track values (ADR-0001). A base
# that is neither — a module-level import, a global — stays `unknown`.
#
# Like the `self.q` rule (1.2a.3g) it is opt-in: `analyze_sink_body`
# classifies a body against its own parameters and opts in, while
# `analyze_has_access_to` does not, so the `data_flow_confirmed` values
# of edges are untouched. `_classify_argument` is tested directly for
# the exact value, `analyze_sink_body` for the node value it yields.

def _classify(source: str, sink: str = "os.remove") -> str:
    """Classify the single argument of the `sink` call, opting in."""
    func = _func(source)
    call = _sink_call(func, sink.split(".")[-1])
    arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
    assert len(arguments) == 1
    return _classify_argument(
        arguments[0],
        _parameter_names(func),
        _local_variable_names(func),
        parameter_attributes=True,
    )


def test_parameter_attribute_is_data() -> None:
    """`file_data.filename` — FinBot `create_vendor_file`."""
    src = """
    def write_file(file_data):
        os.remove(file_data.filename)
    """
    assert _classify(src) == "parameter_match"


def test_nested_parameter_attribute_is_data() -> None:
    """`session_context.current_user.id` — the chain is followed.

    Any depth is accepted: every step is an attribute access on the same
    parameter, so the base name carries the evidence the whole way.
    """
    src = """
    def write_file(session_context):
        os.remove(session_context.current_user.id)
    """
    assert _classify(src) == "parameter_match"


def test_parameter_attribute_in_an_fstring_is_data() -> None:
    """The f-string branch recurses into the attribute case (1.2a.3j)."""
    src = """
    def write_file(file_data):
        os.remove(f"{file_data.filename}/x")
    """
    assert _classify(src) == "parameter_match"


def test_attribute_of_a_module_level_name_is_unknown() -> None:
    """`obj.attr` where `obj` is a global: no parameter evidence."""
    src = """
    import obj

    def write_file(file_data):
        os.remove(obj.attr)
    """
    assert _classify(src) == "unknown"


def test_attribute_of_a_local_from_a_parameter_is_data() -> None:
    """`config = load(param)` then `config.path` is a parameter match.

    The local is bound in this body, and the analyzer follows names, not
    values (ADR-0001) — the same reason `f"{base}"` is classified like
    `base` itself.
    """
    src = """
    def write_file(param):
        config = load(param)
        os.remove(config.path)
    """
    assert _classify(src) == "parameter_match"


def test_attribute_of_a_literal_local_is_data_too() -> None:
    """`config = {"path": "/tmp"}` then `config.path` is also a match.

    Deliberate over-approximation, pinned here because it is the price of
    the rule above: a local is locally-bound data whatever its value, and
    `_local_variable_names` is value-blind (ADR-0001). The narrower
    alternative — resolving the assignment — is dataflow, not naming.
    """
    src = """
    def write_file(param):
        config = {"path": "/tmp"}
        os.remove(config.path)
    """
    assert _classify(src) == "parameter_match"


def test_attribute_of_a_call_result_is_unknown() -> None:
    """`fetch().path` has no base name to judge → `unknown`."""
    src = """
    def write_file():
        os.remove(fetch().path)
    """
    assert _classify(src) == "unknown"


def test_attribute_of_a_self_attribute_is_unknown() -> None:
    """`self.q.name` is out of scope: the base is `self`, not `q`.

    `self.q` itself is a match (1.2a.3g, one level); an attribute *of* it
    would be a statement about the type of `q`, which is not what the
    parameter rule says.
    """
    src = """
    def write_file(self, q):
        self.q = q
        os.remove(self.q.name)
    """
    assert _classify(src) == "unknown"


# ============================================================
# Primitives used by the classification rules
# ============================================================
# These are the pieces `_classify_argument` is built from. They are
# exercised through it above; the tests here pin them directly so a
# change in a primitive is attributable to the primitive.

def _expr(source: str) -> ast.expr:
    """The first top-level expression statement in `source`."""
    return ast.parse(source).body[0].value


def test_is_literal_for_constants_and_constant_collections() -> None:
    """A constant and a collection of constants are constant data (1.2a.4)."""
    assert _is_literal(_expr('"cmd"')) is True
    assert _is_literal(_expr('["docker", "info"]')) is True
    assert _is_literal(_expr('{"a": 1, "b": [2, 3]}')) is True
    assert _is_literal(_expr("[]")) is True


def test_is_literal_is_false_for_names_and_calls() -> None:
    """A name, an attribute and a call are not literals."""
    assert _is_literal(_expr("param")) is False
    assert _is_literal(_expr("config.path")) is False
    assert _is_literal(_expr("fetch()")) is False


def test_is_literal_is_false_for_a_mixed_collection() -> None:
    """One non-constant element makes the whole collection non-constant."""
    assert _is_literal(_expr('["a", param]')) is False


def test_is_literal_none_is_not_literal() -> None:
    """`None` is not a literal here: it marks `{**mapping}` unpacking."""
    assert _is_literal(None) is False


def test_parameter_names_exclude_self() -> None:
    """`self`/`cls` are bound, not attacker-supplied."""
    func = _func("def f(self, a, b=1, *args, c, **kw): ...")
    assert _parameter_names(func) == {"a", "b", "args", "c", "kw"}

    cls_func = _func("class X:\n    def f(cls, a): ...")
    assert _parameter_names(cls_func) == {"a"}


def test_local_variable_names_collect_store_targets() -> None:
    """Assignments, `for` targets, `with ... as` and walrus count."""
    func = _func(
        "def f(self, items):\n"
        "    path = fetch()\n"
        "    for item in items:\n"
        "        pass\n"
        "    with open(path) as handle:\n"
        "        pass\n"
        "    if (found := lookup()):\n"
        "        pass\n"
    )
    assert _local_variable_names(func) == {
        "path", "item", "handle", "found",
    }


def test_self_attribute_names_follow_parameter_and_self_chains() -> None:
    """A parameter, and a chain of derived attributes, both count (1.2a.3g)."""
    func = _func(
        "def f(self, q):\n"
        "    self.q = q\n"
        "    self.r = self.q\n"
        "    self.literal = 'x'\n"
        "    self.computed = fetch()\n"
    )
    assert _self_attribute_names(func) == frozenset({"q", "r"})


def test_self_attribute_names_ignore_another_methods_assignment() -> None:
    """The scope is one body: `__init__` says nothing about `delete`."""
    func = _func(
        "def delete(self):\n"
        "    self.db.delete(self.q)\n"
    )
    assert _self_attribute_names(func) == frozenset()
