"""Flow Analyzer — name-based data-flow classification.

ADR-0015 introduces `flow_kind` on every edge and `data_flow_confirmed`
on `has_access_to`. This module answers *how* data moves.

Edge question — *how* does data reach a sink call invoked inside a tool
body?

    analyze_has_access_to(tool_func, sink_call) -> str

Node questions (1.2a.1) — the tool and the sink are often colocated in
a file without the tool calling the sink by name. Then the useful
questions are asked of each body separately:

    analyze_sink_body(sink_func)    -> str   ("sink_data_flow")
    analyze_sink_class(sink_cls)    -> str   ("sink_data_flow")
    analyze_tool_returns(tool_func) -> bool  ("returns_data")

A capability-based sink can be a class rather than a function (1.2a.5):
`UserProfileRepository` writes to a database, `DVMCPClient` reaches the
network — the class is the sink. Such a class is a container of
callable entry points, so `analyze_sink_class` asks the sink question of
every method it defines, plus its class-level statements. A class node
has no body of its own, so the function variant cannot see it.

A tool that returns data and a sink that accepts data make a data flow
*through the agent* plausible even when the `has_access_to` edge is
`control`. The attributes feed the prompt; they do not change chain
enumeration or `flow_kind`.

The result of `analyze_has_access_to` is one of six values:

    parameter_match     an argument is a parameter of the tool function
    local_var_match     an argument is a local variable of the tool
    call_result_match   an argument is the result of another call
    literal_only        every argument is a literal constant
    no_args             the sink call takes no arguments
    unknown             otherwise — an argument could not be classified

The first rule that matches wins, in the order above (with `no_args`
checked first and `unknown` last); the ranking is not linear.

This is deliberately name-based, not value-based (ADR-0001 forbids full
dataflow analysis). It does not follow data through calls or control
flow: a subscript, a binop, or an attribute of another object falls back
to `unknown`. False negatives are accepted; the LLM calibrates
confidence from the result (ADR-0015).

Three shapes are looked one step into (1.2a.3g), because there the body
being analyzed is the one *constructing* the value:

    self.q      an attribute on `self` assigned from a parameter or a
                local variable earlier in the same body
    f"{q}/x"    an f-string with a parameter-derived interpolation
    {"k": q}    a collection with a parameter-derived element or value

They return the kind of the name inside them, so
`self.q = q; self.db.delete(self.q)` and
`requests.post(f"{self.base}/chat", json={"m": messages})` are
recognized. Only names are followed — never call results — and only
within one body: the same `self.q` read in another method is `unknown`.

A literal is a constant or a collection of constants: the command list
of `subprocess.run(["docker", "info"])` is constant data, so it is
`literal_only`, not `unknown` (1.2a.4). A collection is classified by
its elements, so `["a", param]` is `parameter_match` — the parameter is
inside the value the sink is handed (1.2a.3g, loosening the earlier
"mixed collection is `unknown`" rule).

A commit call is the one exception to the no-argument rule (1.2a.3c).
`db.commit()` takes no arguments and is still the dangerous operation:
it persists the session's state, which is the result of the writes
earlier in the body, so the sink's data does reach the database through
it. `subprocess.run()` with no arguments stays `no_args` — a bare
`subprocess.run()` is genuinely safe.

The function is pure: it takes AST nodes and returns a string, with no
filesystem or global state. The normalizer supplies the nodes.
"""

from __future__ import annotations

import ast

from ..patterns import ORM_COMMIT_CALLS, SINK_CAPABILITIES
# The Capability Extractor owns the call-site patterns (ADR-0009). The
# sink analyzer reuses its classifier so "dangerous call" means exactly
# the same thing as "the call that gave the node a sink capability", and
# reuses its pattern matcher so a commit call is recognized by the same
# trailing-segment rule as every other call-site pattern (1.2a.3c).
# Importing the private helpers follows the convention the framework
# detectors already use (`from ..capability_extractor import _scan_function`).
from .capability_extractor import _classify_call, _dotted_name, _matches_any


# Tool functions may be sync or async (MCP tools are often `async def`).
# Both node types expose `.args` and are walkable.
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


# The six values of `data_flow_confirmed` (ADR-0015). Mirrors the CHECK
# constraint on edges.data_flow_confirmed in graph/schema.sql.
DATA_FLOW_CONFIRMED_VALUES: frozenset[str] = frozenset({
    "parameter_match",
    "local_var_match",
    "call_result_match",
    "literal_only",
    "no_args",
    "unknown",
})

