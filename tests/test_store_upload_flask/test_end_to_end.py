"""The Flask detector through the pipeline (B.3b-user_upload-flask, E1–E2).

The fixture is a Flask module: an `@app.post` view that reads
`request.files["report"]` and writes `/uploads/report.txt` in its own body,
and a second view that reads the same literal back. The entry-point nodes
are the ones the *real* `entry_points` detector and `EntryPointStage`
produce — not hand-built — so what is pinned here is the whole seam: a
route the scan found, a body the detector read, a store the `fs` family
recognized, and the `writes_to` edge that joins them.

**The decorators are the point of the fixtures, and they are named
here.** `entry_points._extract_route` recognizes both of Flask's route
spellings as of `B.3b-user_upload-route-detectors-followup`: a call on an
attribute whose last segment is an HTTP method (`@app.post("/upload")`),
and the canonical `@app.route("/upload", methods=["POST"])`, whose method
comes out of the `methods=` keyword. E1 uses the first and E2 the second,
on real scans, and both are one site and one `0.872` edge — the
decorator is not part of the arithmetic, only the fact that the view is a
route is. `test_detector.py` pins the confirmation for both shapes.

E1 is the edge: `entry_point → store` at `combine_signals(
UPLOAD_WRITER_BASE, [equal literal, same module, context in body])` =
**0.872** — base 0.5 (the view accepts an upload), an equal literal, the
same module, and the write in the handler's own body.

The read side is pinned since B.3b-user_upload-attribution: the second
view reads the same literal in the writer's own file, so the scan creates
a `user_upload` source for it (ADR-0022 §1) and links the store to it
with `reads_from`. `test_attribution.py` asserts that state, and the rule
itself is pinned in `tests/test_store_upload_core/test_attribution.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aicorellator.ingestion import discover, ingest
from aicorellator.parsing import make_context, normalize, run_tsa
from aicorellator.parsing.capability_extractor import (
    extract as extract_capabilities,
)
from aicorellator.parsing.entry_points import run as run_entry_points
from aicorellator.store.confidence import (
    BASE,
    CODE_CONTEXT_SAME_MODULE,
    LITERAL_EQUAL,
    combine_signals,
)
from aicorellator.store.context.upload import registry
from aicorellator.store.context.upload.context import UserUploadContext
from aicorellator.store.context.upload.writer import (
    CONTEXT_IN_BODY,
    UPLOAD_WRITER_BASE,
)

MAIN = "app/views.py"
LITERAL = "/uploads/report.txt"

#: The view's `def` line — `FunctionDef.lineno`, and therefore
#: `UploadSite.line` — in the first two fixtures below.
UPLOAD_LINE = 8

#: The `with open(..., "rb")` line in `FILES`: the source node's
#: `line_start` and the read site's line.
READ_LINE = 19

FILES: dict[str, str] = {
    MAIN: '''\
"""A Flask view that accepts an upload and reads it back."""
from flask import Flask, request

app = Flask(__name__)


@app.post("/upload")
def upload_report():
    """Persist the upload."""
    uploaded = request.files["report"]
    with open("/uploads/report.txt", "wb") as fh:
        fh.write(uploaded.read())
    return "ok", 204


@app.get("/read")
def read_report():
    """Read it back."""
    with open("/uploads/report.txt", "rb") as fh:
        return fh.read()
''',
}

#: The same view with the upload and no write: a site, and no edge.
NO_WRITE_FILES: dict[str, str] = {
    MAIN: '''\
"""A Flask view that accepts an upload and writes nothing."""
from flask import Flask, request

app = Flask(__name__)


@app.post("/upload")
def upload_report():
    """Hand the upload on."""
    return request.files.get("report")
''',
}

#: A view that writes and reads nothing out of the request: no site.
NO_UPLOAD_FILES: dict[str, str] = {
    MAIN: '''\
"""A Flask view that takes no upload."""
from flask import Flask, request

app = Flask(__name__)


@app.post("/ping")
def ping():
    """Write something, accept nothing."""
    with open("/uploads/audit.txt", "wb") as fh:
        fh.write(b"ping")
    return "ok", 204
''',
}

#: E2 — the canonical Flask route shape, on a real scan.
ROUTE_METHODS_FILES: dict[str, str] = {
    MAIN: '''\
"""A Flask view decorated the way Flask documents it."""
from flask import Flask, request

app = Flask(__name__)


@app.route("/upload", methods=["POST"])
def upload_report():
    """Persist the upload."""
    uploaded = request.files["report"]
    with open("/uploads/report.txt", "wb") as fh:
        fh.write(uploaded.read())
    return "ok", 204
''',
}


def _scan(tmp_path: Path, files: dict[str, str], *, capabilities: bool = True):
    """The delta a real scan of `files` produces.

    The real chain, as `run_scan.py` runs it: `run_tsa` for the functions
    and classes the late stages read, `run_entry_points` for the routes,
    the call graph and the import table, `capability_extractor` for the
    `sink` node a `open(..., "wb")` earns, then `normalize`.

    `capabilities=False` is the same scan without the sink node. It is the
    shape in which the resolver's writer for a write inside a handler is
    the `entry_point` node itself (a `sink` node is what it looks for
    first), which is the collision the stage merges.
    """
    root = tmp_path / "target"
    for rel_path, source in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    target = ingest(root, kind="folder")
    manifest = discover(target)
    extracted = {
        entry.path: extract_capabilities(entry.abs_path, entry.path)
        for entry in manifest.files
        if entry.language == "python"
    } if capabilities else {}
    return normalize(make_context(
        manifest, run_tsa(manifest, target.root), run_entry_points(manifest),
        capabilities=extracted,
    ))


@pytest.fixture
def delta(tmp_path: Path):
    """The pipeline's output for the upload fixture, sink node included."""
    return _scan(tmp_path, FILES)


