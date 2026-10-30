"""The Flask upload detector — shapes and non-shapes (B.3b).

`FlaskUploadDetector.detect` answers one question about one module: does a
route handler in it read `request.files`? The shapes here are D1–D7 of the
brief and the readings the brief leaves to the code:

  - the *entry point* decides what a route is (`entry_points` produced the
    node), and the detector confirms the decorator but does not invent a
    route — the same two conditions `detectors.django` and
    `detectors.fastapi` apply;
  - **both Flask spellings are routes**: `@app.post("/upload")` and the
    canonical `@app.route("/upload", methods=["POST"])` both produce an
    entry point (`B.3b-user_upload-route-detectors-followup`, where the
    stage learned to read `methods=`), and the confirmation accepts both
    — a `@app.route(path)` with a variable path is not a route;
  - the access must be in the handler's own body, the two spellings are
    `request.files[...]` and `request.files.get(...)`, and **no binding for
    `request` is required** — that is the one rule this detector does not
    share with `detectors.django`, and it is pinned in three spellings
    (imported, a parameter, neither);
  - a class-based `MethodView` is no site, and a nested function's access
    is the view's;
  - the answer is a function of the file and not of `ast.walk`'s order.

The entry-point nodes are hand-built, the way `tests/test_store_upload_core`
builds them: a detector is handed nodes, not a pipeline.
`test_end_to_end.py` is the same detector on a real scan, and its fixtures
are the ones that say which route decorators the entry-point stage
recognizes.
"""

from __future__ import annotations

import ast

from aicorellator.graph.models import Node
from aicorellator.store.context.upload.base import UploadSite
from aicorellator.store.context.upload.detectors import flask as detector
from aicorellator.store.context.upload.detectors.flask import (
    FlaskUploadDetector,
)

FILE = "app/views.py"

#: The line the fixture's view is defined on — `FunctionDef.lineno`, and
#: therefore `UploadSite.line` — in `_module` below.
VIEW_LINE = 7


def _node(handler: str, node_id: str | None = None) -> Node:
    """The entry-point node `entry_points.EntryPointStage` would build.

    The HTTP route shape: `source_file`, `line_start`, `method`, `path`
    and `handler`, and no `entry_kind` — a CLI, Streamlit or MCP entry
    carries a kind instead, and no handler.
    """
    return Node(
        id=node_id or f"entry_point:{handler}",
        kind="entry_point",
        name=f"POST /{handler}",
        host_id="localhost",
        attributes={
            "source_file": FILE, "line_start": VIEW_LINE,
            "method": "POST", "path": f"/{handler}", "handler": handler,
        },
    )


def _module(
    body: str,
    *,
    name: str = "upload",
    decorator: str = '@app.post("/upload")',
    parameters: str = "",
    imports: str = "from flask import Flask, request",
) -> str:
    """A one-view Flask module: `parameters` the signature, `body` the body.

    The `def` line is `VIEW_LINE`, and `body` is written with its own
    indentation so a test reads like the view it pins. `parameters` is
    empty by default — Flask views import `request` instead of being
    handed one — and `imports` is what a test changes to pin a module
    that imports nothing.
    """
    return (
        '"""A Flask view."""\n'
        f"{imports}\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        f"{decorator}\n"
        f"def {name}({parameters}):\n"
        '    """Handle it."""\n'
        f"{body}"
    )


def _sites(source: str, handlers: tuple[str, ...] = ("upload",)) -> list:
    """The sites `source` yields for one node per name in `handlers`."""
    return FlaskUploadDetector().detect(
        ast.parse(source), FILE, [_node(name) for name in handlers]
    )


def _one(source: str, handlers: tuple[str, ...] = ("upload",)) -> UploadSite:
    """The one site `source` yields, failing if there is not exactly one."""
    sites = _sites(source, handlers)
    assert len(sites) == 1
    return sites[0]


def _none(source: str, handlers: tuple[str, ...] = ("upload",)) -> None:
    """`source` yields no site."""
    assert _sites(source, handlers) == []


# ============================================================
# D1–D7 — the shapes
# ============================================================

def test_a_files_subscript_is_a_site() -> None:
    """D1: `request.files["file"]` under `@app.post` — the shape."""
    site = _one(_module('    f = request.files["file"]\n'))
    assert site.file_path == FILE
    assert site.line == VIEW_LINE
    assert site.framework == "flask"
    assert site.entry_point.attributes["handler"] == "upload"


def test_a_files_get_call_is_a_site() -> None:
    """D2: `request.files.get("file")` — the second spelling."""
    site = _one(_module('    f = request.files.get("file")\n'))
    assert site.line == VIEW_LINE
    assert site.framework == "flask"


