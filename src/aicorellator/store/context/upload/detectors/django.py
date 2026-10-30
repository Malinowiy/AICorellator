"""Django uploads — a view that reads `request.FILES` (B.3b).

ADR-0021 §8 (revision 2) makes an entry-point handler that *accepts* an
upload a **context-based writer**: the stores it writes into are its
stores at base confidence 0.5 instead of 0.3. `UploadDetector` is the
interface that answers "does this handler accept an upload" — one module
per framework, ADR-0011's shape — and this is the third implementation,
after `fastapi` (function-level, read from a signature) and `streamlit`
(module-level, read from an entry point). Django is function-level
again, and the marker is the handler's **body** rather than its
signature:

    @router.post("/upload")
    def upload(request):
        f = request.FILES["file"]            → one site
        g = request.FILES.get("file")        → one site

**The route check is not this detector's, and today it is the limit.**
`entry_points.EntryPointStage` decides which functions are routes, and
`entry_points._extract_route` recognizes one shape: a decorator that is
a **call on an attribute** whose last segment is an HTTP method (or
`websocket`), carrying a **literal string path** — `@app.post("/upload")`,
`@router.post("/upload")`. None of Django's own route decorators is that
shape: `@require_POST`, `@require_http_methods([...])` and `@csrf_exempt`
are bare names, `@api_view(["POST"])` (DRF) is a call on a name, and a
class-based view's route lives in `urls.py`. **A view decorated with
Django's own decorators therefore produces no entry-point node, and this
detector — which matches a function's name against the nodes the stage
built — reports nothing for it.** That is a false negative, stated, and
the fix is a Django route shape in `entry_points.py`: a separate step,
and outside this one's change set. What this detector matches is
whatever `EntryPointStage` produced for the file — a node carrying a
`handler`, which is the HTTP/WebSocket route shape and the same shape
`detectors.fastapi` reads — and the fixture in
`tests/test_store_upload_django/test_end_to_end.py` uses a route
decorator the stage does recognize, and says so.

The detector does not invent a route either: a function is a candidate
only when the scan built a node naming it **and** the function carries a
route-shaped decorator (`_is_route_decorator`, `_extract_route`'s shape
restated). On a scan the stage produced, the second condition cannot
subtract anything — the stage records a handler only after it saw such a
decorator, so the two agree by construction; it is here so that a
hand-built node cannot make a helper a writer.

**The upload marker is the handler's body.** `request.FILES[...]` (a
`Subscript`) or `request.FILES.get(...)` (a `Call`), where `request` is a
**parameter of the handler** — Django hands a view its request, so the
parameter is what the marker is read against, and a `request` the module
defines is not the view's. Two limits, stated:

  - **A helper's `request.FILES` is not a marker.** In
    `def upload(request): save(request)` with `request.FILES` in `save`,
    the view identifies no site: the marker is the handler's own body,
    and the upload the helper reads is the helper's detail. The writer's
    BFS already reaches the write sites the helper holds, so the *write*
    side is not lost — what is lost is the site, and with it the base
    0.5. A false negative, and the same one `detectors.fastapi` states
    for `request.form()`.
  - **A nested function's `request.FILES` is the handler's**, because
    the nested function closes over the handler's `request` and its body
    is inside the handler's. The writer's context-in-body signal is a
    separate and narrower reading — a nested write is not the handler's
    own body (`writer._enclosing_functions`) — and the two do not have
    to agree: the site is the view's, the confidence is the write's.

**One site per handler**, not per access: two `request.FILES` accesses in
one view accept one upload, and two sites for it would be two edges into
one store. The `line` is `FunctionDef.lineno` — the view, not the access.

**Function-level only.** A Django **class-based** view is not a site
here, and not because a class is refused: nothing names it.
`UploadView.post` is a function of the module like any other, but a
class-based view reaches its handler through `urls.py`
(`UploadView.as_view()`) or a class-level decorator, so its `post`
carries no route decorator the entry-point stage records, the name match
finds no node, and the view is no writer. Supporting the class as a unit
— `line` = the class, the class body's own writes, and `self.`-calls,
which the call graph does not record (ADR-0016) — is
`B.3b-user_upload-django-followup`'s step; the fixture in
`tests/test_store_upload_django/test_detector.py` pins today's answer,
and pins the decorated-method shape that is *not* a class-based view so
the boundary is a decision rather than an accident.

The sites come back in source order (`FunctionDef.lineno`), so two runs
over one file are identical — the same property `detectors.fastapi`
gives its sites, and the reason the context's dedup is the only thing
that decides between two detectors of one framework.
"""