@pytest.fixture
def bare_delta(tmp_path: Path):
    """The same fixture, without capability extraction (no `sink` node)."""
    return _scan(tmp_path, FILES, capabilities=False)


def _of_kind(delta, kind: str) -> list:
    """The delta's nodes of one kind, in delta order."""
    return [node for node in delta.nodes if node.kind == kind]


def _entry(delta, name: str):
    """The `entry_point` node named `name` (`"POST /upload"`)."""
    return next(node for node in _of_kind(delta, "entry_point")
                if node.name == name)


def _sites(delta, source: str = FILES[MAIN]) -> list:
    """The sites the context identifies over the delta's own nodes.

    The file's AST is parsed from the fixture source: the detector reads
    the body, and that is the same AST the scan read (the stage hands the
    detector `ctx.module_for(file_path)`). The context is the scan's own —
    every registered detector — so the sites are what a scan reports.
    """
    nodes = [
        node for node in _of_kind(delta, "entry_point")
        if node.attributes.get("source_file") == MAIN
    ]
    return UserUploadContext().identify(ast.parse(source), MAIN, nodes)


# ============================================================
# E1 — the upload view and its store
# ============================================================

def test_the_detector_is_registered_for_the_scan() -> None:
    """The pipeline found it without this file naming it."""
    assert "flask" in registry.all_names()


def test_the_detector_finds_the_upload_view(delta) -> None:
    """E1a: one site, for the view that reads `request.files`.

    `GET /read` takes no request and reads nothing out of one, so it is
    not an upload site even though it is a route in the same file — the
    body is what decides. The site is the *view*: `line` is the `def`
    line, not the `request.files` access's.
    """
    sites = _sites(delta)
    assert len(sites) == 1
    site = sites[0]
    assert site.framework == "flask"
    assert site.file_path == MAIN
    assert site.line == UPLOAD_LINE
    assert site.entry_point.id == _entry(delta, "POST /upload").id
    assert site.entry_point.attributes["handler"] == "upload_report"


def test_the_upload_view_writes_the_store(delta) -> None:
    """E1: `entry_point → store`, at §8's revision-2 base.

    The write is in the handler's own body and in the handler's own file,
    so the four signals are base 0.5, an equal literal, the same module
    and the body: `1 − 0.5 × 0.4 × 0.8 × 0.8 = 0.872`. A declaration, not
    a data-flow verdict (§4, ADR-0016 decision 3).
    """
    entry_id = _entry(delta, "POST /upload").id
    store_id = _of_kind(delta, "store")[0].id
    upload = [
        edge for edge in delta.edges
        if edge.kind == "writes_to" and edge.source_id == entry_id
    ]
    assert len(upload) == 1
    edge = upload[0]
    assert edge.target_id == store_id
    assert round(edge.identity_confidence, 4) == 0.872
    assert edge.flow_kind == "structural"
    assert edge.observed_by == ["ast"]
    assert edge.data_flow_confirmed is None