# The subset that confirms data flow — `flow_kind = "data"` (ADR-0015,
# decision 3). The rest mean data flow was not confirmed: `control`.
DATA_FLOW_DATA_VALUES: frozenset[str] = frozenset({
    "parameter_match",
    "local_var_match",
    "call_result_match",
})

# The three values of `sink_data_flow` on a sink node (1.2a.1). The
# three data kinds of `has_access_to` collapse into `parameter_match`:
# from the sink's own body, any argument that is not a literal comes
# from the sink's parameters or its internal state.
SINK_DATA_FLOW_VALUES: frozenset[str] = frozenset({
    "parameter_match",
    "literal_only",
    "unknown",
})


def analyze_sink_body(sink_func: FunctionNode) -> str:
    """Classify how a sink passes its own data to a dangerous call.

    Args:
        sink_func: the body of a node classified as `sink`.

    Returns:
        `"parameter_match"`, `"literal_only"`, or `"unknown"`.

    The question is not "does a tool call this sink?" but "does this
    sink accept data?". From the sink's own perspective every argument
    that is not a literal is derived from the sink's parameters or its
    internal state, so the three data kinds of `has_access_to`
    (`parameter_match`, `local_var_match`, `call_result_match`) all
    collapse into `parameter_match`.

    Ranking — the strongest result across every dangerous call wins:

        no dangerous call in the body          -> unknown
        any dangerous call takes a non-literal -> parameter_match
        every dangerous argument is a literal  -> literal_only
        otherwise                              -> unknown

    A dangerous call with no arguments carries no data in either
    direction, so it neither confirms nor denies flow; it is skipped.
    `unknown` therefore means "no argument-level evidence", not "the
    body was not read".

    Exception (1.2a.3c): a commit. `db.commit()` persists the session's
    accumulated state, and that state is the result of the writes
    earlier in the body — the sink's data arrives at the database
    through the session, not through the call's arguments. A bare
    commit therefore counts as data reaching the dangerous call and
    returns `parameter_match`, provided the sink declares at least one
    parameter. A sink with no parameters (`def flush(self):`) has no
    parameter-derived state to persist, so its commit contributes
    nothing and the body stays `unknown` — `parameter_match` would
    claim evidence that does not exist. The exception is confined to
    `patterns.ORM_COMMIT_CALLS`; `subprocess.run()` with no arguments
    is still skipped.

    A `literal_only` sink cannot receive attacker data through its own
    arguments — only constants reach the dangerous call. Like
    `analyze_has_access_to` this is name-based (ADR-0001): values are
    never followed, so subscripts, call results, and attributes of other
    objects fall back to `unknown`. A stored attribute (`self.q = q`),
    an f-string, and a collection are looked one step into (1.2a.3g),
    because here the body under analysis is the one that built the
    value. False negatives are accepted (ADR-0015).
    """
    dangerous = _dangerous_calls(sink_func)
    # 1.2a.3c: a commit the capability extractor does not already treat
    # as dangerous (`repo.commit`, `conn.commit`, ...) still carries the
    # commit exception, so it joins the evidence set. `call not in
    # dangerous` is an identity check — AST nodes do not compare equal.
    dangerous.extend(
        call for call in _commit_calls(sink_func) if call not in dangerous
    )
    if not dangerous:
        return "unknown"

    params = _parameter_names(sink_func)
    locals_ = _local_variable_names(sink_func)
    self_attrs = _self_attribute_names(sink_func)
    saw_literal_only = False

    for call in dangerous:
        arguments: list[ast.expr] = list(call.args)
        arguments.extend(kw.value for kw in call.keywords)
        if not arguments:
            # The commit exception (1.2a.3c). A bare commit persists the
            # session, so a sink that takes parameters has data reaching
            # the dangerous call through the session's state. A
            # parameterless sink has no such data, and every other
            # no-argument dangerous call is skipped as before.
            if params and _is_commit_call(call):
                return "parameter_match"
            continue

        kinds = [
            _classify_argument(arg, params, locals_, self_attrs)
            for arg in arguments
        ]
        if any(kind in DATA_FLOW_DATA_VALUES for kind in kinds):
            return "parameter_match"
        if all(kind == "literal_only" for kind in kinds):
            saw_literal_only = True

    return "literal_only" if saw_literal_only else "unknown"


