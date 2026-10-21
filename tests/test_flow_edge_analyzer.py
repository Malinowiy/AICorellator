"""Unit tests for `flow.edge_analyzer` — the edge question (1.2a.1).

The analyzer is pure: these tests build AST nodes from source strings
and assert the returned `FlowDecision.kind` — the published
`data_flow_confirmed` value of a `has_access_to` edge. The decision
object itself (its `source` and `evidence`) is asserted in
`test_flow_decision.py`. No filesystem, no TSA subprocess.
"""

from __future__ import annotations

import ast
import textwrap

from aicorellator.parsing.flow.arguments import FunctionNode
from aicorellator.parsing.flow.decision import FlowDecision
from aicorellator.parsing.flow.edge_analyzer import analyze_has_access_to


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


def _analyze(source: str, sink: str = "os.remove") -> FlowDecision:
    """Analyze the call to `sink` inside the function in `source`."""
    func = _func(source)
    call = _sink_call(func, sink.split(".")[-1])
    return analyze_has_access_to(func, call)


# ============================================================
# The six values
# ============================================================

def test_parameter_match() -> None:
    """An argument that is a tool parameter is a direct match."""
    src = """
    def read_file(query):
        os.remove(query)
    """
    assert _analyze(src).kind == "parameter_match"


def test_local_var_match() -> None:
    """An argument assigned in the body is a local-variable match."""
    src = """
    def purge():
        path = fetch_path()
        os.remove(path)
    """
    assert _analyze(src).kind == "local_var_match"


def test_local_var_match_for_loop_target() -> None:
    """`for` targets are locals (Store context)."""
    src = """
    def purge(files):
        for f in files:
            os.remove(f)
    """
    assert _analyze(src).kind == "local_var_match"


def test_call_result_match() -> None:
    """An argument that is itself a call result is inferential."""
    src = """
    def purge():
        os.remove(fetch_path())
    """
    assert _analyze(src).kind == "call_result_match"


def test_call_result_match_await() -> None:
    """`await foo()` is still a call result."""
    src = """
    async def purge():
        os.remove(await fetch_path())
    """
    assert _analyze(src).kind == "call_result_match"


def test_literal_only() -> None:
    """A literal argument is a literal-only match."""
    src = """
    def purge():
        os.remove("/tmp/scratch")
    """
    assert _analyze(src).kind == "literal_only"


def test_no_args() -> None:
    """A sink call with no arguments is `no_args`, not `unknown`."""
    src = """
    def purge():
        delete_all()
    """
    assert _analyze(src, "delete_all").kind == "no_args"


def test_unknown_attribute_access() -> None:
    """Attribute access cannot be followed by name-based analysis."""
    src = """
    def purge(config):
        os.remove(config.path)
    """
    assert _analyze(src).kind == "unknown"


# ============================================================
# Edge cases
# ============================================================

def test_empty_args_is_no_args() -> None:
    """Zero args and zero keywords → `no_args`."""
    src = """
    def purge():
        os.remove()
    """
    assert _analyze(src).kind == "no_args"


def test_nested_attribute_call_is_call_result() -> None:
    """`config.get("path")` is a call result, not a parameter match."""
    src = """
    def purge(config):
        os.remove(config.get("path"))
    """
    assert _analyze(src).kind == "call_result_match"


def test_nested_subscript_is_unknown() -> None:
    """A subscript of a parameter cannot be resolved → unknown."""
    src = """
    def purge(config):
        os.remove(config["path"])
    """
    assert _analyze(src).kind == "unknown"


def test_fstring_with_parameter_is_parameter_match() -> None:
    """An f-string with a parameter-derived interpolation is data.

    Changed by 1.2a.3g: `_classify_argument` used to treat every
    `ast.JoinedStr` as `unknown`. The body under analysis is the one
    that built the string, so the parameter inside it is evidence.
    """
    src = """
    def purge(base, name):
        os.remove(f"{base}/{name}")
    """
    assert _analyze(src).kind == "parameter_match"


def test_fstring_without_a_parameter_is_unknown() -> None:
    """An f-string whose interpolations are globals is still `unknown`.

    `BASE_URL` is a module-level name, not a parameter or a local, so
    there is no parameter-derived component (1.2a.3g).
    """
    src = """
    def purge():
        os.remove(f"{BASE_URL}/api")
    """
    assert _analyze(src).kind == "unknown"


