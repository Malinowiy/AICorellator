"""Unit tests for the Flow Analyzer (ADR-0015).

The analyzer is pure: these tests build AST nodes from source strings
and assert the returned `data_flow_confirmed` value. No filesystem, no
TSA subprocess.
"""

from __future__ import annotations

import ast
import textwrap

from aicorellator.parsing.flow_analyzer import (
    DATA_FLOW_CONFIRMED_VALUES,
    DATA_FLOW_DATA_VALUES,
    FunctionNode,
    SINK_DATA_FLOW_VALUES,
    _is_commit_call,
    analyze_has_access_to,
    analyze_sink_body,
    analyze_sink_class,
    analyze_tool_returns,
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


def _analyze(source: str, sink: str = "os.remove") -> str:
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
    assert _analyze(src) == "parameter_match"


def test_local_var_match() -> None:
    """An argument assigned in the body is a local-variable match."""
    src = """
    def purge():
        path = fetch_path()
        os.remove(path)
    """
    assert _analyze(src) == "local_var_match"


def test_local_var_match_for_loop_target() -> None:
    """`for` targets are locals (Store context)."""
    src = """
    def purge(files):
        for f in files:
            os.remove(f)
    """
    assert _analyze(src) == "local_var_match"


def test_call_result_match() -> None:
    """An argument that is itself a call result is inferential."""
    src = """
    def purge():
        os.remove(fetch_path())
    """
    assert _analyze(src) == "call_result_match"


def test_call_result_match_await() -> None:
    """`await foo()` is still a call result."""
    src = """
    async def purge():
        os.remove(await fetch_path())
    """
    assert _analyze(src) == "call_result_match"


def test_literal_only() -> None:
    """A literal argument is a literal-only match."""
    src = """
    def purge():
        os.remove("/tmp/scratch")
    """
    assert _analyze(src) == "literal_only"


def test_no_args() -> None:
    """A sink call with no arguments is `no_args`, not `unknown`."""
    src = """
    def purge():
        delete_all()
    """
    assert _analyze(src, "delete_all") == "no_args"


def test_unknown_attribute_access() -> None:
    """Attribute access cannot be followed by name-based analysis."""
    src = """
    def purge(config):
        os.remove(config.path)
    """
    assert _analyze(src) == "unknown"


# ============================================================
# Edge cases
# ============================================================

def test_empty_args_is_no_args() -> None:
    """Zero args and zero keywords → `no_args`."""
    src = """
    def purge():
        os.remove()
    """
    assert _analyze(src) == "no_args"


def test_nested_attribute_call_is_call_result() -> None:
    """`config.get("path")` is a call result, not a parameter match."""
    src = """
    def purge(config):
        os.remove(config.get("path"))
    """
    assert _analyze(src) == "call_result_match"


def test_nested_subscript_is_unknown() -> None:
    """A subscript of a parameter cannot be resolved → unknown."""
    src = """
    def purge(config):
        os.remove(config["path"])
    """
    assert _analyze(src) == "unknown"


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
    assert _analyze(src) == "parameter_match"


def test_fstring_without_a_parameter_is_unknown() -> None:
    """An f-string whose interpolations are globals is still `unknown`.

    `BASE_URL` is a module-level name, not a parameter or a local, so
    there is no parameter-derived component (1.2a.3g).
    """
    src = """
    def purge():
        os.remove(f"{BASE_URL}/api")
    """
    assert _analyze(src) == "unknown"


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
    assert _analyze(src) == "local_var_match"


def test_fstring_nested_interpolation_is_parameter_match() -> None:
    """Interpolations are classified recursively (1.2a.3g)."""
    src = """
    def purge(q):
        os.remove(f"{f'{q}'}/{q}")
    """
    assert _analyze(src) == "parameter_match"


def test_strongest_result_wins() -> None:
    """A parameter beats a coexisting literal."""
    src = """
    def purge(query):
        os.remove("/tmp/scratch", query)
    """
    assert _analyze(src) == "parameter_match"


def test_mixed_literal_and_unknown_is_unknown() -> None:
    """`literal_only` is a universal: one unresolved argument defeats it.

    `os.remove(config.path, "/tmp/scratch")` may still route data through
    `config.path`, so the result cannot claim "literals only".
    """
    src = """
    def purge(config):
        os.remove(config.path, "/tmp/scratch")
    """
    assert _analyze(src) == "unknown"


def test_mixed_literal_and_subscript_is_unknown() -> None:
    """A subscript alongside a literal is likewise not `literal_only`."""
    src = """
    def purge(config):
        os.remove(config["a"], "/tmp/x")
    """
    assert _analyze(src) == "unknown"


def test_all_literals_is_literal_only() -> None:
    """Every argument a literal → `literal_only`."""
    src = """
    def purge():
        os.remove("/tmp/a", "/tmp/b")
    """
    assert _analyze(src) == "literal_only"


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
    assert _analyze(src, "subprocess.run") == "literal_only"


def test_tuple_argument_is_literal_only() -> None:
    """Tuples follow the same rule as lists."""
    src = """
    def check_redis():
        subprocess.run(("redis-cli", "ping"))
    """
    assert _analyze(src, "subprocess.run") == "literal_only"


def test_dict_argument_is_literal_only() -> None:
    """A dict whose keys and values are all constants is constant data."""
    src = """
    def check_config():
        subprocess.run({"a": 1, "b": 2})
    """
    assert _analyze(src, "subprocess.run") == "literal_only"


def test_set_argument_is_literal_only() -> None:
    """Sets follow the same rule."""
    src = """
    def check_flags():
        subprocess.run({"a", "b"})
    """
    assert _analyze(src, "subprocess.run") == "literal_only"


def test_empty_collection_argument_is_literal_only() -> None:
    """An empty collection has no element that could carry data."""
    src = """
    def check_nothing():
        subprocess.run([])
    """
    assert _analyze(src, "subprocess.run") == "literal_only"


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
    assert _analyze(src, "subprocess.run") == "parameter_match"


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
    assert _analyze(src, "subprocess.run") == "local_var_match"


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
    assert _analyze(attribute, "subprocess.run") == "unknown"
    assert _analyze(call_result, "subprocess.run") == "unknown"


def test_nested_collection_argument_is_literal_only() -> None:
    """Collections recurse: all elements constants all the way down."""
    src = """
    def check_nested():
        subprocess.run([["a"], ["b"]])
    """
    assert _analyze(src, "subprocess.run") == "literal_only"


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
    assert _analyze(src, "subprocess.run") == "parameter_match"


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
    assert _analyze(src, "subprocess.run") == "parameter_match"


def test_dict_unpacking_is_unknown() -> None:
    """`{**mapping}` is not a constant collection: the mapping is not
    literal, and `ast.Dict.keys` marks the unpacking with `None`."""
    src = """
    def check_config(param):
        subprocess.run({**param})
    """
    assert _analyze(src, "subprocess.run") == "unknown"


def test_starred_element_is_unknown() -> None:
    """`[*param, "a"]` mixes a non-constant element in."""
    src = """
    def check_args(param):
        subprocess.run([*param, "a"])
    """
    assert _analyze(src, "subprocess.run") == "unknown"


def test_literal_with_call_result_is_call_result() -> None:
    """A call result outranks a coexisting literal."""
    src = """
    def purge():
        os.remove("/tmp/scratch", fetch_path())
    """
    assert _analyze(src) == "call_result_match"


def test_literal_with_local_var_is_local_var() -> None:
    """A local variable outranks a coexisting literal."""
    src = """
    def purge():
        path = fetch_path()
        os.remove("/tmp/scratch", path)
    """
    assert _analyze(src) == "local_var_match"


def test_keyword_argument_parameter() -> None:
    """Keyword-argument values are classified like positional ones."""
    src = """
    def purge(query):
        os.remove(path=query)
    """
    assert _analyze(src) == "parameter_match"


def test_self_is_not_a_parameter() -> None:
    """`self` is the bound instance, not attacker-supplied data."""
    src = """
    def purge(self):
        os.remove(self)
    """
    assert _analyze(src) == "unknown"


def test_global_name_is_unknown() -> None:
    """A module-level constant is not a parameter or a local."""
    src = """
    def purge():
        os.remove(DEFAULT_PATH)
    """
    assert _analyze(src) == "unknown"


# ============================================================
# Constants
# ============================================================

def test_confirmed_values_are_the_six() -> None:
    assert DATA_FLOW_CONFIRMED_VALUES == {
        "parameter_match", "local_var_match", "call_result_match",
        "literal_only", "no_args", "unknown",
    }


def test_data_values_are_the_three_data_kinds() -> None:
    assert DATA_FLOW_DATA_VALUES == {
        "parameter_match", "local_var_match", "call_result_match",
    }
    assert DATA_FLOW_DATA_VALUES <= DATA_FLOW_CONFIRMED_VALUES


def test_sink_data_flow_values_are_the_three() -> None:
    """`sink_data_flow` collapses the data kinds into one value (1.2a.1)."""
    assert SINK_DATA_FLOW_VALUES == {
        "parameter_match", "literal_only", "unknown",
    }


# ============================================================
# Sink body analyzer (1.2a.1)
# ============================================================

def _sink_body(source: str) -> str:
    """Analyze the first function in `source` as a sink body."""
    return analyze_sink_body(_func(source))


def test_sink_parameter_reaches_dangerous_call() -> None:
    """`delete_file(file_id)` → the parameter reaches `os.remove`."""
    src = """
    def delete_file(self, file_id):
        os.remove(file_id)
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_local_var_derived_from_parameter() -> None:
    """A local variable built in the body is still the sink's own data."""
    src = """
    def delete_file(self, file_id):
        path = self._resolve(file_id)
        os.remove(path)
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_call_result_counts_as_data() -> None:
    """A call result is derived from the sink's parameters or state."""
    src = """
    def write_report(self, name):
        open(self._path(name), "w")
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_loop_target_is_data() -> None:
    """`for` targets are locals; a non-literal argument is data."""
    src = """
    def purge(self, files):
        for f in files:
            os.remove(f)
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_literals_only() -> None:
    """Only constants reach the dangerous call → literal_only."""
    src = """
    def purge(self):
        os.remove("/tmp/scratch")
    """
    assert _sink_body(src) == "literal_only"


def test_sink_no_dangerous_call_is_unknown() -> None:
    """A body with no dangerous call has no sink evidence."""
    src = """
    def get_file(self, file_id):
        return self.db.query(file_id)
    """
    assert _sink_body(src) == "unknown"


def test_sink_empty_body_is_unknown() -> None:
    """An empty body cannot be analyzed."""
    src = """
    def nothing(self):
        pass
    """
    assert _sink_body(src) == "unknown"


def test_sink_no_argument_dangerous_call_is_unknown() -> None:
    """A dangerous call with no arguments carries no data either way."""
    src = """
    def create_tables(self):
        setup_database()
    """
    assert _sink_body(src) == "unknown"


def test_sink_literal_with_unresolved_attribute_is_unknown() -> None:
    """One unresolved argument defeats `literal_only` (same rule as edges)."""
    src = """
    def purge(self, config):
        os.remove(config.path, "/tmp/scratch")
    """
    assert _sink_body(src) == "unknown"


def test_sink_strongest_result_wins() -> None:
    """A literal call does not hide a data call elsewhere in the body."""
    src = """
    def purge(self, query):
        os.remove("/tmp/scratch")
        os.remove(query)
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_async_def_supported() -> None:
    """`async def` sink bodies are analyzed like sync ones."""
    src = """
    async def wipe(self, target):
        subprocess.run(target, shell=True)
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_shell_call_with_constant_is_literal_only() -> None:
    """A constant command reaches the shell → literal_only."""
    src = """
    def run_script(self):
        subprocess.run("ls -la", shell=True)
    """
    assert _sink_body(src) == "literal_only"


def test_sink_list_argument_is_literal_only() -> None:
    """A list of constants is constant data → `literal_only`.

    Changed by 1.2a.4: this test previously asserted `unknown` on the
    grounds that a list is not `ast.Constant`. A list whose elements
    are all constants is equivalent to a literal for the sink analyzer.
    """
    src = """
    def run_script(self):
        subprocess.run(["ls", "-la"])
    """
    assert _sink_body(src) == "literal_only"


def test_sink_tuple_argument_is_literal_only() -> None:
    """Tuples follow the same rule at the sink call site."""
    src = """
    def check_redis(self):
        subprocess.run(("redis-cli", "ping"))
    """
    assert _sink_body(src) == "literal_only"


def test_sink_dict_argument_is_literal_only() -> None:
    """A constant dict reaches the dangerous call → `literal_only`."""
    src = """
    def check_config(self):
        subprocess.run({"a": 1, "b": 2})
    """
    assert _sink_body(src) == "literal_only"


def test_sink_set_argument_is_literal_only() -> None:
    """Sets follow the same rule at the sink call site."""
    src = """
    def check_flags(self):
        subprocess.run({"a", "b"})
    """
    assert _sink_body(src) == "literal_only"


def test_sink_empty_collection_is_literal_only() -> None:
    """An empty collection carries no data → `literal_only`."""
    src = """
    def check_nothing(self):
        subprocess.run([])
    """
    assert _sink_body(src) == "literal_only"


def test_sink_mixed_collection_is_parameter_match() -> None:
    """A collection with the sink's parameter is data (1.2a.3g).

    Changed by 1.2a.3g, which loosened the 1.2a.4 rule: the parameter is
    inside the value handed to the dangerous call.
    """
    src = """
    def check_tool(self, param):
        subprocess.run([param, "a"])
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_nested_collection_is_literal_only() -> None:
    """Nested collections of constants are still constant data."""
    src = """
    def check_nested(self):
        subprocess.run([["a"], ["b"]])
    """
    assert _sink_body(src) == "literal_only"


def test_sink_nested_mixed_collection_is_parameter_match() -> None:
    """A parameter one level down promotes the collection (1.2a.3g)."""
    src = """
    def check_nested(self, param):
        subprocess.run([["a"], param])
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_dict_unpacking_is_unknown() -> None:
    """`{**mapping}` is not a constant collection."""
    src = """
    def check_config(self, param):
        subprocess.run({**param})
    """
    assert _sink_body(src) == "unknown"


def test_sink_self_attribute_from_parameter_is_data() -> None:
    """`self.q = q; self.db.delete(self.q)` is a data flow (1.2a.3g).

    The sink stores its own parameter on the instance and hands it to
    the dangerous call. Values are not followed, but the name is: the
    attribute is parameter-derived because this body assigned it from a
    parameter.
    """
    src = """
    def delete(self, q):
        self.q = q
        self.db.delete(self.q)
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_self_attribute_from_a_local_var_is_data() -> None:
    """A local variable on the right-hand side is enough (1.2a.3g)."""
    src = """
    def delete(self, q):
        path = os.path.join("/tmp", q)
        self.q = path
        self.db.delete(self.q)
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_self_attribute_chain_is_data() -> None:
    """`self.q = q; self.r = self.q; ... self.r` follows the chain.

    The "right-hand side is another derived attribute" rule is applied
    until nothing changes, so a chain of any length is followed
    (1.2a.3g).
    """
    src = """
    def delete(self, q):
        self.q = q
        self.r = self.q
        self.s = self.r
        self.db.delete(self.s)
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_self_attribute_from_a_literal_is_unknown() -> None:
    """`self.q = "literal"` is not parameter-derived (1.2a.3g)."""
    src = """
    def delete(self, q):
        self.q = "literal"
        self.db.delete(self.q)
    """
    assert _sink_body(src) == "unknown"


def test_sink_self_attribute_from_a_call_is_unknown() -> None:
    """A call result on the right-hand side is not a name (ADR-0001)."""
    src = """
    def delete(self, q):
        self.q = fetch(q)
        self.db.delete(self.q)
    """
    assert _sink_body(src) == "unknown"


def test_sink_self_attribute_not_assigned_here_is_unknown() -> None:
    """An attribute on `self` that this body never assigns (1.2a.3g).

    `self.q` may hold anything the rest of the class put there — an
    assignment in another method is not visible, because each body is
    analyzed on its own (1.2a.5).
    """
    src = """
    def delete(self, q):
        self.db.delete(self.q)
    """
    assert _sink_body(src) == "unknown"


def test_sink_attribute_of_another_object_is_unknown() -> None:
    """`other.attr` is not `self.attr` — only `self` is followed.

    `other.attr` is someone else's state: the body did not store a
    parameter in it (1.2a.3g).
    """
    src = """
    def delete(self, other):
        self.db.delete(other.attr)
    """
    assert _sink_body(src) == "unknown"


def test_sink_self_attribute_annotated_and_augmented_is_data() -> None:
    """`self.q: str = q` and `self.q += q` are assignments too (1.2a.3g)."""
    annotated = """
    def delete(self, q):
        self.q: str = q
        self.db.delete(self.q)
    """
    augmented = """
    def delete(self, q):
        self.q += q
        self.db.delete(self.q)
    """
    assert _sink_body(annotated) == "parameter_match"
    assert _sink_body(augmented) == "parameter_match"


def test_sink_fstring_with_parameter_is_data() -> None:
    """An f-string built from the sink's parameter reaches the call.

    This is the dvmcp `VulnerableChatAssistant` shape: the URL is
    formatted at the call site (1.2a.3g).
    """
    src = """
    def fetch(self, q):
        requests.get(f"{BASE_URL}/api/{q}")
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_fstring_without_a_parameter_is_unknown() -> None:
    """No parameter-derived interpolation → no evidence (1.2a.3g).

    An f-string is never `literal_only`: it is evaluated at runtime even
    when every interpolation is a constant.
    """
    src = """
    def fetch(self, q):
        requests.get(f"{BASE_URL}/api/tags")
    """
    assert _sink_body(src) == "unknown"


def test_sink_fstring_with_a_derived_attribute_is_data() -> None:
    """`self.base = base` then `f"{self.base}/tags"` is data (1.2a.3g)."""
    src = """
    def fetch(self, base):
        self.base = base
        requests.get(f"{self.base}/api/tags")
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_dict_with_parameter_value_is_data() -> None:
    """`requests.post(url, json={"q": q})` carries the parameter.

    The dict is the payload; the values are what the sink consumes
    (1.2a.3g).
    """
    src = """
    def post(self, q):
        requests.post(URL, json={"q": q})
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_dict_with_local_var_value_is_data() -> None:
    """The dvmcp shape: a local list built from the parameter.

    `messages` is a local variable, and the body under analysis is the
    one that built it (1.2a.3g).
    """
    src = """
    def chat(self, user_message):
        messages = [{"role": "user", "content": user_message}]
        requests.post(
            f"{BASE_URL}/api/chat",
            json={"model": MODEL, "messages": messages},
        )
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_dict_all_literals_is_literal_only() -> None:
    """A dict of constants stays `literal_only` (1.2a.4)."""
    src = """
    def post(self, q):
        requests.post("https://example.com/api", json={"a": 1, "b": "x"})
    """
    assert _sink_body(src) == "literal_only"


def test_sink_list_with_parameter_is_data() -> None:
    """A command list with the parameter in it is a data flow (1.2a.3g).

    Changed by 1.2a.3g: `[param, "a"]` used to be `unknown` (1.2a.4).
    """
    src = """
    def convert(self, q):
        subprocess.run(["docker", q])
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_tuple_with_parameter_is_data() -> None:
    """Tuples follow the same rule as lists (1.2a.3g)."""
    src = """
    def convert(self, q):
        subprocess.run(("docker", q))
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_orm_delete_with_parameter() -> None:
    """The FinBot `delete_file` shape: the sink parameter reaches the
    ORM delete call (1.2a.3)."""
    src = """
    def delete_file(self, f):
        self.db.delete(f)
        self.db.commit()
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_orm_delete_with_local_var() -> None:
    """A local variable derived from the sink parameter is still data."""
    src = """
    def delete_file(self, file_id):
        record = self.db.query(file_id).first()
        self.db.delete(record)
        self.db.commit()
    """
    assert _sink_body(src) == "parameter_match"


def test_sink_orm_delete_with_literal() -> None:
    """Only a constant reaches the ORM delete call → literal_only."""
    src = """
    def purge(self):
        self.db.delete("literal-id")
        self.db.commit()
    """
    assert _sink_body(src) == "literal_only"


# ============================================================
# Commit exception (1.2a.3c)
# ============================================================

def test_commit_call_is_recognized_by_qualifier() -> None:
    """Every documented qualifier is a commit call (1.2a.3c)."""
    for qualifier in (
        "db", "session", "repository", "repo",
        "transaction", "connection", "conn",
    ):
        src = f"""
        def flush(self, oid):
            {qualifier}.commit()
        """
        call = _sink_call(_func(src), "commit")
        assert _is_commit_call(call), qualifier


def test_commit_call_requires_a_qualifier() -> None:
    """A bare `commit()` is not a commit call: no qualifier, no match."""
    for body in ("commit()", "commit_all()", "flush()", "commit_now()"):
        src = f"""
        def flush(self, oid):
            self.{body}
        """
        call = _sink_call(_func(src), body.split("(")[0])
        assert not _is_commit_call(call), body


def test_commit_only_body_with_parameter_is_parameter_match() -> None:
    """The FinBot `toggle_admin` shape: the body's only dangerous call is
    a bare `db.commit()`, which persists the session (1.2a.3c)."""
    src = """
    def toggle_admin(self, user_id):
        self.db.commit()
    """
    assert _sink_body(src) == "parameter_match"


def test_commit_only_body_on_session_is_parameter_match() -> None:
    """`session.commit()` gets the same treatment as `db.commit()`."""
    src = """
    def update_status(self, order_id):
        session.commit()
    """
    assert _sink_body(src) == "parameter_match"


def test_commit_after_a_write_is_parameter_match() -> None:
    """The write already supplied `parameter_match`; the commit adds the
    same value — the strongest result is unchanged."""
    src = """
    def mark_as_read(self, email_id):
        self.db.add(email_id)
        self.db.commit()
    """
    assert _sink_body(src) == "parameter_match"


def test_commit_exception_does_not_apply_to_subprocess() -> None:
    """`subprocess.run()` with no arguments is not a commit → unknown."""
    src = """
    def run_task(self, cmd):
        subprocess.run()
    """
    assert _sink_body(src) == "unknown"


def test_commit_exception_does_not_apply_to_os_system() -> None:
    """`os.system()` with no arguments is not a commit → unknown."""
    src = """
    def shell_out(self, cmd):
        os.system()
    """
    assert _sink_body(src) == "unknown"


def test_commit_contributes_while_subprocess_run_does_not() -> None:
    """The commit carries the result even when a bare shell call sits in
    the same body."""
    src = """
    def toggle(self, user_id):
        self.db.commit()
        subprocess.run()
    """
    assert _sink_body(src) == "parameter_match"


def test_commit_only_body_without_parameters_is_unknown() -> None:
    """Edge case (1.2a.3c): a sink with no parameters has nothing
    parameter-derived to persist, so `parameter_match` would claim
    evidence that does not exist — the commit contributes nothing."""
    src = """
    def flush(self):
        self.db.commit()
    """
    assert _sink_body(src) == "unknown"


def test_commit_without_a_write_promotes_a_literal_only_body() -> None:
    """Accepted consequence of the rule: the commit is treated as a
    parameter-derived persistence step, so a body whose only write is a
    constant still reports `parameter_match`."""
    src = """
    def update_status(self, order_id):
        cursor.execute("UPDATE orders SET status = 'done'")
        self.conn.commit()
    """
    assert _sink_body(src) == "parameter_match"


def test_commit_of_a_qualifier_outside_the_capabilities_counts() -> None:
    """`conn.commit` is not a sink capability, but the commit exception
    still recognizes it (the sink comes from the `execute` call)."""
    src = """
    def set_flag(self, flag):
        self.session.execute("UPDATE configs SET flag = 1")
        self.conn.commit()
    """
    assert _sink_body(src) == "parameter_match"

# --- Private qualifiers (1.2a.3e) ---

def test_commit_call_accepts_a_private_qualifier() -> None:
    """`_conn.commit` is `conn.commit` on a private connection — the
    spelling mcploitable uses throughout (1.2a.3e)."""
    src = """
    def seed(self):
        self._conn.commit()
    """
    call = _sink_call(_func(src), "commit")
    assert _is_commit_call(call)


def test_private_conn_commit_with_parameter_is_parameter_match() -> None:
    """mcploitable's `create_ticket(customer, subject, body)` shape: the
    body's persistence step is a `_conn.commit()` the capability
    extractor does not treat as a write, so only the commit exception
    can carry the data — and it does once the underscore is ignored
    (1.2a.3e)."""
    src = """
    def create_ticket(self, customer, body):
        self._conn.commit()
    """
    assert _sink_body(src) == "parameter_match"


def test_private_conn_commit_without_parameters_is_unknown() -> None:
    """The 1.2a.3c edge-case rule is unaffected by the normalization: a
    parameterless sink has no parameter-derived state to persist."""
    src = """
    def flush(self):
        self._conn.commit()
    """
    assert _sink_body(src) == "unknown"


def test_private_commit_does_not_outrank_a_literal_only_body() -> None:
    """`self._db.add("literal")` + `self._conn.commit()` — the write is a
    constant, the commit still counts (the accepted 1.2a.3c
    consequence), now also for a private qualifier."""
    src = """
    def seed(self, tag):
        self._db.add("literal")
        self._conn.commit()
    """
    assert _sink_body(src) == "parameter_match"




def test_subprocess_run_with_empty_string_stays_literal_only() -> None:
    """A commit-shaped exception must not widen literal calls (1.2a.3c)."""
    src = """
    def run_task(self):
        subprocess.run("")
    """
    assert _sink_body(src) == "literal_only"


# ============================================================
# Tool return analyzer (1.2a.1)
# ============================================================

def _returns(source: str) -> bool:
    """Analyze the first function in `source` as a tool body."""
    return analyze_tool_returns(_func(source))


def test_tool_returns_data() -> None:
    src = """
    def read_file(query):
        return search(query)
    """
    assert _returns(src) is True


def test_tool_returns_none_is_false() -> None:
    src = """
    def read_file(query):
        log(query)
        return None
    """
    assert _returns(src) is False


def test_tool_bare_return_is_false() -> None:
    src = """
    def read_file(query):
        log(query)
        return
    """
    assert _returns(src) is False


def test_tool_without_return_is_false() -> None:
    src = """
    def read_file(query):
        log(query)
    """
    assert _returns(src) is False


def test_tool_yield_is_true() -> None:
    """A generator hands data to its caller."""
    src = """
    def read_file(query):
        yield query
    """
    assert _returns(src) is True


def test_tool_yield_from_is_true() -> None:
    src = """
    def read_file(query):
        yield from query
    """
    assert _returns(src) is True


def test_tool_conditional_return_with_data_is_true() -> None:
    """One data return is enough, even when another branch returns None."""
    src = """
    def read_file(query):
        if query:
            return search(query)
        return None
    """
    assert _returns(src) is True


def test_tool_async_return_is_true() -> None:
    src = """
    async def read_file(query):
        return await search(query)
    """
    assert _returns(src) is True


# ============================================================
# Class-body sink analyzer (1.2a.5)
# ============================================================

def _class(source: str) -> ast.ClassDef:
    """Parse `source` and return its first class definition."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            return node
    raise AssertionError("no class found in source")


def _class_sink(source: str) -> str:
    """Analyze the first class in `source` as a sink body."""
    return analyze_sink_class(_class(source))


def test_class_sink_init_parameter_reaches_dangerous_call() -> None:
    """A constructor parameter reaches the dangerous call (1.2a.5).

    The same case spelled as a stored attribute — `self.db.delete(self.q)`
    — is also `parameter_match` since 1.2a.3g; see
    `test_class_sink_self_attribute_argument_is_parameter_match`.
    """
    src = """
    class X:
        def __init__(self, q):
            self.db.delete(q)
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_call_parameter_reaches_dangerous_call() -> None:
    """`__call__`'s parameter reaches `os.remove`: the class is invoked."""
    src = """
    class X:
        def __call__(self, q):
            os.remove(q)
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_async_call_is_analyzed() -> None:
    """`async def __call__` is the same scope (AsyncFunctionDef)."""
    src = """
    class X:
        async def __call__(self, q):
            os.remove(q)
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_literal_only_init() -> None:
    """A constructor whose dangerous call takes a constant only."""
    src = """
    class X:
        def __init__(self):
            self.db.delete("literal")
    """
    assert _class_sink(src) == "literal_only"


def test_class_sink_without_dangerous_call_is_unknown() -> None:
    """`self.q = q` alone is no flow evidence."""
    src = """
    class X:
        def __init__(self, q):
            self.q = q
    """
    assert _class_sink(src) == "unknown"


def test_class_sink_commit_only_init_with_parameter_is_data() -> None:
    """The commit exception applies at class level too (1.2a.3c).

    The constructor declares a parameter, so `self.db.commit()` persists
    the class's data and the class is `parameter_match`. The preceding
    `self.db.delete(self.q)` is `parameter_match` on its own as well
    (1.2a.3g), so the commit is not what decides this value.
    """
    src = """
    class X:
        def __init__(self, q):
            self.q = q
            self.db.delete(self.q)
            self.db.commit()
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_commit_only_init_without_parameter_is_unknown() -> None:
    """The parameterless rule holds for class bodies as well.

    A constructor with no parameters has no parameter-derived state to
    persist, so a bare commit contributes nothing.
    """
    src = """
    class X:
        def __init__(self):
            self.db.commit()
    """
    assert _class_sink(src) == "unknown"


def test_class_sink_self_attribute_argument_is_parameter_match() -> None:
    """`self.db.delete(self.q)` is data when the body stored `q`.

    Changed by 1.2a.3g: the stored attribute used to be `unknown` (the
    1.2a.5 sketch called it a known limitation). The class body is the
    scope, and `self.q` is assigned from the constructor's parameter.
    """
    src = """
    class X:
        def __init__(self, q):
            self.q = q
            self.db.delete(self.q)
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_self_attribute_from_a_literal_is_unknown() -> None:
    """`self.q = "literal"` then `self.q` in the call is `unknown`."""
    src = """
    class X:
        def __init__(self, q):
            self.q = "literal"
            self.db.delete(self.q)
    """
    assert _class_sink(src) == "unknown"


def test_class_sink_attribute_from_another_method_is_unknown() -> None:
    """Each method is its own scope: `__init__` does not help `delete`.

    1.2a.5 analyzes every method separately, so an attribute stored in
    one is not visible in another (1.2a.3g).
    """
    src = """
    class X:
        def __init__(self, q):
            self.q = q
        def delete(self):
            self.db.delete(self.q)
    """
    assert _class_sink(src) == "unknown"


def test_class_sink_fstring_argument_is_parameter_match() -> None:
    """The dvmcp shape, at class level: a formatted URL with the sink's
    parameter (1.2a.3g)."""
    src = """
    class X:
        def fetch(self, q):
            requests.get(f"{BASE_URL}/api/{q}")
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_dict_argument_with_local_var_is_parameter_match() -> None:
    """The `VulnerableChatAssistant` shape: `messages` is a local built
    from the parameter and handed to the sink in a dict (1.2a.3g)."""
    src = """
    class X:
        def chat(self, user_message):
            messages = [{"role": "user", "content": user_message}]
            requests.post(BASE_URL, json={"messages": messages})
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_strongest_scope_wins() -> None:
    """Any `parameter_match` beats a `literal_only`, in either order."""
    literal_then_parameter = """
    class X:
        def __init__(self):
            self.db.delete("literal")
        def __call__(self, q):
            os.remove(q)
    """
    parameter_then_literal = """
    class X:
        def __init__(self, q):
            self.db.delete(q)
        def __call__(self):
            self.db.delete("literal")
    """
    assert _class_sink(literal_then_parameter) == "parameter_match"
    assert _class_sink(parameter_then_literal) == "parameter_match"


def test_class_sink_plain_method_is_analyzed() -> None:
    """Every method counts, not only `__init__`/`__call__` (1.2a.5).

    A class-kind sink is a container of callable entry points; the
    caller invokes `delete(q)` and `q` reaches the dangerous call there.
    """
    src = """
    class X:
        def delete(self, q):
            os.remove(q)
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_repository_style_add_and_commit_is_data() -> None:
    """`claim_username`-style method: the parameter is written and
    committed through the session (FinBot `UserProfileRepository`).
    """
    src = """
    class X:
        def claim_username(self, user_id, username):
            profile = self.db.query(Profile).filter_by(username=username)
            profile.user_id = user_id
            profile.username = username
            self.db.add(profile)
            self.db.commit()
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_execute_method_parameter_reaches_subprocess() -> None:
    """`execute_mcp_tool`-style method: the parameter is the command
    (dvmcp `DVMCPClient`).
    """
    src = """
    class X:
        def execute(self, tool_name, params):
            subprocess.run(params, shell=True)
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_method_literal_only() -> None:
    """A method whose dangerous call takes a constant only
    (mcploitable `Handler.do_GET`-style, literal URL).
    """
    src = """
    class X:
        def do_GET(self):
            urllib.request.urlopen("http://example.com/")
    """
    assert _class_sink(src) == "literal_only"


def test_class_sink_methods_without_dangerous_call_is_unknown() -> None:
    """Methods that never call anything dangerous → unknown."""
    src = """
    class X:
        def get(self, q):
            return q
        def put(self, q):
            self.q = q
    """
    assert _class_sink(src) == "unknown"


def test_class_sink_async_method_is_analyzed() -> None:
    """`async def handle` is a method like any other (AsyncFunctionDef)."""
    src = """
    class X:
        async def handle(self, q):
            os.remove(q)
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_strongest_method_wins() -> None:
    """Any method's `parameter_match` beats another's `literal_only`."""
    src = """
    class X:
        def ping(self):
            self.db.delete("literal")
        def purge(self, q):
            os.remove(q)
    """
    assert _class_sink(src) == "parameter_match"


def test_class_sink_methods_only_literal_only_is_literal_only() -> None:
    """All methods literal → `literal_only`, not `unknown`."""
    src = """
    class X:
        def ping(self):
            self.db.delete("literal")
        def pong(self):
            self.db.delete("other")
    """
    assert _class_sink(src) == "literal_only"


def test_class_sink_class_level_dangerous_call_counts() -> None:
    """A class with no methods: `class X: db = SomeClient();
    db.delete("scratch")` runs at definition time, so the class-level
    scope still contributes its argument.
    """
    src = """
    class X:
        db = SomeClient()
        db.delete("scratch")
    """
    assert _class_sink(src) == "literal_only"


def test_class_sink_class_level_name_argument_is_unknown() -> None:
    """A class with no methods whose only dangerous call is class level
    and takes no parameter → unknown. There are no parameters at class
    level, so a bare name is a global and contributes no evidence
    (ADR-0001)."""
    src = """
    class X:
        db = SomeClient()
        db.delete(CONFIG_PATH)
    """
    assert _class_sink(src) == "unknown"


def test_class_sink_nested_class_is_not_analyzed() -> None:
    """A nested class is a scope of its own — its `__call__` body is not
    this class's data flow. Counting it here would double-count the call
    when the nested class is itself a sink node."""
    src = """
    class X:
        class Inner:
            def __call__(self, q):
                os.remove(q)
    """
    assert _class_sink(src) == "unknown"


def test_class_sink_nested_class_method_is_not_analyzed() -> None:
    """A nested class's ordinary method is excluded the same way: the
    dangerous call belongs to `Inner`, not to `X`."""
    src = """
    class X:
        def ping(self):
            self.db.delete("literal")
        class Inner:
            def delete(self, q):
                os.remove(q)
    """
    assert _class_sink(src) == "literal_only"


def test_class_sink_nested_function_inside_method_counts() -> None:
    """A function nested inside a method is not separated: the method
    body is walked whole, as everywhere else in this module."""
    src = """
    class X:
        def handle(self, q):
            def inner():
                os.remove(q)
            inner()
    """
    assert _class_sink(src) == "parameter_match"


# ============================================================
# Cursor fetch reads (1.2a.3h)
# ============================================================
# A fetch gives `reads_db` (patterns.CAPABILITY_ORM_READS_DB), and
# `reads_db` is deliberately not in `SINK_CAPABILITIES`: a fetch is
# read evidence, not sink evidence. So a fetch never makes a body
# dangerous, and these tests pin that the analyzer stays honest.

def test_fetch_only_body_has_no_sink_evidence() -> None:
    """`cursor.fetchall()` alone is not a dangerous call → `unknown`.

    Such a body is not a sink node at all, so this path is unreachable
    in a scan; the test pins the analyzer's logic, not a corpus shape.
    """
    src = """
    def list_rows(self):
        return cursor.fetchall()
    """
    assert _sink_body(src) == "unknown"


def test_fetch_after_a_read_statement_has_no_sink_evidence() -> None:
    """The realistic shape: `execute("SELECT ...")` + `fetchall()`.
    Both are reads, so neither is a dangerous call."""
    src = """
    def list_rows(self):
        cursor.execute("SELECT * FROM rows")
        return cursor.fetchall()
    """
    assert _sink_body(src) == "unknown"


def test_fetch_does_not_change_a_write_sinks_data_flow() -> None:
    """On a real sink the fetch sits next to the write: it adds
    `reads_db` to the node's capabilities but contributes no argument
    evidence, so the value is still the write's `parameter_match`."""
    src = """
    def insert_and_read(self, rows):
        self.db.add(rows)
        return cursor.fetchall()
    """
    assert _sink_body(src) == "parameter_match"