def analyze_sink_class(cls: ast.ClassDef) -> str:
    """Classify how a class-kind sink passes its own data to a dangerous call.

    Args:
        cls: the `ast.ClassDef` of a node classified as `sink`.

    Returns:
        `"parameter_match"`, `"literal_only"`, or `"unknown"`.

    A capability-based sink can be a class. `UserProfileRepository`,
    `DVMCPClient` and `VulnerableChatAssistant` are sinks because their
    bodies write to a database, execute a shell or reach the network —
    not because of a function named like the node. Such a node has no
    function body, so `analyze_sink_body` cannot see it and
    `sink_data_flow` was `unknown` by construction (1.2a.5).

    The class variant analyzes the scopes a caller can drive:

        every method  the class's callable entry points — a caller
                      invokes `claim_username`, `execute_mcp_tool`,
                      `do_GET`, ... and *that* is where the caller's
                      data meets the dangerous call
        class level   statements executed at definition time

    A class is a container of entry points, so the value means "at least
    one method of this class can pass data to a dangerous call" — which
    is the sink question the prompt asks of a class node. `__init__` and
    `__call__` are methods like any other; the earlier
    `__init__`/`__call__`-only rule missed `UserProfileRepository`,
    `DVMCPClient` and `Handler` entirely, because their constructors
    only assign `self.db` (1.2a.5 rework).

    Each scope goes through `analyze_sink_body`, so the argument ranking
    and the commit exception (1.2a.3c) apply to method bodies exactly as
    they do to functions. The results merge with the same non-linear
    ranking used everywhere else: any `parameter_match` wins, else any
    `literal_only`, else `unknown`.

    Nested classes are not analyzed: their methods belong to the nested
    class, which is a sink node of its own. A function nested inside a
    method is not separated — `analyze_sink_body` walks the whole method
    body, matching the rest of this module.

    A class with no methods and no class-level dangerous call is
    `unknown` — the same fallback as a sink whose body cannot be
    resolved.

    This stays name-based (ADR-0001): a stored attribute (`self.q`) is
    still an attribute chain and classifies as `unknown`, so a
    repository that forwards only `self.<attr>` to a dangerous call
    remains `unknown` (attribute-based data flow is backlog 1.2a.3g).
    """
    results: list[str] = [
        analyze_sink_body(method) for method in _methods(cls)
    ]
    scope = _class_level_scope(cls)
    if scope is not None:
        results.append(analyze_sink_body(scope))

    if "parameter_match" in results:
        return "parameter_match"
    if "literal_only" in results:
        return "literal_only"
    return "unknown"


def analyze_tool_returns(tool_func: FunctionNode) -> bool:
    """True if `tool_func` returns data to its caller.

    A tool that hands data back to the calling agent can carry the
    agent's data onward — the second half of the "potential data flow
    through the agent" signal (1.2a.1).

    `return <value>` counts. A bare `return`, `return None`, and a body
    with no `return` at all all mean "nothing is handed back". `yield`
    and `yield from` count: the caller receives a generator, which is
    data.

    Nested scopes are not separated — an inner function's `return`
    counts, matching `_local_variable_names` (acceptable for name-based
    analysis, ADR-0001).
    """
    for node in ast.walk(tool_func):
        if isinstance(node, ast.Return):
            value = node.value
            if value is None:
                continue
            if isinstance(value, ast.Constant) and value.value is None:
                continue
            return True
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return True
    return False

def analyze_has_access_to(
    tool_func: FunctionNode,
    sink_call: ast.Call,
) -> str:
    """Classify how data reaches `sink_call` inside `tool_func`.

    Args:
        tool_func: the function (or method) whose body contains the call.
        sink_call: the call to the sink node inside that body.

    Returns:
        One of the six `data_flow_confirmed` values.

    The ranking is not linear. The first rule that matches wins:

        no arguments at all              -> no_args
        any argument is a tool parameter -> parameter_match
        any argument is a local variable -> local_var_match
        any argument is a call result    -> call_result_match
        all arguments are literals       -> literal_only
        otherwise                        -> unknown

    `literal_only` is a universal, not an existential: it requires
    *every* argument to be a literal (a constant collection counts as a
    literal — 1.2a.4). A single unresolved argument means data may flow
    through it, so the result is `unknown` even when another argument
    is a literal.

    The call is assumed to be inside `tool_func`; the function does not
    verify this. It reads nothing outside the two AST nodes.
    """
    arguments: list[ast.expr] = list(sink_call.args)
    arguments.extend(kw.value for kw in sink_call.keywords)

    if not arguments:
        return "no_args"

    params = _parameter_names(tool_func)
    locals_ = _local_variable_names(tool_func)

    kinds = [_classify_argument(arg, params, locals_) for arg in arguments]

    if "parameter_match" in kinds:
        return "parameter_match"
    if "local_var_match" in kinds:
        return "local_var_match"
    if "call_result_match" in kinds:
        return "call_result_match"
    if all(kind == "literal_only" for kind in kinds):
        return "literal_only"
    return "unknown"


