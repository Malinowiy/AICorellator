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


def test_fstring_is_unknown() -> None:
    """Interpolated f-strings are not literals (ADR-0015)."""
    src = """
    def purge(base, name):
        os.remove(f"{base}/{name}")
    """
    assert _analyze(src) == "unknown"


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


def test_mixed_collection_argument_is_unknown() -> None:
    """A collection holding a parameter is not `literal_only` — and not
    `parameter_match` either. The collection as a whole is not a
    constant, but we do not know whether the sink consumes the
    parameter element or the literal one, so the honest answer is
    `unknown` (ADR-0001, ADR-0015)."""
    src = """
    def check_tool(param):
        subprocess.run(["a", param])
    """
    assert _analyze(src, "subprocess.run") == "unknown"


def test_nested_collection_argument_is_literal_only() -> None:
    """Collections recurse: all elements constants all the way down."""
    src = """
    def check_nested():
        subprocess.run([["a"], ["b"]])
    """
    assert _analyze(src, "subprocess.run") == "literal_only"


def test_nested_mixed_collection_argument_is_unknown() -> None:
    """A non-constant element one level down defeats the whole
    collection."""
    src = """
    def check_nested(param):
        subprocess.run([["a"], param])
    """
    assert _analyze(src, "subprocess.run") == "unknown"


def test_dict_with_parameter_value_is_unknown() -> None:
    """One non-constant value is enough to make the dict non-constant."""
    src = """
    def check_config(param):
        subprocess.run({"a": param})
    """
    assert _analyze(src, "subprocess.run") == "unknown"


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


def test_sink_mixed_collection_is_unknown() -> None:
    """A collection with a non-literal element is `unknown` — not
    `parameter_match`: the sink's parameter is inside the collection,
    but which element reaches the operation is not known (1.2a.4)."""
    src = """
    def check_tool(self, param):
        subprocess.run([param, "a"])
    """
    assert _sink_body(src) == "unknown"


def test_sink_nested_collection_is_literal_only() -> None:
    """Nested collections of constants are still constant data."""
    src = """
    def check_nested(self):
        subprocess.run([["a"], ["b"]])
    """
    assert _sink_body(src) == "literal_only"


def test_sink_nested_mixed_collection_is_unknown() -> None:
    """A non-literal element one level down defeats the collection."""
    src = """
    def check_nested(self, param):
        subprocess.run([["a"], param])
    """
    assert _sink_body(src) == "unknown"


def test_sink_dict_unpacking_is_unknown() -> None:
    """`{**mapping}` is not a constant collection."""
    src = """
    def check_config(self, param):
        subprocess.run({**param})
    """
    assert _sink_body(src) == "unknown"


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

