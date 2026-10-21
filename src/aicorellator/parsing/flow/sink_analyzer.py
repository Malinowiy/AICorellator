"""Sink analyzer — does this sink consume data, and how.

Two shapes of sink, one question:

    analyze_sink_body(func)   a function or method sink (1.2a.1)
    analyze_sink_class(cls)   a class sink (1.2a.5): a class is a
                              container of callable entry points, so
                              every method and the class-level
                              statements are asked the same question

Both return a `FlowDecision` whose `source` says which shape decided
(`sink_body`, `sink_class`), and the normalizer publishes
`decision.kind` as the node's `sink_data_flow` (three values:
`parameter_match`, `literal_only`, `unknown`).

This is the one analyzer that opts into `parameter_attributes=True`
(1.2a.3j): it classifies a body against *its own* parameters, so
`file_data.filename` is parameter-derived by construction. The edge
analyzer never opts in, and the flag is passed here at the call sites
rather than baked into a shared default.

The commit exception (1.2a.3c) lives here too: `db.commit()` with no
arguments still carries the session's accumulated state, so a sink that
declares parameters has data reaching the database through it.
`_dangerous_calls` and `_commit_calls` are deliberately separate — one
answers "does this call exercise a sink capability?", the other "is this
a commit?" — and the analyzer unions the two.
"""

from __future__ import annotations

import ast

from ..capability_extractor import _classify_call, _dotted_name, _matches_any
from ...patterns import ORM_COMMIT_CALLS, SINK_CAPABILITIES
from .arguments import (
    FunctionNode,
    _argument_evidence,
    _classify_argument,
    _local_variable_names,
    _parameter_names,
    _self_attribute_names,
)
from .decision import DATA_KINDS, FlowDecision


# The three values of `sink_data_flow` on a sink node (1.2a.1). The
# three data kinds of `has_access_to` collapse into `parameter_match`:
# from the sink's own body, any argument that is not a literal comes
# from the sink's parameters or its internal state.
SINK_DATA_FLOW_VALUES: frozenset[str] = frozenset({
    "parameter_match",
    "literal_only",
    "unknown",
})


def analyze_sink_body(sink_func: FunctionNode) -> FlowDecision:
    """Classify how a sink passes its own data to a dangerous call.

    Args:
        sink_func: the body of a node classified as `sink`.

    Returns:
        A `FlowDecision` with `source="sink_body"`, whose `kind` is
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
    and since 1.2a.3j so is an attribute of a parameter or of a local
    variable (`file_data.filename`) — this is the one place that opts in
    to it, because only here is the body classified against its own
    parameters. False negatives are accepted (ADR-0015).
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
        return _sink_decision("unknown")

    params = _parameter_names(sink_func)
    locals_ = _local_variable_names(sink_func)
    self_attrs = _self_attribute_names(sink_func)
    saw_literal_only = False
    literal_evidence: tuple[str, ...] = ()

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
                return _sink_decision(
                    "parameter_match", ("commit persists parameter state",)
                )
            continue

        kinds = [
            _classify_argument(
                arg,
                params,
                locals_,
                self_attrs,
                parameter_attributes=True,
            )
            for arg in arguments
        ]
        if any(kind in DATA_KINDS for kind in kinds):
            return _sink_decision(
                "parameter_match", _argument_evidence(arguments)
            )
        if all(kind == "literal_only" for kind in kinds):
            saw_literal_only = True
            literal_evidence = _argument_evidence(arguments)

    if saw_literal_only:
        return _sink_decision("literal_only", literal_evidence)
    return _sink_decision("unknown", ("no argument-level evidence",))


def _sink_decision(
    kind: str,
    evidence: tuple[str, ...] = (),
) -> FlowDecision:
    """Build the sink-body decision. One place, so `source` is stable."""
    return FlowDecision(kind, evidence, source="sink_body")



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


def analyze_sink_class(cls: ast.ClassDef) -> FlowDecision:
    """Classify how a class-kind sink passes its own data to a dangerous call.

    Args:
        cls: the `ast.ClassDef` of a node classified as `sink`.

    Returns:
        A `FlowDecision` with `source="sink_class"`, whose `kind` is
        `"parameter_match"`, `"literal_only"`, or `"unknown"`. The
        evidence names the scope that decided — `method:<name>` or
        `class_body` — because for a class the *which method* question
        is the useful one.

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
    scopes: list[tuple[str, FlowDecision]] = [
        (f"method:{method.name}", analyze_sink_body(method))
        for method in _methods(cls)
    ]
    scope = _class_level_scope(cls)
    if scope is not None:
        scopes.append(("class_body", analyze_sink_body(scope)))

    for kind in ("parameter_match", "literal_only"):
        for name, decision in scopes:
            if decision.kind == kind:
                return FlowDecision(
                    kind,
                    (name, *decision.evidence),
                    source="sink_class",
                )
    return FlowDecision(
        "unknown", ("no scope passed data",), source="sink_class"
    )



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
    defined twice the first definition wins, matching the name fallback 
    in _index_functions. A class with no methods returns an empty list, and
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