def test_the_edge_number_comes_from_the_constants(delta) -> None:
    """The number is the four signals' arithmetic, not a literal.

    `combine_signals(UPLOAD_WRITER_BASE, [equal literal, same module,
    context in body])` — the same combination `test_store_upload_fastapi`
    and `test_store_upload_django` pin, because a Flask view's write is a
    function-level write like theirs.
    """
    expected = combine_signals(
        UPLOAD_WRITER_BASE,
        [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE, CONTEXT_IN_BODY],
    )
    entry_id = _entry(delta, "POST /upload").id
    edge = next(
        edge for edge in delta.edges
        if edge.kind == "writes_to" and edge.source_id == entry_id
    )
    assert round(expected, 4) == round(edge.identity_confidence, 4) == 0.872


def test_the_family_only_claim_is_kept_beside_the_upload_claim(delta) -> None:
    """The resolver's `sink → store` edge survives; the upload adds one.

    `open(..., "wb")` is a `writes_filesystem` sink, so the write's own
    component is a `sink` node and the resolver's writer is that node,
    not the handler. Two links into one store, from two different
    writers: the sink that makes the call (0.776, family-only §8) and the
    view that accepts the upload (0.872, revision 2). Both are true and
    they are not the same edge.
    """
    sink_id = next(
        node.id for node in _of_kind(delta, "sink")
        if node.name == "upload_report"
    )
    writes = {
        (edge.source_id, edge.target_id): edge
        for edge in delta.edges if edge.kind == "writes_to"
    }
    store_id = _of_kind(delta, "store")[0].id
    assert set(writes) == {
        (sink_id, store_id), (_entry(delta, "POST /upload").id, store_id),
    }
    assert round(writes[(sink_id, store_id)].identity_confidence, 4) == 0.776


def test_the_resolver_and_the_upload_writer_name_one_link_once(
    bare_delta,
) -> None:
    """Without a `sink` node the resolver merges the two into the stronger.

    A scan that extracted no capabilities has no `sink` node, so the
    resolver's own writer for the write inside the handler is the
    `entry_point` node — the same source the upload writer names. The
    stage merges them and keeps the upload writer's 0.872 over the
    family-only 0.776. Two rows here would mean the delta holds one link
    twice.
    """
    writes = [e for e in bare_delta.edges if e.kind == "writes_to"]
    assert len(writes) == 1
    assert writes[0].source_id == _entry(bare_delta, "POST /upload").id
    assert round(writes[0].identity_confidence, 4) == 0.872



def test_the_store_is_the_one_the_family_named(delta) -> None:
    """The store is §1's: one node per `(family, identity_hint)`."""
    stores = _of_kind(delta, "store")
    assert [node.name for node in stores] == [f"fs:{LITERAL}"]
    assert stores[0].attributes == {
        "family": "fs", "identity_hint": LITERAL,
    }
    assert stores[0].confidence == 1.0


def test_the_read_view_gets_its_source_and_links(delta) -> None:
    """The reader in the same file: a `user_upload` source and its links.

    The second view reads the literal the first view wrote, so the read
    site is a `user_upload` source (B.3b-user_upload-attribution,
    ADR-0022 §1): the scan creates the node, the store gets `reads_from`
    to it, and the source gets 1.3a's `has_access_to` edge to the reader —
    the `GET /read` entry point, resolved by the handler name.

    The reader's link is §8's reader side: base 0.3, an equal literal and
    the same module — `1 − 0.7 × 0.4 × 0.8 = 0.776`.
    """
    store = _of_kind(delta, "store")[0]
    sources = _of_kind(delta, "untrusted_source")
    assert [node.name for node in sources] == ["user_upload:open"]
    source = sources[0]
    assert source.attributes == {
        "source_type": "user_upload",
        "call_target": "open",
        "source_file": MAIN,
        "line_start": READ_LINE,
        "reader_kind": "function",
        "reader": "read_report",
    }

    reads_from = [e for e in delta.edges if e.kind == "reads_from"]
    assert [(e.source_id, e.target_id) for e in reads_from] == [
        (store.id, source.id)
    ]
    assert round(reads_from[0].identity_confidence, 4) == 0.776
    assert round(
        combine_signals(BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]), 4
    ) == 0.776
    assert not [
        node for node in delta.nodes
        if node.attributes.get("parent_server") is not None
    ]

    access = [
        e for e in delta.edges
        if e.kind == "has_access_to" and e.source_id == source.id
    ]
    assert [e.target_id for e in access] == [_entry(delta, "GET /read").id]


