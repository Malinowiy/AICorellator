"""Shared argument primitives — naming, classification, evidence.

One question, asked by two analyzers: *what is this expression, in terms
of the body it appears in?* A name is a parameter, a local variable, or
neither; a constant is a literal; an attribute, an f-string or a
collection is looked one step into. Nothing here decides whether data
flows — that is the caller's question (`edge_analyzer`,
`sink_analyzer`).

This module is the only place the name-based rules live (ADR-0001). The
one judgment call that differs between callers — whether an attribute of
a parameter counts (1.2a.3j) — stays a keyword-only flag on
`_classify_argument` with a `False` default: the sink analyzer declares
that it classifies a body against its own parameters by passing `True`
at its call sites, the edge analyzer never does, and the choice is
visible where it is made instead of buried in a shared default.

`FunctionNode` lives here because every analyzer needs it and none of
them should import another analyzer to get it.
"""

from __future__ import annotations

import ast


# Tool functions may be sync or async (MCP tools are often `async def`).
# Both node types expose `.args` and are walkable.
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


# ============================================================
# Evidence (step 1)
# ============================================================

def _argument_evidence(arguments: list[ast.expr]) -> tuple[str, ...]:
    """Name-based descriptors of the arguments, for a decision's evidence.

    Never a value: a constant reads `literal`, a call reads `call`, and
    a name reads as itself. The strings exist so a failing test or a
    report can say *why* a decision came out as it did (step 1's
    provenance) without copying program data into the graph.
    """
    return tuple(_describe_argument(arg) for arg in arguments)


def _describe_argument(node: ast.expr) -> str:
    """Short description of one argument expression."""
    if isinstance(node, ast.Await):
        node = node.value
    if isinstance(node, ast.Constant):
        return "literal"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _attribute_base_name(node)
        if base is not None:
            return f"{base}.{node.attr}"
        if _is_self_attribute(node):
            return f"self.{node.attr}"
        return "attribute"
    if isinstance(node, ast.Call):
        return "call"
    if isinstance(node, ast.JoinedStr):
        return "f-string"
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return "collection"
    if isinstance(node, ast.Dict):
        return "mapping"
    return "expression"



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


def _attribute_base_name(node: ast.expr) -> str | None:
    """The leftmost name of an attribute chain, or None (1.2a.3j).

    The whole chain is followed, so the base is the same for
    `file_data.filename` and for `session_context.current_user.id`;
    depth is not limited, because every step is an attribute access on
    the same base name and the base is what carries the evidence
    (ADR-0001: names, not values).

    Returns None when the chain does not start at a plain name —
    `get_config().path` has a call for a base, and a call is a value the
    analyzer does not follow.

    `self.q.name` therefore has base `self`, which matches neither
    `params` nor `locals_` (both discard `self`), so it stays `unknown`:
    1.2a.3j extends the *parameter* case, not the `self` case, and
    `self.q` itself is still handled by the one-level rule in
    `_self_attribute_names` (1.2a.3g).
    """
    base: ast.expr = node
    while isinstance(base, ast.Attribute):
        base = base.value
    return base.id if isinstance(base, ast.Name) else None


def _classify_components(
    elements: list[ast.expr],
    params: set[str],
    locals_: set[str],
    self_attrs: frozenset[str],
    *,
    literal_if_all_constant: bool = True,
    parameter_attributes: bool = False,
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
        _classify_argument(
            element,
            params,
            locals_,
            self_attrs,
            parameter_attributes=parameter_attributes,
        )
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
    *,
    parameter_attributes: bool = False,
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

    1.2a.3j adds a fourth shape, behind the same kind of opt-in:

        `param.x`    an attribute whose chain starts at a parameter, or
        `local.x`    at a local variable of this body, is
                     `parameter_match` (`_attribute_base_name`), at any
                     depth. `obj.x` for any other base — a module-level
                     import, a global, `self` — stays `unknown`.

    `parameter_attributes` defaults to False for the same reason
    `self_attrs` does: the edge analyzer (`analyze_has_access_to`) asks a
    different question — whether a *tool* passes data into a sink — and
    its `data_flow_confirmed` values are a published edge attribute
    (1.2a.1). Only `analyze_sink_body`, which classifies a body against
    its own parameters, opts in.
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
    #
    # 1.2a.3j: `file_data.filename` — any attribute reached through a
    # name that is a parameter or a local variable of this body. The
    # base name decides, at any depth (`session_context.current_user.id`
    # is the same shape as `file_data.filename`); everything else, in
    # particular an attribute of a module-level global, stays `unknown`.
    #
    # A local's value is deliberately not checked: `config = load(param)`
    # and `config = {"path": "/tmp"}` classify alike, because a local is
    # bound in this body and `_local_variable_names` follows names, not
    # values (ADR-0001). `self.X` stays stricter — it needs an assignment
    # to `X` from a parameter or a local (1.2a.3g) — because there the
    # name alone says nothing until the body stores something in it.
    if isinstance(node, ast.Attribute):
        if _is_self_attribute(node) and node.attr in self_attrs:
            return "parameter_match"
        if parameter_attributes:
            base = _attribute_base_name(node)
            if base is not None and (base in params or base in locals_):
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
            parameter_attributes=parameter_attributes,
        )

    # Constant collections are literals for our purposes (1.2a.4); a
    # collection with a parameter-derived element is a match (1.2a.3g).
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return _classify_components(
            node.elts,
            params,
            locals_,
            self_attrs,
            parameter_attributes=parameter_attributes,
        )

    if isinstance(node, ast.Dict):
        # `{**mapping}` is not a value that can be read: `ast.Dict.keys`
        # marks each unpacking with `None` (1.2a.4). Keys are not
        # classified — the sink consumes the values.
        if any(key is None for key in node.keys):
            return "unknown"
        return _classify_components(
            list(node.values),
            params,
            locals_,
            self_attrs,
            parameter_attributes=parameter_attributes,
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