# ============================================================
# Dangerous calls
# ============================================================

def _dangerous_calls(func: FunctionNode) -> list[ast.Call]:
    """Calls in `func` that exercise a sink capability.

    A dangerous call is one the Capability Extractor would credit with
    a capability in `patterns.SINK_CAPABILITIES` — `os.remove(p)`,
    `subprocess.run(cmd, shell=True)`, `cursor.execute("DELETE ...")`,
    `open(p, "w")`, `requests.post(url, ...)`. Reusing the extractor's
    classifier keeps one definition of "dangerous" in the codebase
    (ADR-0009) and keeps the analyzer name-based (ADR-0001).
    """
    calls: list[ast.Call] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if _classify_call(node) & SINK_CAPABILITIES:
            calls.append(node)
    return calls


def _is_commit_call(call: ast.Call) -> bool:
    """True if `call` is a commit on a database session (1.2a.3c).

    `self.db.commit()` and `conn.commit()` are the same operation under
    different qualifiers. The names are data (`patterns.ORM_COMMIT_CALLS`)
    and are matched with the Capability Extractor's trailing-segment rule,
    so `self.db.commit` matches `db.commit` while a bare `commit()`
    matches nothing.
    """
    dotted = _dotted_name(call.func)
    if dotted is None:
        return False
    return _matches_any(dotted, ORM_COMMIT_CALLS)


def _commit_calls(func: FunctionNode) -> list[ast.Call]:
    """Calls in `func` to a recognized commit method (1.2a.3c).

    Deliberately separate from `_dangerous_calls`, which answers a
    capability question: "does this call exercise a sink capability?".
    `db.commit` and `session.commit` are answers to both, because they
    are ORM writes; `repo.commit`, `conn.commit` and the other
    qualifiers are answers only to this one, because a commit is not by
    itself evidence of a write. `analyze_sink_body` unions the two.
    """
    return [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call) and _is_commit_call(node)
    ]


# ============================================================
# Argument classification
# ============================================================