def test_two_files_accesses_are_one_site() -> None:
    """D3: one upload per handler, not one per access."""
    source = _module(
        '    f = request.files["file"]\n'
        '    g = request.files.get("other")\n'
    )
    sites = _sites(source)
    assert len(sites) == 1
    assert sites[0].line == VIEW_LINE


def test_a_view_with_files_but_no_entry_point_is_not_a_site() -> None:
    """D4: no node names it, so it is not a route a client can reach."""
    _none(
        _module('    f = request.files["file"]\n'),
        handlers=("upload_other",),
    )


def test_a_class_based_method_view_is_not_a_site() -> None:
    """D5: a `MethodView` subclass is out of scope, in every reading.

    `post` is a function of the module and the detector walks it, but
    nothing names it: a `MethodView`'s route is registered with
    `app.add_url_rule(..., view_func=UploadView.as_view("upload"))` — the
    route names `as_view`, and the entry-point stage records no node for
    the method. A hand-built node naming `post` changes nothing either,
    because the method carries no route decorator, which is the second
    condition `_is_route` applies.

    The body spells the marker as the imported proxy (`request.files`,
    which this detector *does* accept) and not `self.request.files`, so
    the route check is the only reason there is no site here — the
    class-based spelling is refused on its own in
    `test_a_qualified_request_is_not_an_access`.
    """
    source = '''\
"""A Flask class-based view."""
from flask.views import MethodView


class UploadView(MethodView):
    def post(self):
        """Persist it."""
        uploaded = request.files["file"]
        return "ok", 204
'''
    assert _sites(source, handlers=()) == []
    assert _sites(source) == []
    assert FlaskUploadDetector().detect(
        ast.parse(source), FILE, [_node("post")]
    ) == []


def test_a_view_without_files_is_not_a_site() -> None:
    """D6: a view that reads nothing out of the request."""
    _none(_module('    return "ok", 204\n'))


def test_a_route_methods_decorator_is_a_site() -> None:
    """D7: the canonical Flask shape is a route as of the route step.

    `@app.route("/upload", methods=["POST"])` is a call on an attribute
    whose last segment is `route`, and `entry_points._extract_route`
    reads the method out of the keyword
    (`B.3b-user_upload-route-detectors-followup`), so the stage builds the
    node and this detector matches it by name. The method is not read
    here — the node carries it.

    Both readings are pinned: the hand-built node is accepted by the
    confirmation, and with no node at all there is nothing to match.
    """
    source = _module(
        '    f = request.files["file"]\n',
        decorator='@app.route("/upload", methods=["POST"])',
    )
    site = _one(source)
    assert site.line == VIEW_LINE
    assert site.framework == "flask"
    assert _sites(source, handlers=()) == []


def test_a_route_decorator_without_methods_is_a_site() -> None:
    """`@app.route("/upload")` is a route, and Flask's default method is GET.

    The stage records GET when `methods=` is absent or unresolved, and
    the confirmation does not read the method at all: the node it was
    handed carries it.
    """
    _one(_module(
        '    f = request.files["file"]\n',
        decorator='@app.route("/upload")',
    ))


def test_a_route_decorator_with_a_variable_path_is_not_a_route() -> None:
    """`@app.route(path, ...)` names no path, so the stage records no route."""
    _none(_module(
        '    f = request.files["file"]\n',
        decorator='@app.route(path, methods=["POST"])',
    ))


def test_a_django_guard_is_confirmed_too() -> None:
    """The confirmation mirrors `_extract_route`, not this framework.

    A Flask module does not spell a view `@require_POST`, but the
    entry-point stage is framework-agnostic: when it records a route for a
    decorator, the detector must not refuse the node it built. The
    reverse — a Flask `@app.route` in a Django module — is pinned in
    `tests/test_store_upload_django/test_detector.py`.
    """
    _one(_module(
        '    f = request.files["file"]\n', decorator="@require_POST"
    ))


# ============================================================
# The marker, read precisely
# ============================================================

def test_a_bare_files_attribute_is_not_an_access() -> None:
    """`request.files` alone stores nothing: no subscript, no `get`."""
    _none(_module("    return request.files\n"))


def test_a_get_on_another_attribute_is_not_an_access() -> None:
    """`request.form["file"]` is a form *field*, not a file.

    Flask's `request.form` is the multipart form's scalar fields, and a
    field is not an upload — the same refusal `detectors.fastapi` makes
    for `Form(...)`. `request.data.get(...)` and `request.json` are the
    same: a `get` or a subscript on something that is not `files`.
    """
    _none(_module('    return request.form["file"]\n'))
    _none(_module('    return request.form.get("file")\n'))
    _none(_module('    return request.data.get("file")\n'))


