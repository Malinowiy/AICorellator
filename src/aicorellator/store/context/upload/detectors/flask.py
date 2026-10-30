"""Flask uploads — a view that reads `request.files` (B.3b).

ADR-0021 §8 (revision 2) makes an entry-point handler that *accepts* an
upload a **context-based writer**: the stores it writes into are its
stores at base confidence 0.5 instead of 0.3. `UploadDetector` is the
interface that answers "does this handler accept an upload" — one module
per framework, ADR-0011's shape — and this is the fourth and last
implementation, after `fastapi` (function-level, read from a signature),
`streamlit` (module-level, read from an entry point) and `django`
(function-level, read from a body). Flask is `django`'s shape with
`request.files` for `request.FILES`:

    @app.post("/upload")
    def upload():
        f = request.files["file"]            → one site
        g = request.files.get("file")        → one site

**The route check is not this detector's, and it now covers both Flask
spellings.** `entry_points.EntryPointStage` decides which functions are
routes, and `entry_points._extract_route` recognizes four shapes: a
**call on an attribute** whose last segment is an HTTP method or
`websocket` (`@app.post("/upload")`); the **same shape with `route` as
the last segment** (`@app.route("/upload", methods=["POST"])`, whose
method is the first name in `methods=`, or GET when it is absent); a
**bare name** Django guard (`@require_POST`); and a **call on a name**
taking a literal list (`@require_http_methods([...])`, DRF's
`@api_view([...])`). Both of Flask's spellings are routes as of
`B.3b-user_upload-route-detectors-followup`:

    @app.post("/upload")                        → an entry point
    @app.route("/upload", methods=["POST"])     → an entry point

so a view decorated the **canonical** way has the node this detector
matches by name. What remains out of scope is the **class-based**
`MethodView`: its route is `app.add_url_rule(...)` with
`view_func=UploadView.as_view(...)`, which names `as_view` rather than the
method, so nothing names the method — unless the method carries a route
decorator of its own, which the stage records like any other decorated
function.
What this detector matches is whatever `EntryPointStage` produced for the
file: a node carrying a `handler`, the HTTP/WebSocket route shape, and
the same shape `detectors.fastapi` and `detectors.django` read. The
fixture in `tests/test_store_upload_flask/test_end_to_end.py` uses
`@app.post` for E1 and the canonical `@app.route(..., methods=[...])` for
E2; `test_detector.py` pins both.

The detector does not invent a route either: a function is a candidate
only when the scan built a node naming it **and** the function carries a
route-shaped decorator (`_is_route_decorator`, `_extract_route`'s shape
restated). On a scan the stage produced, the second condition cannot
subtract anything — the stage records a handler only after it saw such a
decorator, so the two agree by construction; it is here so that a
hand-built node cannot make a helper a writer.

**The upload marker is the handler's body, and the body is the whole
test.** `request.files[...]` (a `Subscript`) or `request.files.get(...)`
(a `Call`), on the bare name `request`. Unlike `detectors.django`, the
detector **does not require `request` to be a parameter**, and that is
deliberate:

  - Django *hands* a view its request, so there the parameter is what
    the marker is read against and a module-level `request` is not the
    view's. Flask **imports** it (`from flask import request`), so the
    module's name *is* the view's — a detector that required a parameter
    would report nothing for the ordinary shape.
  - The job is the marker, not the import. A view that takes `request`
    as a parameter (a dispatcher that passes it, a test double) holds
    the same marker, and a `request.files[...]` whose `request` is
    bound some other way is read the same: one function's AST cannot
    prove a binding, and the marker is the upload's spelling. So all
    three of these are sites, and the file's imports are not consulted:

        from flask import request
        def upload(): ...        request.files["file"] ...

        def upload(request): ... request.files["file"] ...

        def upload(): ...        request.files["file"] ...  (no import)

Three limits, stated:

  - **A helper's `request.files` is not a marker.** In
    `def upload(): save(request)` with `request.files` in `save`, the
    view identifies no site: the marker is the handler's own body, and
    the upload the helper reads is the helper's detail. The writer's BFS
    already reaches the write sites the helper holds, so the *write*
    side is not lost — what is lost is the site, and with it the base
    0.5. The same false negative `detectors.django` and
    `detectors.fastapi` state.
  - **A nested function's `request.files` is the handler's**, because
    the nested function closes over the handler's `request` and its body
    is a statement of the handler's. The writer's `CONTEXT_IN_BODY`
    signal is the same reading.
  - **A renamed request is a false negative.** `r = request` and then
    `r.files["file"]`, or `from flask import request as req`: the marker
    is the name `request`, and the AST holds no binding from `r` to it
    (ADR-0016: no assignment chains) — the limit `detectors.fastapi`
    states for `from fastapi import UploadFile as UF`.

**One site per handler**, and `line` is `FunctionDef.lineno` — the `def`
line, not the access's. A handler with two `request.files` accesses is
one site: one view accepts one upload, and two sites for it would be two
edges into one store.

**Function-level only.** A class-based view — a `MethodView` subclass
whose `post` reads `self.request.files` — is out of scope: its route
lives in `add_url_rule` and not on the method, the class body holds
several methods and one `line` would have to stand for all of them, and
`self.request.files` is an attribute of an attribute, which
`_is_files_attribute` refuses. Pinned as no site, and a followup if a
corpus needs it.

The sites come back in source order (`FunctionDef.lineno`), so two runs
over one file are identical, and the context deduplicates by
`(entry_point.id, framework)`.
"""