from __future__ import annotations

import ast

from .....graph.models import Node
from ..base import UploadDetector, UploadSite

#: The registry key, and the `framework` every site is stamped with. The
#: class declares the same name, so the registry's name-vs-class warning
#: (`registry.register_upload_detector`) stays quiet.
NAME = "django"

#: The parameter a Django view is handed, and the attribute the marker
#: hangs off. Both are read as names: `request.FILES` is Django's
#: spelling, and `req.FILES` is a different one this step does not claim
#: (the same limit `detectors.fastapi` states for an import alias).
REQUEST = "request"
FILES = "FILES"

#: `request.FILES.get("file")` — the second spelling of the marker. The
#: attribute's name and not a call target, so `request.FILES.get` read
#: without a call is not an access either.
GET = "get"

#: The decorator attributes `entry_points._extract_route` records a route
#: for — `patterns.HTTP_METHODS` and `patterns.WEBSOCKET_METHOD`,
#: restated rather than imported: `store/` reads no pattern table (the
#: same rule `families.fs` and `detectors.fastapi` follow), and this is a
#: membership test, not logic that drifts.
ROUTE_ATTRIBUTES = frozenset({
    "get", "post", "put", "delete", "patch", "options", "head",
    "websocket",
})


class DjangoUploadDetector(UploadDetector):
    """A Django view whose body reads `request.FILES`."""

    name = NAME

    def detect(
        self,
        tree: ast.Module,
        file_path: str,
        entry_points: list[Node],
    ) -> list[UploadSite]:
        """One site per route handler of `file_path` that accepts a file.

        `entry_points` are the file's entry-point nodes, so a site names
        the node the writer's BFS starts from and the edge's source is
        the same object. A node with no `handler` attribute cannot be
        matched by name and is skipped: a CLI, Streamlit or MCP entry is
        the module, not a function, and `entry_points.EntryPointStage`
        records no handler for one.

        Every function of the module is a candidate, class methods
        included — the route check is the name, and a method's name is
        as unique as a function's — so the body is what decides. A name
        two nodes share yields one site per node, because each node is a
        route the scan found; the writer resolves the same stores either
        way.
        """
        handlers = _nodes_by_handler(entry_points)
        if not handlers:
            return []
        sites: list[UploadSite] = []
        for func in _route_functions(tree):
            nodes = handlers.get(func.name)
            if not nodes or not _accepts_upload(func):
                continue
            sites.extend(
                UploadSite(
                    entry_point=node,
                    file_path=file_path,
                    line=func.lineno,
                    framework=self.name,
                )
                for node in nodes
            )
        return sites


# ------------------------------------------------------------------
# The module's functions
# ------------------------------------------------------------------

def _nodes_by_handler(entry_points: list[Node]) -> dict[str, list[Node]]:
    """`handler` → the entry-point nodes naming it, in the order given.

    `detectors.fastapi._nodes_by_handler`'s rule, restated: a detector
    module is read on its own, and this is an index rather than logic
    that drifts. A node with no `handler` attribute — a CLI, Streamlit or
    MCP entry — is not a function name, so it cannot match one and is
    left out.
    """
    index: dict[str, list[Node]] = {}
    for node in entry_points:
        handler = node.attributes.get("handler")
        if not isinstance(handler, str) or not handler:
            continue
        index.setdefault(handler, []).append(node)
    return index