# ============================================================
# E2 — the canonical route shape, and the two negatives
# ============================================================

def test_a_route_methods_view_is_a_site(tmp_path: Path) -> None:
    """E2: `@app.route(..., methods=[...])` — one entry point, one site.

    The canonical Flask decorator is a route as of
    `B.3b-user_upload-route-detectors-followup`: the stage reads the
    method out of `methods=` (`POST`), the path out of the first
    positional argument (`/upload`), and the node is named exactly as the
    `@app.post` fixture's is. The detector matches it by handler, so the
    upload writer's edge is the same `0.872` — the decorator is not part
    of the arithmetic, only the fact that the view is a route is.
    """
    delta = _scan(tmp_path, ROUTE_METHODS_FILES)

    entries = _of_kind(delta, "entry_point")
    assert [node.name for node in entries] == ["POST /upload"]
    assert entries[0].attributes["path"] == "/upload"

    sites = _sites(delta, ROUTE_METHODS_FILES[MAIN])
    assert len(sites) == 1
    assert sites[0].framework == "flask"
    assert sites[0].line == UPLOAD_LINE
    assert sites[0].entry_point.id == entries[0].id

    store_id = _of_kind(delta, "store")[0].id
    upload = [
        edge for edge in delta.edges
        if edge.kind == "writes_to" and edge.source_id == entries[0].id
    ]
    assert len(upload) == 1
    assert upload[0].target_id == store_id
    assert round(upload[0].identity_confidence, 4) == 0.872


def test_a_view_with_no_write_is_a_site_with_no_edge(tmp_path: Path) -> None:
    """E2a: the site exists, the edge does not.

    The view accepts an upload and writes nothing, so no store is named
    and the writer's BFS from the handler finds no write site: one site,
    no `store` node, no `writes_to`. The view is still an upload site —
    being reachable by an HTTP client is the claim, not the write — which
    is what makes it a writer at all.
    """
    delta = _scan(tmp_path, NO_WRITE_FILES)

    sites = _sites(delta, NO_WRITE_FILES[MAIN])
    assert len(sites) == 1
    assert sites[0].line == UPLOAD_LINE
    assert _of_kind(delta, "store") == []
    assert [e for e in delta.edges if e.kind == "writes_to"] == []


def test_a_view_without_an_upload_is_no_site_and_no_edge(
    tmp_path: Path,
) -> None:
    """E2b: the same view shape, no `request.files`: not a writer.

    The write is real, so the `fs` family reports the store and the
    resolver builds the node — a store is a place, and the place exists —
    and the `sink` node the write earns gets the resolver's own
    family-only edge. What is missing is the upload writer's edge: the
    view accepts no upload, so the detector identifies no site and no
    `entry_point` claims the write.
    """
    delta = _scan(tmp_path, NO_UPLOAD_FILES)

    assert [node.name for node in _of_kind(delta, "store")] == [
        "fs:/uploads/audit.txt"
    ]
    assert _sites(delta, NO_UPLOAD_FILES[MAIN]) == []
    writes = [e for e in delta.edges if e.kind == "writes_to"]
    assert [e.source_id.split(":")[0] for e in writes] == ["sink"]
    assert not [
        edge for edge in writes
        if any(edge.source_id == node.id
               for node in _of_kind(delta, "entry_point"))
    ]


def test_two_scans_of_one_file_agree(tmp_path: Path) -> None:
    """The detector's answer is a function of the file and the registry."""
    first = _sites(_scan(tmp_path, FILES))
    second = _sites(_scan(tmp_path, FILES))

    assert [
        (site.entry_point.id, site.line, site.framework) for site in first
    ] == [
        (site.entry_point.id, site.line, site.framework) for site in second
    ]