def _is_literal(node: ast.expr | None) -> bool:
    """True if `node` is a literal or a constant collection (1.2a.4).

    A collection is a literal when all of its elements are literals, so
    `["docker", "info"]` is as constant as `"docker"`. Collections
    recurse: `[["a"], ["b"]]` is constant, `[["a"], param]` is not. An
    empty collection is constant (`all()` over nothing).

    `None` is not constant: it is how `ast.Dict.keys` represents the
    `{**mapping}` unpacking form, which is not a constant.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal(element) for element in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            _is_literal(element) for element in (*node.keys, *node.values)
        )
    return False


def _is_self_attribute(node: ast.expr) -> bool:
    """True if `node` is an attribute of the bound instance (`self.x`).

    `cls.x` and `other.x` do not count: the analyzer only follows state
    the body itself stored on `self` (1.2a.3g).
    """
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _classify_components(
    elements: list[ast.expr],
    params: set[str],
    locals_: set[str],
    self_attrs: frozenset[str],
    *,
    literal_if_all_constant: bool = True,
) -> str:
    """Classify the parts of a composite expression as one value.

    The strongest part wins, with the same ranking as the call site:
    `parameter_match`, else `local_var_match`, else `literal_only` when
    every part is a constant, else `unknown`. A part that cannot be
    resolved (a subscript, a call result, an attribute of another
    object) contributes nothing — `[fetch(q)]` stays `unknown`, because
    following a call is dataflow, not naming (ADR-0001).

    `literal_if_all_constant=False` is for f-strings: their parts are
    interpolations, and an f-string is never constant data even when
    every interpolation is (`f"{3}"`), so the weakest result there is
    `unknown` (deliberate, 1.2a.3g).

    This is where 1.2a.4's "a collection that mixes a parameter and a
    literal is `unknown`" rule is loosened (1.2a.3g): a collection with
    a parameter inside it does carry parameter-derived data, and the
    body under analysis is the one that built it, so the parameter is
    evidence that the sink received data.
    """
    if literal_if_all_constant and all(
        _is_literal(element) for element in elements
    ):
        return "literal_only"
    kinds = [
        _classify_argument(element, params, locals_, self_attrs)
        for element in elements
    ]
    if "parameter_match" in kinds:
        return "parameter_match"
    if "local_var_match" in kinds:
        return "local_var_match"
    return "unknown"


def _classify_argument(
    node: ast.expr,
    params: set[str],
    locals_: set[str],
    self_attrs: frozenset[str] = frozenset(),
) -> str:
    """Classify a single argument expression.

    Name-based, one level deep (ADR-0001, ADR-0015): a name is a
    parameter, a local variable, or neither — values are never followed.

    Three shapes are looked into, because the body being analyzed is the
    one that built them (1.2a.3g):

        `self.q`     parameter-derived when `q` is in `self_attrs`,
                     i.e. when this body assigned it from a parameter or
                     a local variable (`_self_attribute_names`)
        `f"{q}/x"`   the interpolations are classified recursively
        `[q, "x"]`   the elements — or a dict's values — are classified
                     recursively

    `self_attrs` defaults to empty, so a caller that does not build the
    set (the edge analyzer, 1.2a.3g scope) still sees `self.q` as
    `unknown`, exactly as before.
    """
    # `await foo()` is still a call result.
    if isinstance(node, ast.Await):
        node = node.value

    if isinstance(node, ast.Call):
        return "call_result_match"

    if isinstance(node, ast.Name):
        if node.id in params:
            return "parameter_match"
        if node.id in locals_:
            return "local_var_match"
        # A global, import, or builtin — not tracked.
        return "unknown"

    if isinstance(node, ast.Constant):
        return "literal_only"

    # 1.2a.3g: `self.q` where `q` was stored on the instance earlier in
    # the same body. An attribute that is not on `self`, or on `self`
    # but never assigned in this body, stays `unknown`.
    if isinstance(node, ast.Attribute):
        if _is_self_attribute(node) and node.attr in self_attrs:
            return "parameter_match"
        return "unknown"

    # An f-string is never `literal_only`: formatting is evaluated at
    # runtime even when every interpolation is a constant.
    if isinstance(node, ast.JoinedStr):
        return _classify_components(
            [
                part.value
                for part in node.values
                if isinstance(part, ast.FormattedValue)
            ],
            params,
            locals_,
            self_attrs,
            literal_if_all_constant=False,
        )

    # Constant collections are literals for our purposes (1.2a.4); a
    # collection with a parameter-derived element is a match (1.2a.3g).
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return _classify_components(node.elts, params, locals_, self_attrs)

    if isinstance(node, ast.Dict):
        # `{**mapping}` is not a value that can be read: `ast.Dict.keys`
        # marks each unpacking with `None` (1.2a.4). Keys are not
        # classified — the sink consumes the values.
        if any(key is None for key in node.keys):
            return "unknown"
        return _classify_components(
            list(node.values), params, locals_, self_attrs
        )

    # Subscript, binop, starred, comprehension, ... — name-based
    # analysis cannot follow these (ADR-0001, ADR-0015).
    return "unknown"


def _parameter_names(func: FunctionNode) -> set[str]:
    """All declared parameter names of a function.

    `self` and `cls` are excluded: they are the bound instance/class,
    not attacker-supplied data, and passing them to a sink is not a
    data-flow signal.
    """
    names: set[str] = set()
    a = func.args
    for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
        names.add(arg.arg)
    if a.vararg is not None:
        names.add(a.vararg.arg)
    if a.kwarg is not None:
        names.add(a.kwarg.arg)

    names.discard("self")
    names.discard("cls")
    return names


def _local_variable_names(func: FunctionNode) -> set[str]:
    """Names assigned inside the function body.

    Collects every `ast.Name` in a Store context: plain assignments,
    `for` targets, `with ... as`, walrus expressions, and comprehension
    targets. Nested scopes are not separated — a name bound in an inner
    function still counts, which is acceptable for name-based matching.
    """
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)

    names.discard("self")
    names.discard("cls")
    return names


def _self_attribute_names(func: FunctionNode) -> frozenset[str]:
    """Attributes on `self` that hold parameter-derived data (1.2a.3g).

    `self.q = q; self.db.delete(self.q)` stores a parameter on the
    instance and hands it to the dangerous call one line later. Values
    cannot be followed (ADR-0001), but *names* can: an attribute is
    parameter-derived when the right-hand side of an assignment to it is

        a parameter or a local variable   `self.q = q`
        another derived attribute         `self.r = self.q`

    The second rule is applied until nothing changes, so a chain of any
    length is followed — it is the same rule, and stopping after one hop
    would be an arbitrary cutoff. Nothing else counts: `self.q = fetch(q)`
    is not derived, because following a call is dataflow, not naming.

    Assignments are collected from the whole body, so order is not
    modeled (a name is derived if *any* assignment to it is); that
    matches `_local_variable_names`, which is likewise order-blind. The
    scope is one body: `self.q` assigned in `__init__` says nothing about
    `self.q` read in `delete`, because the two are analyzed separately
    (1.2a.5). `self.q: str = q` and `self.q += q` count as assignments.
    """
    assignments: list[tuple[str, ast.expr]] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            assignments.extend(
                (target.attr, node.value)
                for target in node.targets
                if _is_self_attribute(target)
            )
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None and _is_self_attribute(node.target):
                assignments.append((node.target.attr, node.value))
        elif isinstance(node, ast.AugAssign):
            if _is_self_attribute(node.target):
                assignments.append((node.target.attr, node.value))

    params = _parameter_names(func)
    locals_ = _local_variable_names(func)
    derived: set[str] = set()
    progress = True
    while progress:
        progress = False
        for attr, value in assignments:
            if attr in derived:
                continue
            if isinstance(value, ast.Name):
                is_derived = value.id in params or value.id in locals_
            elif _is_self_attribute(value):
                is_derived = value.attr in derived
            else:
                is_derived = False
            if is_derived:
                derived.add(attr)
                progress = True

    return frozenset(derived)


# ============================================================
# Class bodies (1.2a.5)
# ============================================================

# Name of the synthetic function that stands in for a class's
# class-level statements. It is never compiled, unparsed, or matched
# against a call-site pattern — it exists only so those statements can
# be handed to `analyze_sink_body` as one function-like scope.
_CLASS_LEVEL_SCOPE_NAME = "<class-level>"


def _methods(cls: ast.ClassDef) -> list[FunctionNode]:
    """The class's own method definitions, in class-body order.

    Every method counts, whatever its name (1.2a.5 rework). A
    capability-based sink is a container of callable entry points, and
    the dangerous call receives the caller's data in the method the
    caller invokes — `UserProfileRepository.claim_username`,
    `DVMCPClient.execute_mcp_tool`, `Handler.do_GET` — while the
    constructor usually only assigns `self.db`.

    Only direct members count: a function nested inside a method, and a
    nested class's methods, belong to another scope. When a name is
    defined twice the first definition wins, matching `_index_functions`
    in the normalizer. A class with no methods returns an empty list, and
    then only its class-level statements are analyzed.
    """
    found: dict[str, FunctionNode] = {}
    for statement in cls.body:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        found.setdefault(statement.name, statement)
    return list(found.values())


def _class_level_scope(cls: ast.ClassDef) -> FunctionNode | None:
    """The class-level statements of `cls` as a parameterless scope.

    `class X: db = SomeClient(); db.delete("t")` runs at class
    definition time, outside any method, so no method body can see it.
    Wrapping those statements in a synthetic
    `FunctionDef` with no parameters lets `analyze_sink_body` — and with
    it the argument ranking and the commit exception — apply to
    class-level calls unchanged. The node is only ever walked.

    Method definitions and nested classes are excluded: a method body is
    analyzed as itself, and a nested class is a scope of its own, so
    counting its dangerous calls here would double-count a call when
    both classes are sinks.

    Returns None when the class has no class-level statements, in which
    case the class-level scope contributes nothing.
    """
    statements = [
        statement
        for statement in cls.body
        if not isinstance(
            statement,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        )
    ]
    if not statements:
        return None
    return ast.FunctionDef(
        name=_CLASS_LEVEL_SCOPE_NAME,
        args=ast.arguments(
            posonlyargs=[],
            args=[],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=[],
        ),
        body=statements,
        decorator_list=[],
    )