def _route_functions(
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """The module's route-shaped functions, in source order.

    `ast.walk` is breadth-first, so the list is sorted by `lineno` before
    it is filtered: source order is what makes the detector's answer a
    property of the file and not of a walk. `AsyncFunctionDef` is
    included — a Django view may be `async def`, and the marker does not
    care which it is.
    """
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    found.sort(key=lambda node: node.lineno)
    return [node for node in found if _is_route(node)]


def _is_route(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function carries a route decorator.

    The confirmation and not the decision: `entry_points` is what makes a
    route a route, and this only refuses to claim a function the scan
    never recorded as one (the module docstring states the two
    conditions).
    """
    return any(
        _is_route_decorator(decorator)
        for decorator in func.decorator_list
    )


def _is_route_decorator(decorator: ast.expr) -> bool:
    """True if the decorator is the shape `entry_points` records a route for.

    `_extract_route`'s three conditions, restated: a call, on an
    attribute whose last segment is an HTTP method or `websocket`, whose
    first argument is a literal string path. A bare `@post` and a
    `@require_POST` are names and not calls; `@api_view(["POST"])` is a
    call on a name; `@router.post(path)` with a variable path names no
    path — none of them is a route here, and none of them is a route in
    the entry-point stage either.
    """
    if not isinstance(decorator, ast.Call):
        return False
    if not isinstance(decorator.func, ast.Attribute):
        return False
    if decorator.func.attr not in ROUTE_ATTRIBUTES:
        return False
    if not decorator.args:
        return False
    path = decorator.args[0]
    return isinstance(path, ast.Constant) and isinstance(path.value, str)


# ------------------------------------------------------------------
# The upload marker
# ------------------------------------------------------------------

def _accepts_upload(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the handler's own body reads `request.FILES`.

    Two conditions, and both are needed: the handler takes a parameter
    named `request`, and one of its body statements holds a
    `request.FILES` access. The body is walked statement by statement so
    that a decorator or a default value — which sit on the function but
    outside its body — cannot be the marker, and so that a nested
    function's body (a statement of the handler's, closing over its
    `request`) can.
    """
    if not _takes_request(func):
        return False
    return any(
        _is_files_access(node)
        for statement in func.body
        for node in ast.walk(statement)
    )


def _takes_request(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """True if the handler has a parameter named `request`.

    Positional, positional-only and keyword-only — the three sets a name
    can be declared in. `*args` and `**kwargs` are not, because a view
    does not reach its request through them, which is the reading
    `detectors.fastapi` gives `*args: UploadFile`.
    """
    args = func.args
    names = {
        argument.arg
        for argument in (*args.posonlyargs, *args.args, *args.kwonlyargs)
    }
    return REQUEST in names


def _is_files_access(node: ast.AST) -> bool:
    """True if `node` is `request.FILES[...]` or `request.FILES.get(...)`.

    The two spellings the brief names, and only them. A bare
    `request.FILES` is an attribute read that stores nothing — a view
    that passes the whole multi-file dict on is not claiming one upload —
    and `request.FILES["file"].read()` is the subscript, which the walk
    visits on its own, so a longer chain does not hide it.
    """
    if isinstance(node, ast.Subscript):
        return _is_files_attribute(node.value)
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == GET
            and _is_files_attribute(node.func.value)
        )
    return False


def _is_files_attribute(node: ast.AST) -> bool:
    """True if `node` is `request.FILES`, with `request` the bare name.

    `request.FILES` and nothing else: an attribute of an attribute
    (`self.request.FILES`, a class-based view's spelling) is not read
    here — the class is out of scope, and the marker is the view's own
    parameter name.
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == FILES
        and isinstance(node.value, ast.Name)
        and node.value.id == REQUEST
    )


__all__ = [
    "FILES",
    "GET",
    "NAME",
    "REQUEST",
    "ROUTE_ATTRIBUTES",
    "DjangoUploadDetector",
]