from __future__ import annotations

import ast

from .....graph.models import Node
from ..base import UploadDetector, UploadSite

#: The registry key, and the `framework` every site is stamped with. The
#: class declares the same name, so the registry's name-vs-class warning
#: (`registry.register_upload_detector`) stays quiet.
NAME = "flask"

#: The name Flask's request proxy is imported under, and the attribute
#: the marker hangs off. Both are read as names: `request.files` is
#: Flask's spelling, and `req.files` is a different one this step does
#: not claim (the module docstring's third limit).
REQUEST = "request"
FILES = "files"

#: `request.files.get("file")` — the second spelling of the marker. The
#: attribute's name and not a call target, so `request.files.get` read
#: without a call is not an access either.
GET = "get"

#: The decorator attributes `entry_points._extract_route` records a route
#: for — `patterns.HTTP_METHODS`, `patterns.WEBSOCKET_METHOD` and Flask's
#: canonical `route` — restated rather than imported: `store/` reads no
#: pattern table (the rule `families.fs`, `detectors.fastapi` and
#: `detectors.django` follow), and this is a membership test, not logic
#: that drifts. `route` is a member because the stage reads the method out
#: of its `methods=` keyword, and the confirmation must not refuse a route
#: the stage recorded.
ROUTE_ATTRIBUTES = frozenset({
    "get", "post", "put", "delete", "patch", "options", "head",
    "websocket", "route",
})

#: The bare `Name` decorators the stage records a route for — Django's
#: method guards (`entry_points.DJANGO_METHOD_DECORATORS`), restated for
#: the same reason as `ROUTE_ATTRIBUTES`. The method each one names is not
#: read here: the node the stage built carries it.
GUARD_DECORATORS = frozenset({
    "require_GET", "require_POST", "require_PUT", "require_PATCH",
    "require_DELETE", "require_safe",
})

#: The `entry_points.DJANGO_LIST_DECORATORS` names — a call on a `Name`
#: taking a literal list of methods: Django's `require_http_methods` and
#: DRF's `api_view`.
LIST_DECORATORS = frozenset({"require_http_methods", "api_view"})


class FlaskUploadDetector(UploadDetector):
    """A Flask view whose body reads `request.files`."""

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
    included — a Flask view may be `async def`, and the marker does not
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

    `_extract_route`'s four shapes, restated: a call on an attribute whose
    last segment is an HTTP method, `websocket` or Flask's `route`, with a
    literal string path as its first argument; a bare name in
    `GUARD_DECORATORS` (`@require_POST`); and a call on a name in
    `LIST_DECORATORS` taking a literal list (`@require_http_methods([...])`,
    `@api_view([...])`).

    `@app.post("/upload")` and `@app.route("/upload", methods=["POST"])`
    are both that shape; a bare `@post` is a name and not a guard, and
    `@app.post(path)` with a variable path names no path — neither is a
    route here, and neither is one in the entry-point stage.
    """
    if isinstance(decorator, ast.Name):
        return decorator.id in GUARD_DECORATORS
    if not isinstance(decorator, ast.Call):
        return False
    if not decorator.args:
        return False

    first = decorator.args[0]
    if isinstance(decorator.func, ast.Attribute):
        if decorator.func.attr not in ROUTE_ATTRIBUTES:
            return False
        return isinstance(first, ast.Constant) and isinstance(
            first.value, str
        )
    if isinstance(decorator.func, ast.Name):
        if decorator.func.id not in LIST_DECORATORS:
            return False
        return isinstance(first, (ast.List, ast.Tuple)) and any(
            isinstance(element, ast.Constant)
            and isinstance(element.value, str)
            for element in first.elts
        )
    return False


# ------------------------------------------------------------------
# The upload marker
# ------------------------------------------------------------------

def _accepts_upload(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the handler's own body reads `request.files`.

    One condition, and it is the whole test: a body statement holds a
    `request.files` access. **No parameter is required**, and that is the
    one rule this detector does not share with `detectors.django` — Flask
    imports `request` rather than receiving it, so a parameter check
    would report nothing for the ordinary shape (the module docstring
    argues it and names the three accepted spellings).

    The body is walked statement by statement so that a decorator or a
    default value — which sit on the function but outside its body —
    cannot be the marker, and so that a nested function's body (a
    statement of the handler's, closing over the handler's `request`)
    can.
    """
    return any(
        _is_files_access(node)
        for statement in func.body
        for node in ast.walk(statement)
    )


def _is_files_access(node: ast.AST) -> bool:
    """True if `node` is `request.files[...]` or `request.files.get(...)`.

    The two spellings the brief names, and only them. A bare
    `request.files` is an attribute read that stores nothing — a view
    that passes the whole multi-file dict on is not claiming one upload —
    and `request.files["file"].read()` is the subscript, which the walk
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
    """True if `node` is `request.files`, with `request` the bare name.

    `request.files` and nothing else: an attribute of an attribute
    (`self.request.files`, a `MethodView`'s spelling, or
    `g.request.files`) is not read here — the class is out of scope, and
    the marker is the name Flask imports.
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
    "GUARD_DECORATORS",
    "LIST_DECORATORS",
    "NAME",
    "REQUEST",
    "ROUTE_ATTRIBUTES",
    "FlaskUploadDetector",
]
