"""The FastAPI upload detector — shapes and non-shapes (B.3b).

`FastAPIUploadDetector.detect` answers one question about one module: does
a route handler in it accept an upload? The shapes here are S1–S6 of the
brief and the non-shapes N1–N5, and the rest of the file pins the
readings the brief leaves to the code:

  - the *entry point* decides what a route is (`entry_points` produced
    the node), and the detector confirms the decorator but does not
    invent a route;
  - `Optional[UploadFile]` and an import alias are false negatives, and
    `Form(...)` beats a file-typed annotation;
  - the answer is a function of the file and not of `ast.walk`'s order.

The entry-point nodes are hand-built, the way
`tests/test_store_upload_core` builds them: a detector is handed nodes, not
a pipeline. `test_end_to_end.py` is the same detector on a real scan.
"""

from __future__ import annotations

import ast

import pytest

from aicorellator.graph.models import Node
from aicorellator.store.context.upload.base import UploadSite
from aicorellator.store.context.upload.detectors import fastapi as detector
from aicorellator.store.context.upload.detectors.fastapi import (
    UPLOAD_CONTAINERS,
    FastAPIUploadDetector,
)

FILE = "app/main.py"

#: The line `_module`'s handler is defined on — `FunctionDef.lineno`, and
#: therefore `UploadSite.line`.
HANDLER_LINE = 8


def _node(handler: str, node_id: str | None = None) -> Node:
    """The entry-point node `entry_points.EntryPointStage` would build."""
    return Node(
        id=node_id or f"entry_point:{handler}",
        kind="entry_point",
        name=f"POST /{handler}",
        host_id="localhost",
        attributes={
            "source_file": FILE, "line_start": HANDLER_LINE,
            "method": "POST", "path": f"/{handler}", "handler": handler,
        },
    )


def _module(
    parameters: str,
    *,
    name: str = "upload",
    decorator: str = '@app.post("/upload")',
) -> str:
    """A one-handler FastAPI module, `parameters` being its signature."""
    return (
        "from fastapi import FastAPI, File, Form, UploadFile\n"
        "import fastapi\n"
        "\n"
        "app = FastAPI()\n"
        "\n"
        "\n"
        f"{decorator}\n"
        f"async def {name}({parameters}):\n"
        '    """Handle it."""\n'
        "    return {}\n"
    )


