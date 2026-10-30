"""The Django upload detector — shapes and non-shapes (B.3b).

`DjangoUploadDetector.detect` answers one question about one module: does a
route handler in it read `request.FILES`? The shapes here are D1–D6 of the
brief and the readings the brief leaves to the code:

  - the *entry point* decides what a route is (`entry_points` produced the
    node), and the detector confirms the decorator but does not invent a
    route — the same two conditions `detectors.fastapi` applies;
  - `request` must be a parameter of the handler, the access must be in
    the handler's own body, and the two spellings are `request.FILES[...]`
    and `request.FILES.get(...)`;
  - **Django's own route spellings are routes**: `@require_POST` and the
    other guards, `@require_http_methods([...])`, DRF's `@api_view([...])`
    (`B.3b-user_upload-route-detectors-followup`), so the confirmation
    accepts them — a bare `@post` and a bare `@csrf_exempt` are still not
    routes;
  - a class-based view is no site **as a unit**, a method carrying a
    route decorator of its own is one, and a nested function's access is
    the view's;
  - the answer is a function of the file and not of `ast.walk`'s order.

The entry-point nodes are hand-built, the way `tests/test_store_upload_core`
builds them: a detector is handed nodes, not a pipeline. `test_end_to_end.py`
is the same detector on a real scan, and its fixtures are the ones that say
which route decorators the entry-point stage recognizes.
"""

from __future__ import annotations

import ast

from aicorellator.graph.models import Node
from aicorellator.store.context.upload.base import UploadSite
from aicorellator.store.context.upload.detectors import django as detector
from aicorellator.store.context.upload.detectors.django import (
    DjangoUploadDetector,
)

FILE = "app/views.py"

#: The line the fixture's view is defined on — `FunctionDef.lineno`, and
#: therefore `UploadSite.line` — in `_module` below.
VIEW_LINE = 6


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
    decorator: str = '@router.post("/upload")',
    parameters: str = "request",
) -> str:
    """A one-view Django module: `parameters` the signature, `body` the body.

    The `def` line is `VIEW_LINE`, and `body` is written with its own
    indentation so a test reads like the view it pins.
    """
    return (
        '"""A Django view."""\n'
        "from router import router\n"
        "\n"
        "\n"
        f"{decorator}\n"
        f"def {name}({parameters}):\n"
        '    """Handle it."""\n'
        f"{body}"
    )