def test_a_qualified_request_is_not_an_access() -> None:
    """`self.request.files[...]` is a class-based view's spelling."""
    _none(
        _module('    return self.request.files["file"]\n'),
        handlers=("upload",),
    )


def test_a_subscript_inside_a_longer_chain_is_an_access() -> None:
    """`request.files["file"].read()` holds the subscript the walk visits."""
    assert _one(
        _module('    return request.files["file"].read()\n')
    ).line == VIEW_LINE


def test_the_marker_alone_is_a_site_without_a_request_binding() -> None:
    """The stated reading: the marker is the test, the import is not.

    A module that imports nothing still reads `request.files` in a view,
    and this detector claims it: one file's AST cannot prove where
    `request` came from, and the marker is the upload's spelling. This is
    the rule `detectors.django` does *not* share — there the parameter is
    required, because Django hands a view its request.
    """
    assert _one(
        _module('    f = request.files["file"]\n', imports="import os")
    ).line == VIEW_LINE


def test_a_request_parameter_is_a_site() -> None:
    """The parameter form: a project may pass the request in itself.

    Unusual for Flask (the proxy is imported) and not wrong — a
    dispatcher or a test double does it — and the marker reads the same
    either way.
    """
    assert _one(
        _module('    f = request.files["file"]\n', parameters="request")
    ).line == VIEW_LINE


def test_a_renamed_request_is_not_a_site() -> None:
    """The stated false negative: the marker's name is `request`.

    `r = request` then `r.files["file"]`, or `from flask import request as
    req`: the AST holds no binding from the new name to `request`
    (ADR-0016: no assignment chains), so a project that renames the proxy
    is not read. The same limit `detectors.fastapi` states for
    `from fastapi import UploadFile as UF`.
    """
    _none(_module(
        "    r = request\n"
        '    return r.files["file"]\n',
    ))
    _none(_module(
        '    return req.files["file"]\n',
        imports="from flask import request as req",
    ))


def test_a_helpers_files_access_is_not_the_views() -> None:
    """The stated false negative: the marker is the handler's own body.

    The view hands its request on and the helper reads the file — the
    ordinary Flask shape for a service function. The view's body holds no
    access and a node naming the helper is refused by the route-decorator
    check, so the module identifies no site at all: the write side is not
    lost (the writer's BFS still reaches the helper's writes), the *site*
    is.
    """
    source = '''\
"""A Flask module whose upload is read in a helper."""
from flask import Flask, request

app = Flask(__name__)


@app.post("/upload")
def upload():
    """Hand the request on."""
    return save(request)


def save(request):
    """Read the file here."""
    return request.files["file"]
'''
    assert _sites(source, handlers=("upload",)) == []
    assert _sites(source, handlers=("upload", "save")) == []


def test_a_nested_functions_files_access_is_the_views() -> None:
    """The nested function closes over the handler's `request`.

    Its body is a statement of the handler's, which is the same reading
    the writer's `CONTEXT_IN_BODY` signal makes.
    """
    source = _module(
        "    def read():\n"
        '        return request.files["file"]\n'
        "    return read()\n"
    )
    assert _one(source).line == VIEW_LINE


# ============================================================
# The route check — what the entry-point stage records
# ============================================================

def test_a_route_decorator_without_a_literal_path_is_not_a_route() -> None:
    """`@app.post(path)` names no path, so the stage records no route."""
    _none(_module('    return request.files["file"]\n',
                  decorator="@app.post(path)"))


def test_a_name_decorator_is_not_a_route() -> None:
    """`@post` is a `Name`: the stage reads an `Attribute`, so is not one."""
    _none(_module('    return request.files["file"]\n', decorator="@post"))


def test_a_function_without_a_route_decorator_is_not_a_site() -> None:
    """The detector confirms the decorator, it does not invent a route.

    The scan built an entry point for the name (a hand-built delta, or a
    duplicate name); the function itself carries no route decorator, so
    it is not the handler that route names.
    """
    _none(_module('    return request.files["file"]\n',
                  decorator="@staticmethod"))