def _sites(source: str, handlers: tuple[str, ...] = ("upload",)) -> list:
    """The sites `source` yields for one node per name in `handlers`."""
    return FastAPIUploadDetector().detect(
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
# S1–S6 — the shapes
# ============================================================

def test_an_upload_file_parameter_is_a_site() -> None:
    """S1: `file: UploadFile` — the documented shape."""
    site = _one(_module("file: UploadFile"))
    assert site.file_path == FILE
    assert site.line == HANDLER_LINE
    assert site.framework == "fastapi"
    assert site.entry_point.attributes["handler"] == "upload"


def test_a_list_of_upload_files_is_a_site() -> None:
    """S2: `files: list[UploadFile]` — a multi-file field."""
    assert _one(_module("files: list[UploadFile]")).framework == "fastapi"


def test_bytes_with_a_file_default_is_a_site() -> None:
    """S3: `file: bytes = File(...)` — the marker plus `bytes`."""
    assert _one(_module("file: bytes = File(...)")).line == HANDLER_LINE


def test_a_qualified_upload_file_is_a_site() -> None:
    """S4: `f: fastapi.UploadFile` — read by its last segment."""
    assert _one(_module("f: fastapi.UploadFile")).framework == "fastapi"


def test_a_typing_list_of_upload_files_is_a_site() -> None:
    """S5: `files: List[UploadFile]` — `typing.List` is a container too."""
    assert _one(_module("files: List[UploadFile]")).framework == "fastapi"


def test_a_handler_with_two_upload_parameters_is_one_site() -> None:
    """S6: one upload per handler, not one per parameter."""
    sites = _sites(_module("first: UploadFile, second: UploadFile"))
    assert len(sites) == 1
    assert sites[0].line == HANDLER_LINE


@pytest.mark.parametrize("container", sorted(UPLOAD_CONTAINERS))
def test_every_container_fastapi_accepts_is_a_site(container: str) -> None:
    """The container set is the module's, and each member is an upload."""
    assert _one(_module(f"files: {container}[UploadFile]")).framework == (
        "fastapi"
    )


def test_a_qualified_upload_file_inside_a_container_is_a_site() -> None:
    """`list[fastapi.UploadFile]` is a subscript of the qualified name."""
    assert _one(_module("files: list[fastapi.UploadFile]")).framework == (
        "fastapi"
    )


def test_a_keyword_only_upload_is_a_site() -> None:
    """A keyword-only parameter is a parameter FastAPI reads."""
    assert _one(_module("*, file: UploadFile")).framework == "fastapi"


def test_a_positional_only_upload_is_a_site() -> None:
    """A positional-only parameter is a positional parameter (the reading).

    `def upload(file: UploadFile, /)` is the same upload as the same
    signature without the `/`; `*args` and `**kwargs` are the shapes that
    are *not* read, and they are the ones the brief names.
    """
    assert _one(_module("file: UploadFile, /")).framework == "fastapi"


def test_a_file_default_without_an_annotation_is_a_site() -> None:
    """FastAPI infers `bytes` from `File(...)`, so the annotation may go."""
    assert _one(_module("file = File(...)")).framework == "fastapi"


def test_a_qualified_file_marker_is_a_site() -> None:
    """`fastapi.File(...)` ends in the marker's name like `File(...)`."""
    assert _one(_module("file: bytes = fastapi.File(...)")).framework == (
        "fastapi"
    )


# ============================================================
# N1–N5 — what is not an upload
# ============================================================

def test_a_form_field_is_not_a_site() -> None:
    """N1: `file: str = Form(...)` is a string form field."""
    _none(_module("file: str = Form(...)"))


def test_a_form_marker_beats_a_file_annotation() -> None:
    """`Form(...)` is not an upload whatever the annotation says."""
    _none(_module("file: UploadFile = Form(...)"))


def test_a_string_annotation_with_a_file_default_is_not_a_site() -> None:
    """The brief's reading: `File(...)` is documented with `bytes` only.

    FastAPI treats `File` as a marker and would accept the field, but the
    AST cannot confirm the author meant a file rather than a string, and
    a false positive here claims a store write that may not exist.
    """
    _none(_module("file: str = File(...)"))


def test_a_request_without_an_upload_is_not_a_site() -> None:
    """N2: `request: Request` — `request.form()` is a followup step."""
    _none(_module("request: Request"))


def test_a_plain_helper_is_not_a_site() -> None:
    """N3: not a route, so not a writer — a client cannot reach it."""
    _none(_module("file: UploadFile"), handlers=("upload_other",))


def test_a_route_without_a_route_decorator_is_not_a_site() -> None:
    """The detector confirms the decorator, it does not invent a route.

    The scan built an entry point for the name (a hand-built delta, or a
    duplicate name); the function itself carries no route decorator, so
    it is not the handler that route names.
    """
    _none(_module("file: UploadFile", decorator="@staticmethod"))


def test_bytes_without_a_file_default_is_not_a_site() -> None:
    """N4: `file: bytes` alone is a JSON body field, not a file."""
    _none(_module("file: bytes"))


@pytest.mark.parametrize("kind", ["*args", "**kwargs"])
def test_varargs_are_not_upload_parameters(kind: str) -> None:
    """N5: FastAPI does not read an upload through `*args`/`**kwargs`."""
    _none(_module(f"{kind}: UploadFile"))


def test_an_optional_upload_file_is_not_a_site() -> None:
    """A false negative, stated: `Optional[...]` is not a container here."""
    _none(_module("file: Optional[UploadFile]"))


def test_a_handler_with_no_parameters_is_not_a_site() -> None:
    """A route that takes nothing accepts no upload."""
    _none(_module(""))


def test_an_empty_entry_point_list_is_no_site() -> None:
    """No node, no site — the detector never invents an entry point."""
    tree = ast.parse(_module("file: UploadFile"))
    assert FastAPIUploadDetector().detect(tree, FILE, []) == []


def test_a_cli_entry_node_is_skipped() -> None:
    """A CLI entry is the module, not a function: it has no handler."""
    cli = Node(
        id="entry_point:cli", kind="entry_point", name=f"CLI:{FILE}",
        host_id="localhost",
        attributes={"source_file": FILE, "method": "CLI", "path": FILE},
    )
    tree = ast.parse(_module("file: UploadFile"))
    assert FastAPIUploadDetector().detect(tree, FILE, [cli]) == []


# ============================================================
# The shape of the answer
# ============================================================

def test_a_site_names_the_node_it_was_handed() -> None:
    """The site carries the node, so the writer has the edge's source."""
    node = _node("upload")
    sites = FastAPIUploadDetector().detect(
        ast.parse(_module("file: UploadFile")), FILE, [node]
    )
    assert sites[0].entry_point is node


def test_the_framework_is_the_module_constant() -> None:
    """`framework` is `"fastapi"`, lowercase, like `fs.FsFamily.name`."""
    assert detector.NAME == "fastapi"
    assert FastAPIUploadDetector.name == "fastapi"
    assert _one(_module("file: UploadFile")).framework == "fastapi"


def test_sites_come_back_in_source_order() -> None:
    """Source order, not the order the nodes arrived in.

    The nodes are handed over in reverse (`alpha` first) and the sites
    still follow the file: `zeta` is defined first, so its site is
    first.
    """
    source = '''\
from fastapi import FastAPI, UploadFile

app = FastAPI()


@app.post("/zeta")
async def zeta(file: UploadFile):
    return {}


@app.post("/alpha")
async def alpha(file: UploadFile):
    return {}
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
    source = _module("files: list[UploadFile]")
    first = [(s.entry_point.id, s.line, s.framework) for s in _sites(source)]
    second = [(s.entry_point.id, s.line, s.framework) for s in _sites(source)]
    assert first == second == [("entry_point:upload", HANDLER_LINE, "fastapi")]


def test_a_class_method_route_is_a_site() -> None:
    """A method is a function of the module: the name and shape decide."""
    source = '''\
from fastapi import FastAPI, UploadFile

app = FastAPI()


class Uploads:
    @app.post("/upload")
    async def upload(self, file: UploadFile):
        return {}
'''
    assert _one(source).line == 8


def test_a_decorator_without_a_call_is_still_a_route() -> None:
    """`@post` is a `Name` and `@app.post(...)` a `Call` — both count."""
    assert _one(_module("file: UploadFile", decorator="@post")).framework == (
        "fastapi"
    )


def test_one_site_per_node_when_two_nodes_share_a_handler_name() -> None:
    """Two routes named after one function are two nodes, hence two sites.

    `entry_points` records one node per route, so a name two routes share
    (Python keeps only the last definition at runtime, but the file holds
    both) is two nodes, and the detector reports each. The writes the
    name reaches are the same either way, so the writer's edges are.
    """
    sites = FastAPIUploadDetector().detect(
        ast.parse(_module("file: UploadFile")),
        FILE,
        [
            _node("upload", "entry_point:post"),
            _node("upload", "entry_point:get"),
        ],
    )
    assert [site.entry_point.id for site in sites] == [
        "entry_point:post", "entry_point:get",
    ]