def _sites(source: str, handlers: tuple[str, ...] = ("upload",)) -> list:
    """The sites `source` yields for one node per name in `handlers`."""
    return DjangoUploadDetector().detect(
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
# D1–D6 — the shapes
# ============================================================

def test_a_files_subscript_is_a_site() -> None:
    """D1: `request.FILES["file"]` — the documented shape."""
    site = _one(_module('    f = request.FILES["file"]\n'))
    assert site.file_path == FILE
    assert site.line == VIEW_LINE
    assert site.framework == "django"
    assert site.entry_point.attributes["handler"] == "upload"


def test_a_files_get_call_is_a_site() -> None:
    """D2: `request.FILES.get("file")` — the second spelling."""
    site = _one(_module('    f = request.FILES.get("file")\n'))
    assert site.line == VIEW_LINE
    assert site.framework == "django"


def test_two_files_accesses_are_one_site() -> None:
    """D3: one upload per handler, not one per access."""
    source = _module(
        '    f = request.FILES["file"]\n'
        '    g = request.FILES.get("other")\n'
    )
    sites = _sites(source)
    assert len(sites) == 1
    assert sites[0].line == VIEW_LINE


def test_a_helper_with_files_but_no_entry_point_is_not_a_site() -> None:
    """D4: no node names it, so it is not a route a client can reach."""
    _none(
        _module('    f = request.FILES["file"]\n'),
        handlers=("upload_other",),
    )


def test_a_class_based_view_is_not_a_site_as_a_unit() -> None:
    """D5: a `View` subclass names no handler, so it is no site.

    `post` is a function of the module and the detector walks it, but a
    class-based view's route is `UploadView.as_view()` in `urls.py` or a
    decorator on the class, so nothing names the method and the
    entry-point stage records no node for the class.

    A hand-built node naming `post` *is* matched, because the method
    carries `@require_POST` and that is a route shape the stage records
    (`B.3b-user_upload-route-detectors-followup`) — the same
    decorated-method reading `test_a_route_decorated_method_is_a_site`
    pins. The class as a unit is what stays out of scope.
    """
    source = '''\
"""A Django class-based view."""
from django.views import View
from django.views.decorators.http import require_POST


class UploadView(View):
    @require_POST
    def post(self, request):
        """Persist it."""
        uploaded = request.FILES["file"]
        return HttpResponse(status=204)
'''
    assert _sites(source, handlers=()) == []
    assert _sites(source) == []
    site = _one(source, handlers=("post",))
    assert site.entry_point.attributes["handler"] == "post"
    assert site.line == 8


def test_a_view_without_files_is_not_a_site() -> None:
    """D6: a view that reads nothing out of the request."""
    _none(_module('    return {"ok": True}\n'))


# ============================================================
# The marker, read precisely
# ============================================================

def test_a_request_outside_the_signature_is_not_a_marker() -> None:
    """`request` must be a parameter: a module-level one is not the view's."""
    _none(
        _module('    return request.FILES["file"]\n', parameters=""),
        handlers=("upload",),
    )


def test_a_helpers_files_access_is_not_the_views() -> None:
    """The stated false negative: the marker is the handler's own body.

    The view hands its request on and the helper reads the file. The
    view's body holds no access and a node naming the helper is refused
    by the route-decorator check, so the module identifies no site at
    all — the write side is not lost (the writer's BFS still reaches the
    helper's writes), the *site* is.
    """
    source = '''\
"""A Django module whose upload is read in a helper."""
from router import router


@router.post("/upload")
def upload(request):
    """Hand the request on."""
    return save(request)


def save(request):
    """Read the file here."""
    return request.FILES["file"]
'''
    assert _sites(source, handlers=("upload",)) == []
    assert _sites(source, handlers=("upload", "save")) == []


def test_a_nested_functions_files_access_is_the_views() -> None:
    """A nested function closes over the view's `request` (a reading).

    The nested function's body is a statement of the handler's and its
    `request` is the handler's parameter, so the view accepts an upload.
    The writer's context-in-body signal is a narrower reading — a nested
    write is not the handler's own body — and the two do not have to
    agree: the site is the view's, the confidence is the write's.
    """
    source = _module(
        "    def inner():\n"
        '        return request.FILES["file"]\n'
        "    return inner()\n"
    )
    assert _one(source).line == VIEW_LINE


def test_a_bare_files_attribute_is_not_an_access() -> None:
    """`return request.FILES` stores nothing: no subscript, no call."""
    _none(_module("    return request.FILES\n"))


def test_a_get_on_another_attribute_is_not_an_access() -> None:
    """`request.data.get(...)` is a `get` on something that is not FILES."""
    _none(_module('    return request.data.get("file")\n'))


def test_a_qualified_request_is_not_an_access() -> None:
    """`self.request.FILES[...]` is a class-based view's spelling."""
    _none(
        _module('    return self.request.FILES["file"]\n'),
        handlers=("upload",),
    )


def test_a_subscript_inside_a_longer_chain_is_an_access() -> None:
    """`request.FILES["file"].read()` holds the subscript the walk visits."""
    assert _one(
        _module('    return request.FILES["file"].read()\n')
    ).line == VIEW_LINE


# ============================================================
# The route check — what the entry-point stage records
# ============================================================

def test_a_route_decorator_without_a_literal_path_is_not_a_route() -> None:
    """`@router.post(path)` names no path, so the stage records no route."""
    _none(_module('    return request.FILES["file"]\n',
                  decorator="@router.post(path)"))


def test_a_name_decorator_is_not_a_route() -> None:
    """`@post` is a bare name, and not one of Django's guards.

    A bare name is a route only when it is in `GUARD_DECORATORS` —
    `@require_POST` and friends — because the method is then the name's
    own suffix. `@post` names no method the stage can read, so it is not
    a route here and not one there.
    """
    _none(_module('    return request.FILES["file"]\n', decorator="@post"))


def test_a_csrf_exempt_wrapper_is_not_a_route() -> None:
    """`@csrf_exempt` has no method and no path: it is not a route.

    It is a wrapper, and a view carrying only it is a view the stage
    records nothing for — Django's routing still lives in `urls.py`. The
    route below it is read on its own (the next test).
    """
    _none(_module(
        '    return request.FILES["file"]\n', decorator="@csrf_exempt"
    ))


def test_a_guard_under_csrf_exempt_is_a_site() -> None:
    """A wrapper and a guard: the guard is the route, the wrapper is not."""
    source = '''\
"""A Django view."""
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


@csrf_exempt
@require_POST
def upload(request):
    """Handle it."""
    return request.FILES["file"]
'''
    site = _one(source)
    assert site.line == 8
    assert site.framework == "django"


def test_a_django_route_decorator_is_a_site() -> None:
    """`@require_http_methods([...])` is a route as of the route step.

    A view decorated the way Django documents it has an entry-point node
    now (`entry_points.DJANGO_LIST_DECORATORS`), so the node the stage
    built names it and the detector matches it. The method is not read
    here — the node carries it — so the confirmation only has to
    recognize the shape.
    """
    source = _module(
        '    return request.FILES["file"]\n',
        decorator='@require_http_methods(["POST"])',
    )
    site = _one(source)
    assert site.line == VIEW_LINE
    assert site.framework == "django"
    assert _sites(source, handlers=()) == []


def test_a_guard_decorator_is_a_site() -> None:
    """`@require_POST` is a bare name, and a route the stage records."""
    source = _module(
        '    return request.FILES["file"]\n', decorator="@require_POST"
    )
    site = _one(source)
    assert site.line == VIEW_LINE
    assert site.entry_point.attributes["handler"] == "upload"


def test_an_api_view_decorator_is_a_site() -> None:
    """DRF's `@api_view(["POST"])` is a call on a name, and a route."""
    source = _module(
        '    return request.FILES["file"]\n',
        decorator='@api_view(["POST"])',
    )
    assert _one(source).line == VIEW_LINE


def test_a_flask_route_decorator_is_confirmed_too() -> None:
    """The confirmation mirrors `_extract_route`, not this framework.

    A Django module does not spell a view `@app.route`, but the
    entry-point stage is framework-agnostic: when it records a route for a
    decorator, the detector must not refuse the node it built. The
    reverse — a Django guard in a Flask module — is pinned in
    `tests/test_store_upload_flask/test_detector.py`.
    """
    source = _module(
        '    return request.FILES["file"]\n',
        decorator='@app.route("/upload", methods=["POST"])',
    )
    assert _one(source).line == VIEW_LINE


def test_a_list_decorator_without_a_literal_is_not_a_route() -> None:
    """`@require_http_methods(METHODS)` names no method: not a route."""
    _none(_module(
        '    return request.FILES["file"]\n',
        decorator="@require_http_methods(METHODS)",
    ))


def test_a_function_without_a_route_decorator_is_not_a_site() -> None:
    """The detector confirms the decorator, it does not invent a route.

    The scan built an entry point for the name (a hand-built delta, or a
    duplicate name); the function itself carries no route decorator, so
    it is not the handler that route names.
    """
    _none(_module('    return request.FILES["file"]\n',
                  decorator="@staticmethod"))


def test_a_route_decorated_method_is_a_site() -> None:
    """A decorated method *is* a site, and that is not a class-based view.

    The detector is function-based: a method is a function of the module,
    and `fastapi`'s `test_a_class_method_route_is_a_site` pins the same
    reading for that framework. No Django class-based view spells it —
    its route is in `urls.py`, which is why D5 is no site — so the
    boundary is pinned here rather than left to an accident.
    """
    source = '''\
"""A class whose method carries a route decorator."""
from router import router


class Uploads:
    @router.post("/upload")
    def upload(self, request):
        return request.FILES["file"]
'''
    site = _one(source)
    assert site.line == 7
    assert site.framework == "django"


def test_an_async_view_is_a_site() -> None:
    """A Django view may be `async def`; the marker does not care."""
    source = _module('    return request.FILES["file"]\n').replace(
        "def upload(", "async def upload("
    )
    assert _one(source).line == VIEW_LINE


# ============================================================
# The nodes the detector is handed
# ============================================================

def test_an_empty_entry_point_list_is_no_site() -> None:
    """No node, no site — the detector never invents an entry point."""
    tree = ast.parse(_module('    return request.FILES["file"]\n'))
    assert DjangoUploadDetector().detect(tree, FILE, []) == []


def test_a_cli_entry_node_is_skipped() -> None:
    """A CLI entry is the module, not a function: it has no handler."""
    cli = Node(
        id="entry_point:cli", kind="entry_point", name=f"CLI:{FILE}",
        host_id="localhost",
        attributes={"source_file": FILE, "method": "CLI", "path": FILE},
    )
    tree = ast.parse(_module('    return request.FILES["file"]\n'))
    assert DjangoUploadDetector().detect(tree, FILE, [cli]) == []


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
    tree = ast.parse(_module('    return request.FILES["file"]\n'))
    assert DjangoUploadDetector().detect(tree, FILE, [streamlit]) == []


# ============================================================
# The shape of the answer
# ============================================================

def test_a_site_names_the_node_it_was_handed() -> None:
    """The site carries the node, so the writer has the edge's source."""
    node = _node("upload")
    sites = DjangoUploadDetector().detect(
        ast.parse(_module('    return request.FILES["file"]\n')),
        FILE,
        [node],
    )
    assert sites[0].entry_point is node


def test_the_framework_is_the_module_constant() -> None:
    """`framework` is `"django"`, lowercase, like `fs.FsFamily.name`."""
    assert detector.NAME == "django"
    assert DjangoUploadDetector.name == "django"
    assert _one(
        _module('    return request.FILES["file"]\n')
    ).framework == "django"


def test_sites_come_back_in_source_order() -> None:
    """Source order, not the order the nodes arrived in.

    The nodes are handed over in reverse (`alpha` first) and the sites
    still follow the file: `zeta` is defined first, so its site is first.
    """
    source = '''\
"""Two Django views."""
from router import router


@router.post("/zeta")
def zeta(request):
    return request.FILES["file"]


@router.post("/alpha")
def alpha(request):
    return request.FILES["file"]
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
    source = _module('    return request.FILES.get("file")\n')
    first = [(s.entry_point.id, s.line, s.framework) for s in _sites(source)]
    second = [(s.entry_point.id, s.line, s.framework) for s in _sites(source)]
    assert first == second == [("entry_point:upload", VIEW_LINE, "django")]


def test_one_site_per_node_when_two_nodes_share_a_handler_name() -> None:
    """Two routes named after one function are two nodes, hence two sites.

    `entry_points` records one node per route, so a name two routes share
    (Python keeps only the last definition at runtime, but the file holds
    both) is two nodes, and the detector reports each. The writes the
    name reaches are the same either way, so the writer's edges are.
    """
    sites = DjangoUploadDetector().detect(
        ast.parse(_module('    return request.FILES["file"]\n')),
        FILE,
        [
            _node("upload", "entry_point:post"),
            _node("upload", "entry_point:get"),
        ],
    )
    assert [site.entry_point.id for site in sites] == [
        "entry_point:post", "entry_point:get",
    ]