def test_a_route_decorated_method_is_a_site() -> None:
    """A decorated method *is* a site, and that is not a class-based view.

    The detector is function-based: a method is a function of the module,
    and `fastapi`'s `test_a_class_method_route_is_a_site` pins the same
    reading for that framework. No `MethodView` spells it — its route is
    registered with `add_url_rule`, which is why D5 is no site — so the
    boundary is pinned here rather than left to an accident.
    """
    source = '''\
"""A class whose method carries a route decorator."""
from flask import Flask, request

app = Flask(__name__)


class Uploads:
    @app.post("/upload")
    def upload(self):
        return request.files["file"]
'''
    site = _one(source)
    assert site.line == 9
    assert site.framework == "flask"


def test_an_async_view_is_a_site() -> None:
    """A Flask view may be `async def`; the marker does not care."""
    source = _module('    return request.files["file"]\n').replace(
        "def upload(", "async def upload("
    )
    assert _one(source).line == VIEW_LINE


# ============================================================
# The nodes the detector is handed
# ============================================================

def test_an_empty_entry_point_list_is_no_site() -> None:
    """No node, no site — the detector never invents an entry point."""
    tree = ast.parse(_module('    return request.files["file"]\n'))
    assert FlaskUploadDetector().detect(tree, FILE, []) == []


def test_a_cli_entry_node_is_skipped() -> None:
    """A CLI entry is the module, not a function: it has no handler."""
    cli = Node(
        id="entry_point:cli", kind="entry_point", name=f"CLI:{FILE}",
        host_id="localhost",
        attributes={"source_file": FILE, "method": "CLI", "path": FILE},
    )
    tree = ast.parse(_module('    return request.files["file"]\n'))
    assert FlaskUploadDetector().detect(tree, FILE, [cli]) == []


def test_a_module_level_entry_node_is_skipped() -> None:
    """A Streamlit entry carries `entry_kind` and no handler."""
    streamlit = Node(
        id="entry_point:file_uploader", kind="entry_point",
        name=f"Streamlit:{FILE}:file_uploader", host_id="localhost",
        attributes={
            "source_file": FILE, "line_start": 1, "method": "STREAMLIT",
            "entry_kind": "streamlit", "path": "file_uploader",
        },
    )
    tree = ast.parse(_module('    return request.files["file"]\n'))
    assert FlaskUploadDetector().detect(tree, FILE, [streamlit]) == []


# ============================================================
# The shape of the answer
# ============================================================

def test_a_site_names_the_node_it_was_handed() -> None:
    """The site carries the node, so the writer has the edge's source."""
    node = _node("upload")
    sites = FlaskUploadDetector().detect(
        ast.parse(_module('    return request.files["file"]\n')),
        FILE,
        [node],
    )
    assert sites[0].entry_point is node


def test_the_framework_is_the_module_constant() -> None:
    """`framework` is `"flask"`, lowercase, like `fs.FsFamily.name`."""
    assert detector.NAME == "flask"
    assert FlaskUploadDetector.name == "flask"
    assert _one(
        _module('    return request.files["file"]\n')
    ).framework == "flask"




def test_sites_come_back_in_source_order() -> None:
    """Source order, not the order the nodes arrived in.

    The nodes are handed over in reverse (`alpha` first) and the sites
    still follow the file: `zeta` is defined first, so its site is first.
    """
    source = '''\
"""Two Flask views."""
from flask import Flask, request

app = Flask(__name__)


@app.post("/zeta")
def zeta():
    return request.files["file"]


@app.post("/alpha")
def alpha():
    return request.files["file"]
'''
    sites = _sites(source, handlers=("alpha", "zeta"))
    assert [
        site.entry_point.attributes["handler"] for site in sites
    ] == ["zeta", "alpha"]
    assert [site.line for site in sites] == sorted(
        site.line for site in sites
    )


def test_two_runs_are_identical() -> None:
    """A detector's answer is a function of the file."""
    source = _module('    return request.files.get("file")\n')
    first = [(s.entry_point.id, s.line, s.framework) for s in _sites(source)]
    second = [(s.entry_point.id, s.line, s.framework) for s in _sites(source)]
    assert first == second == [("entry_point:upload", VIEW_LINE, "flask")]


def test_one_site_per_node_when_two_nodes_share_a_handler_name() -> None:
    """Two routes named after one function are two nodes, hence two sites.

    `entry_points` records one node per route, so a name two routes share
    (Python keeps only the last definition at runtime, but the file holds
    both) is two nodes, and the detector reports each. The writes the
    name reaches are the same either way, so the writer's edges are.
    """
    sites = FlaskUploadDetector().detect(
        ast.parse(_module('    return request.files["file"]\n')),
        FILE,
        [
            _node("upload", "entry_point:post"),
            _node("upload", "entry_point:get"),
        ],
    )
    assert [site.entry_point.id for site in sites] == [
        "entry_point:post", "entry_point:get",
    ]