def test_fstring_with_a_literal_local_is_local_var_match() -> None:
    """A local variable counts even when its value is a literal.

    The analyzer tracks names, not values (`_local_variable_names`), so
    `base = "https://x"` is indistinguishable from a parameter-derived
    local. The over-approximation is deliberate and pre-existing
    (ADR-0001); `f"{base}"` is classified like `base` itself.
    """
    src = """
    def purge():
        base = "https://example.com"
        os.remove(f"{base}/api")
    """
    assert _analyze(src).kind == "local_var_match"


def test_fstring_nested_interpolation_is_parameter_match() -> None:
    """Interpolations are classified recursively (1.2a.3g)."""
    src = """
    def purge(q):
        os.remove(f"{f'{q}'}/{q}")
    """
    assert _analyze(src).kind == "parameter_match"


def test_strongest_result_wins() -> None:
    """A parameter beats a coexisting literal."""
    src = """
    def purge(query):
        os.remove("/tmp/scratch", query)
    """
    assert _analyze(src).kind == "parameter_match"


def test_mixed_literal_and_unknown_is_unknown() -> None:
    """`literal_only` is a universal: one unresolved argument defeats it.

    `os.remove(config.path, "/tmp/scratch")` may still route data through
    `config.path`, so the result cannot claim "literals only".
    """
    src = """
    def purge(config):
        os.remove(config.path, "/tmp/scratch")
    """
    assert _analyze(src).kind == "unknown"


def test_mixed_literal_and_subscript_is_unknown() -> None:
    """A subscript alongside a literal is likewise not `literal_only`."""
    src = """
    def purge(config):
        os.remove(config["a"], "/tmp/x")
    """
    assert _analyze(src).kind == "unknown"


def test_all_literals_is_literal_only() -> None:
    """Every argument a literal → `literal_only`."""
    src = """
    def purge():
        os.remove("/tmp/a", "/tmp/b")
    """
    assert _analyze(src).kind == "literal_only"


# ============================================================
# Constant collections (1.2a.4)
# ============================================================

def test_list_argument_is_literal_only() -> None:
    """A list of constants is constant data, not an unresolved
    expression: `subprocess.run(["docker", "info"])`."""
    src = """
    def check_docker():
        subprocess.run(["docker", "info"])
    """
    assert _analyze(src, "subprocess.run").kind == "literal_only"


def test_tuple_argument_is_literal_only() -> None:
    """Tuples follow the same rule as lists."""
    src = """
    def check_redis():
        subprocess.run(("redis-cli", "ping"))
    """
    assert _analyze(src, "subprocess.run").kind == "literal_only"


def test_dict_argument_is_literal_only() -> None:
    """A dict whose keys and values are all constants is constant data."""
    src = """
    def check_config():
        subprocess.run({"a": 1, "b": 2})
    """
    assert _analyze(src, "subprocess.run").kind == "literal_only"


def test_set_argument_is_literal_only() -> None:
    """Sets follow the same rule."""
    src = """
    def check_flags():
        subprocess.run({"a", "b"})
    """
    assert _analyze(src, "subprocess.run").kind == "literal_only"


def test_empty_collection_argument_is_literal_only() -> None:
    """An empty collection has no element that could carry data."""
    src = """
    def check_nothing():
        subprocess.run([])
    """
    assert _analyze(src, "subprocess.run").kind == "literal_only"


def test_mixed_collection_argument_is_parameter_match() -> None:
    """A collection holding a parameter is a match (1.2a.3g).

    Changed by 1.2a.3g: 1.2a.4 pinned this as `unknown`, arguing that we
    cannot tell which element the sink consumes. The body under analysis
    is the one that built the collection, and the parameter is inside
    the value handed to the dangerous call, so the collection does carry
    parameter-derived data. ADR-0015 accepts the loosening deliberately:
    a mixed collection is now a true positive, while a collection whose
    non-constant part cannot be resolved (`["a", config.path]`) is still
    `unknown`.
    """
    src = """
    def check_tool(param):
        subprocess.run(["a", param])
    """
    assert _analyze(src, "subprocess.run").kind == "parameter_match"


def test_collection_with_local_var_is_local_var_match() -> None:
    """A local variable inside a collection keeps its own kind.

    The element's kind is returned, not a flattened `parameter_match`:
    the sink analyzer collapses all three data kinds, the edge analyzer
    reports the precise one (1.2a.3g).
    """
    src = """
    def check_tool():
        cmd = fetch_command()
        subprocess.run(["a", cmd])
    """
    assert _analyze(src, "subprocess.run").kind == "local_var_match"


