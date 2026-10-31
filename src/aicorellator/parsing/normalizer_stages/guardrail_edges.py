"""GuardrailEdgeStage — `guards` (guardrail → component) from source evidence.

Run order: 21 of 21 (see `normalizer_stages.STAGES`) — last.

ADR-0019 §1 (emission). A `guards` edge is emitted only when the
source-level evidence establishes a protection relationship, and B.4a
recognizes three forms of it:

  - **Call site.** The component's body calls a function that is a
    `guardrail` node, recorded in the name-keyed call graph
    (`ctx.entry_points.call_graph`, the same one `entry_to_sink` and
    `delegation_edges` read). A *direct* call, not reachability: a
    component calls its guardrail, it does not reach it through a chain,
    so the test is membership and not the BFS `reachable_functions`
    runs.
  - **Decorator.** The component carries a decorator whose last dotted
    segment is the literal name `guardrail` — `@guardrail`,
    `@guardrail(...)`, `@mcp.guardrail(...)` — the same literal-name
    rule by which `framework_detectors/langchain.py` recognizes `@tool`.
  - **Registration** (`mcp_server`, ADR-0019 rev 1 §1). A `mcp_server`
    is a `guards` target in its own right, and its evidence is the
    middleware's registration on the server: an `add_middleware(...)`
    call site on the server object, or a `@<var>.middleware(...)`
    decorator on a factory that hands the guardrail class back. The read
    is the *server's own module* (`ctx.module_for`), never the call
    graph: `mcp.add_middleware(...)` is an `ast.Attribute` call, and
    `entry_points._collect_call_names` records `ast.Name` targets only
    (ADR-0016). The edge means "this middleware runs on this server",
    which is why a server is a component here at all — a middleware on a
    server protects every tool the server exposes (ADR-0007's table
    names what a guardrail protects in the graph's vocabulary; a
    `mcp_server` is the container those tools live in). The middleware
    reaches a tool-level chain through the payload read (§3 rev 1,
    `evaluator._guardrails_for_chain`), not through traversal.

Three forms are deliberately **not** evidence, and emit nothing
(ADR-0019 §1, "not emitted, deliberately"):

  - **Co-location.** A guardrail and a component in the same file are a
    file layout, not a defensive relationship. Emitting on co-location
    would be a false claim about protection — and, once B.4b weighs
    guardrails into confidence, a false claim that lowers confidence on
    chains no guardrail protects. For the `mcp_server` target this is
    the pinned shape: a `Middleware` subclass in a server's file with no
    registration call is a node and no edge.
  - **A `guardrail` node with no call site and no decorator.** One wired
    in by dependency injection, a framework hook or an import side
    effect is a false negative, and it is accepted: a false negative
    leaves confidence where it was; a false positive moves it down for a
    reason the code does not support.
  - **`self.`-prefixed calls.** `self.pii.check(x)` is not in the call
    graph, which records `ast.Name` calls only
    (`entry_points._collect_call_names`). Widening the call graph is a
    separate item and a precondition for a second pass over ADR-0019.

The direction is `guardrail → component` — the guardrail is the actor,
the component is the object (ADR-0019 §1). The edge is `structural`
(`STRUCTURAL_FLOW`, ADR-0015 decision 5): a declaration, not a step in
an attack path, so `find_chains` never traverses it and this stage
cannot change what is enumerated. `observed_by` keeps the `Edge`
default (`["ast"]`) and `data_flow_confirmed` keeps its schema default
(`NULL`) — a guardrail's presence is not a statement about whether any
value reached it (ADR-0016).

Why its own stage, and why the last slot: the only ordering constraint
is the detector merge (stage 10) — a framework detector or the LLM
classifier can produce the `guardrail` node this resolver links from,
and the merge is what puts it in the delta. `lethal_trifecta` shares
that constraint and nothing else, and this stage shares it too, so it
takes the end of the pipeline, where the node set is final. Running last
also keeps it out of the way of the stages that already carry ordering
tests. `flow_kind` (stage 12) is *not* a constraint either way: it skips
`STRUCTURAL_EDGE_KINDS`, and this stage runs after it regardless.

**A known gap in what this stage can see.** No deterministic classifier
in the tree produces a `guardrail` node today: `classification.py`
answers `sink` / `agent` / `llm_endpoint` from capabilities and nothing
else (ADR-0016 decision 7 removed the name-and-path rule that used to
emit `guardrail`). The nodes this resolver links from arrive through
`ctx.framework_detection` — the LLM classifier's `DetectionResult`
(D.2) can name `guardrail` as a kind — and are merged into the delta by
`detector_merge`. The resolver reads them from `delta.nodes`, not from a
per-file index, for the same reason `cross_file_resolution` reads its
tools and sinks from the delta: the merge can replace a node, and the
replacement is what the graph keeps. Which is also why this stage cannot
run before stage 10.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...graph.models import Edge, Node
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import (
    STRUCTURAL_FLOW,
    edge_id,
    node_line_start,
    resolve_definition,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


# The kinds a guardrail protects. ADR-0007 describes the edge as
# "guardrail → component" and its table says "Guardrail protects an
# agent or a tool"; a `mcp_server` is the container those tools live in,
# and ADR-0019 rev 1 §1 adds it as a third target with **its own
# evidence rule** (a registration, not a call from a body). That is not
# "co-location was already allowed": co-location is exactly what rev 1
# refuses. It is a widening because `mcp.add_middleware(...)` is a
# source-level declaration — the same strength as the call-site and
# decorator forms — and the AST sees it.
COMPONENT_KINDS: frozenset[str] = frozenset(
    {"agent", "tool", "mcp_server"}
)

GUARDRAIL_KIND = "guardrail"

# The SDK's registration method, as the `ast.Attribute` it is:
# `mcp.add_middleware(AuditMiddleware())`. ADR-0019 rev 1 §1.
REGISTRATION_METHOD_NAME = "add_middleware"

# The last dotted segment of the decorator form of the same
# registration: `@mcp.middleware(...)`.
MIDDLEWARE_DECORATOR_NAME = "middleware"

# The `resolved_via` value the registration evidence carries. Both forms
# of it — the call site and the decorator — are the ADR's one relation
# ("the middleware is registered on the server"), so they share one
# value rather than inventing two. The other values are `call_graph`
# (the call-site form above) and `decorator` (the literal `@guardrail`
# form), so this is the third form's name, not a redefinition of either.
EVIDENCE_REGISTRATION = "registration"

# The one decorator name B.4a recognizes, by its last dotted segment,
# and the name a `guardrail` node has to carry for the edge to point
# somewhere. Mirrors `framework_detectors/langchain.py::_decorator_name`,
# which recognizes `@tool` by the literal name `tool`: a project whose
# guardrail decorator is called something else (`@pii_guard`) is not
# emitted. That is a known false negative, consistent with ADR-0016's
# "no name tables" posture (ADR-0019 §1).
GUARDRAIL_DECORATOR_NAME = "guardrail"


class GuardrailEdgeStage:
    """`guards` (guardrail → component) from a call site or a decorator."""

    name = "guardrail_edges"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        delta.edges.extend(link_guardrails_to_components(delta, ctx))
        return delta


def link_guardrails_to_components(
    delta: "GraphDelta",
    ctx: NormalizeContext,
) -> list[Edge]:
    """Every `(guardrail, component)` pair the source evidence supports.

    One edge per pair, id-deduplicated: a pair the delta already links
    keeps the edge it has. The guardrail and component nodes are read
    from `delta.nodes` — the stage runs after the merge, and the delta is
    what the graph keeps. A node with no `source_file`, or one whose
    definition is not in its file, contributes no evidence: the resolver
    does not guess.
    """
    guardrails = [n for n in delta.nodes if n.kind == GUARDRAIL_KIND]
    components = [n for n in delta.nodes if n.kind in COMPONENT_KINDS]
    if not guardrails or not components:
        return []

    existing = {edge.id for edge in delta.edges}
    edges: list[Edge] = []
    for guardrail in guardrails:
        for component in components:
            evidence = _evidence(guardrail, component, ctx)
            if not evidence:
                continue
            guard_edge = Edge(
                id=edge_id(guardrail.id, component.id, "guards"),
                source_id=guardrail.id,
                target_id=component.id,
                kind="guards",
                host_id=ctx.host_id,
                # Which forms of evidence resolved the pair —
                # `delegation_edges` carries `resolved_via` for the same
                # reason. Sorted, so the value is deterministic.
                attributes={"resolved_via": ",".join(sorted(evidence))},
                # A declaration, not a path (ADR-0015, decision 5). No
                # data-flow claim either (ADR-0016): spreading
                # `STRUCTURAL_FLOW` leaves `data_flow_confirmed` at its
                # schema default.
                **STRUCTURAL_FLOW,
            )
            if guard_edge.id in existing:
                continue
            existing.add(guard_edge.id)
            edges.append(guard_edge)
    return edges


def _evidence(
    guardrail: Node,
    component: Node,
    ctx: NormalizeContext,
) -> set[str]:
    """The forms of evidence tying `guardrail` to `component`, if any.

    Two rules, by the component's kind. An `agent` or a `tool` is a body
    a guardrail is called from or a definition that carries the
    `@guardrail` decorator, so the call graph and the decorator list are
    read. A `mcp_server` is a container, and its evidence is the
    middleware's registration on it (ADR-0019 rev 1 §1), read from the
    server's own module — the two rules do not mix, because neither
    form of the body rule can fire on a server node.
    """
    if component.kind == "mcp_server":
        if _registered_on_server(guardrail, component, ctx):
            return {EVIDENCE_REGISTRATION}
        return set()
    definition = _definition_for(component, ctx)
    evidence: set[str] = set()
    if _calls_guardrail(guardrail, component, definition, ctx):
        evidence.add("call_graph")
    if _decorated_by_guardrail(guardrail, definition):
        evidence.add("decorator")
    return evidence


def _definition_for(
    component: Node,
    ctx: NormalizeContext,
) -> ast.AST | None:
    """The class or function definition behind a component node, or None.

    A `tool` node is a function or method; an `agent` node is a class.
    Resolution goes through `resolve_definition` with the node's
    `line_start`, the same way `body_attributes` finds the body a node
    came from — a file may define the same name twice.

    Returns None when the node carries no `source_file`, or when the file
    does not define the name at all (a detector-created node in a file
    the manifest does not hold, or a name the TSA index and the file
    index disagree about).

    A `mcp_server` node has no definition either — it names a container,
    not a body — and its evidence is `_registered_on_server`'s module
    scan, so this answers None for it rather than guessing at a function
    or class that happens to share the server's name.
    """
    if component.kind == "mcp_server":
        return None
    file_path = _source_file(component)
    if file_path is None:
        return None
    line_start = node_line_start(component)
    if component.kind == "agent":
        return resolve_definition(
            ctx.classes_for(file_path), component.name, line_start
        )
    return resolve_definition(
        ctx.functions_for(file_path), component.name, line_start
    )


def _body_call_names(
    component: Node,
    definition: ast.AST | None,
) -> frozenset[str]:
    """The call-graph keys whose call set is the component's own body.

    A `tool` node is a function, so its own name is the key
    (`entry_points` records `call_graph[func.name]` per definition). An
    `agent` node is a class: its body is its methods, and the call graph
    keys those by method name, so every method the class defines
    directly is a key. An agent node whose class definition is not
    visible contributes none — the resolver does not fall back to the
    file, because a call anywhere in the file is co-location, not
    evidence about this component.
    """
    if component.kind != "agent":
        return frozenset({component.name})
    if not isinstance(definition, ast.ClassDef):
        return frozenset()
    return frozenset(
        item.name
        for item in definition.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _calls_guardrail(
    guardrail: Node,
    component: Node,
    definition: ast.AST | None,
    ctx: NormalizeContext,
) -> bool:
    """True when the component's body calls the guardrail by bare name.

    Membership, not reachability: the call graph maps a function name to
    the names it calls, so a guardrail one hop away in a *chain* does not
    count — only a call in the component's own body does. Only
    `ast.Name` call targets are recorded, which is what makes
    `self.pii.check(x)` invisible (the accepted false negative of
    ADR-0019 §1).
    """
    call_graph = ctx.entry_points.call_graph
    for key in _body_call_names(component, definition):
        if guardrail.name in call_graph.get(key, ()):
            return True
    return False


def _decorated_by_guardrail(
    guardrail: Node,
    definition: ast.AST | None,
) -> bool:
    """True when the component carries the literal `@guardrail` decorator.

    Two conditions, both required: the decorator's last dotted segment is
    `guardrail`, and a `guardrail` node carries that name — the second is
    what makes the edge point at a node rather than at a string. A
    decorator named anything else emits nothing, even when a `guardrail`
    node shares its name (ADR-0019 §1).
    """
    if guardrail.name != GUARDRAIL_DECORATOR_NAME:
        return False
    for decorator in _decorators(definition):
        if _decorator_name(decorator) == GUARDRAIL_DECORATOR_NAME:
            return True
    return False


def _decorators(definition: ast.AST | None) -> tuple[ast.expr, ...]:
    """The decorator expressions of a class or function definition."""
    if definition is None:
        return ()
    return tuple(getattr(definition, "decorator_list", ()))


def _decorator_name(node: ast.expr) -> str | None:
    """The last dotted segment of a decorator name, or None.

    Mirrors `framework_detectors/langchain.py::_decorator_name`:
    `@guardrail`, `@guardrail(...)`, `@mcp.guardrail` and
    `@mcp.guardrail(...)` all answer `guardrail`. Any other expression
    (a subscript, a lambda) answers None.
    """
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _source_file(node: Node) -> str | None:
    """The relative path a node was found in, when it carries one."""
    value = node.attributes.get("source_file")
    return value if isinstance(value, str) else None


# ============================================================
# The `mcp_server` target (ADR-0019 rev 1 §1)
# ============================================================

def _registered_on_server(
    guardrail: Node,
    component: Node,
    ctx: NormalizeContext,
) -> bool:
    """True when `guardrail` is registered on the server `component`.

    Either form the ADR names (ADR-0019 rev 1 §1): the SDK's
    `add_middleware(...)` call site, or a `@<var>.middleware(...)`
    decorator on a factory that hands the guardrail class back. Both are
    read from the server's *own* module: a registration in some other
    file is not evidence about this server, and co-location — a
    middleware class in this file with no registration — is not evidence
    at all (the pinned negative, `N4`).
    """
    module = _server_module(component, ctx)
    if module is None:
        return False
    if _has_registration_call(module, guardrail.name):
        return True
    return _has_middleware_decorator(module, guardrail.name)


def _server_module(
    component: Node,
    ctx: NormalizeContext,
) -> ast.Module | None:
    """The parsed module an `mcp_server` node stands for, or None."""
    file_path = _server_file(component, ctx)
    if file_path is None:
        return None
    return ctx.module_for(file_path)


def _server_file(component: Node, ctx: NormalizeContext) -> str | None:
    """The manifest path of the module an `mcp_server` node stands for.

    Two producers, two shapes. A framework detector's `mcp_server` node
    carries the `source_file` it was found in (`fastmcp.py`,
    `mcp_python.py`), so the node's own attribute answers. A
    convention node (`mcp_server_nodes.McpServerNodeStage`, one per
    `mcp/servers/<name>/server.py` path) carries only `server_name`, so
    the file is the manifest file whose `classify_file` yields that
    name — the same rule that made the node, rather than a second copy
    of the path convention here. None when neither answers: the
    resolver does not guess a file.
    """
    file_path = _source_file(component)
    if file_path is not None:
        return file_path
    from ..classification import classify_file

    for entry in ctx.manifest.files:
        classification = classify_file(entry.path)
        if classification is None:
            continue
        if classification.attributes.get("server_name") == component.name:
            return entry.path
    return None


def _has_registration_call(module: ast.Module, name: str) -> bool:
    """True when the module registers `name` with `add_middleware(...)`.

    The shape is the SDK's: an `ast.Call` whose function is an
    `ast.Attribute` named `add_middleware` (`mcp.add_middleware(...)`,
    `server.add_middleware(...)`), whose first positional argument names
    the guardrail. The evidence is the module AST, not the name-keyed
    call graph — an attribute call is not recorded there, which is why
    the registration form exists at all (ADR-0016).

    **The receiver is not resolved, and that is the known limit.** Any
    `add_middleware(<guardrail name>)` in the server's module counts, so
    a non-MCP `add_middleware` — Starlette's
    `app.add_middleware(CORSMiddleware, ...)` — would count too, if a
    `guardrail` node carried the argument's name and an `mcp_server`
    node existed in that module. The name must match a node the graph
    already holds, and the call must sit in a server's own file, so the
    pair is narrow; resolving the receiver to the server's own variable
    is a follow-up, not this step. The corpora are the check: eight of
    them, `guards = 0` on all.
    """
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != REGISTRATION_METHOD_NAME:
            continue
        if _registration_argument_name(node) == name:
            return True
    return False


def _registration_argument_name(call: ast.Call) -> str | None:
    """The name in an `add_middleware(...)` call's first argument, or None.

        add_middleware(AuditMiddleware())            → AuditMiddleware
        add_middleware(AuditMiddleware)              → AuditMiddleware
        add_middleware(fastmcp.AuditMiddleware())    → AuditMiddleware
        add_middleware(middleware=AuditMiddleware()) → None
        add_middleware()                             → None

    Only the first *positional* argument is read: that is where the SDK's
    signature puts the middleware, and a keyword form would need a
    parameter-name table the ADR does not give (the same posture as
    ADR-0016's "no name tables"). The class itself and a construction of
    it both count, because both name the same middleware.
    """
    if not call.args:
        return None
    argument = call.args[0]
    if isinstance(argument, ast.Call):
        argument = argument.func
    if isinstance(argument, ast.Name):
        return argument.id
    if isinstance(argument, ast.Attribute):
        return argument.attr
    return None


def _has_middleware_decorator(module: ast.Module, name: str) -> bool:
    """True when a `@<var>.middleware(...)` factory hands `name` back.

    The decorator form registers a *factory*, and the FastMCP middleware
    detector names its `guardrail` node after the class, not the factory
    (`class AuditMiddleware(Middleware)` → `AuditMiddleware`). So the
    decorator alone cannot tie the two by name: the factory's body has
    to return the guardrail. That read is one level — the factory's own
    `return` statements, never a nested function's.
    """
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(
            _decorator_name(decorator) == MIDDLEWARE_DECORATOR_NAME
            for decorator in node.decorator_list
        ):
            continue
        if _returns_name(node, name):
            return True
    return False


def _returns_name(function: ast.AST, name: str) -> bool:
    """True when one of `function`'s own returns names `name`.

    `return AuditMiddleware()` and `return AuditMiddleware` both count; a
    factory that returns anything else does not. The walk does not
    descend into a nested function or class, whose `return` belongs to
    that definition (the same one-level read `_body_call_names` makes
    for an agent's methods).
    """
    stack = list(getattr(function, "body", ()))
    while stack:
        node = stack.pop()
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        if isinstance(node, ast.Return):
            value = node.value
            if isinstance(value, ast.Call):
                value = value.func
            if isinstance(value, ast.Name) and value.id == name:
                return True
            continue
        stack.extend(ast.iter_child_nodes(node))
    return False