def test_collection_with_unresolved_element_is_unknown() -> None:
    """An element that cannot be resolved contributes nothing.

    `config.path` is an attribute of another object and a call result is
    not a name — neither promotes the collection (ADR-0001).
    """
    attribute = """
    def check_tool(config):
        subprocess.run(["a", config.path])
    """
    call_result = """
    def check_tool(param):
        subprocess.run(["a", fetch(param)])
    """
    assert _analyze(attribute, "subprocess.run").kind == "unknown"
    assert _analyze(call_result, "subprocess.run").kind == "unknown"


def test_nested_collection_argument_is_literal_only() -> None:
    """Collections recurse: all elements constants all the way down."""
    src = """
    def check_nested():
        subprocess.run([["a"], ["b"]])
    """
    assert _analyze(src, "subprocess.run").kind == "literal_only"


def test_nested_mixed_collection_argument_is_parameter_match() -> None:
    """A parameter one level down promotes the whole collection.

    Changed by 1.2a.3g: the elements are classified recursively, so the
    inner list is `literal_only`, the parameter is `parameter_match`,
    and the outer list takes the strongest of the two.
    """
    src = """
    def check_nested(param):
        subprocess.run([["a"], param])
    """
    assert _analyze(src, "subprocess.run").kind == "parameter_match"


def test_dict_with_parameter_value_is_parameter_match() -> None:
    """A dict value that is a parameter is data (1.2a.3g).

    Changed by 1.2a.3g: the values are classified like list elements, so
    `{"q": param}` is a match and `{"a": 1, "b": 2}` stays
    `literal_only`.
    """
    src = """
    def check_config(param):
        subprocess.run({"a": param})
    """
    assert _analyze(src, "subprocess.run").kind == "parameter_match"


def test_dict_unpacking_is_unknown() -> None:
    """`{**mapping}` is not a constant collection: the mapping is not
    literal, and `ast.Dict.keys` marks the unpacking with `None`."""
    src = """
    def check_config(param):
        subprocess.run({**param})
    """
    assert _analyze(src, "subprocess.run").kind == "unknown"


def test_starred_element_is_unknown() -> None:
    """`[*param, "a"]` mixes a non-constant element in."""
    src = """
    def check_args(param):
        subprocess.run([*param, "a"])
    """
    assert _analyze(src, "subprocess.run").kind == "unknown"


def test_literal_with_call_result_is_call_result() -> None:
    """A call result outranks a coexisting literal."""
    src = """
    def purge():
        os.remove("/tmp/scratch", fetch_path())
    """
    assert _analyze(src).kind == "call_result_match"


def test_literal_with_local_var_is_local_var() -> None:
    """A local variable outranks a coexisting literal."""
    src = """
    def purge():
        path = fetch_path()
        os.remove("/tmp/scratch", path)
    """
    assert _analyze(src).kind == "local_var_match"


def test_keyword_argument_parameter() -> None:
    """Keyword-argument values are classified like positional ones."""
    src = """
    def purge(query):
        os.remove(path=query)
    """
    assert _analyze(src).kind == "parameter_match"


def test_self_is_not_a_parameter() -> None:
    """`self` is the bound instance, not attacker-supplied data."""
    src = """
    def purge(self):
        os.remove(self)
    """
    assert _analyze(src).kind == "unknown"


def test_global_name_is_unknown() -> None:
    """A module-level constant is not a parameter or a local."""
    src = """
    def purge():
        os.remove(DEFAULT_PATH)
    """
    assert _analyze(src).kind == "unknown"


def test_plain_parameter_is_still_a_data_match() -> None:
    """Unchanged: a bare parameter is the strongest evidence."""
    src = """
    def write_file(param):
        os.remove(param)
    """
    assert _analyze(src).kind == "parameter_match"


def test_edge_analyzer_does_not_follow_parameter_attributes() -> None:
    """`has_access_to` is unchanged — the rule is opt-in (1.2a.3j).

    `analyze_has_access_to` publishes `data_flow_confirmed`, and 1.2a.3g
    drew the same line for `self.q`. The value 1.2a.3j changes is the
    sink body's, which the tests below pin.
    """
    src = """
    def call_sink(file_data):
        os.remove(file_data.filename)
    """
    assert _analyze(src).kind == "unknown"
