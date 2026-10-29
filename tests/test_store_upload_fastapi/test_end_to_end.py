"""The FastAPI detector through the pipeline (B.3b-user_upload-fastapi, E1–E2).

The fixture is a real FastAPI module: a `POST /upload` handler that takes
an `UploadFile` and writes `/uploads/report.txt`, and a `GET /read`
handler that reads the same literal back. The entry-point nodes are the
ones the *real* `entry_points` detector and `EntryPointStage` produce —
not hand-built — so what is pinned here is the whole seam: a route the
scan found, a signature the detector read, a store the `fs` family
recognized, and the `writes_to` edge that joins them.

What is **not** pinned as working is the attribution. `user_upload` is a
read *role*, and giving a filesystem read its role is the read side's
step (B.3, "`user_upload` source type"): 1.3a has no `open(...)` source
type, so a scan of this fixture produces no `untrusted_source` node for
the reader at all and `attribution.attribute_source_type` has nothing to
stamp. The test asserts that state — and `tests/test_store_upload_core/
test_attribution.py` pins the rule on nodes that do exist. Bending this
fixture to manufacture a source node would prove the wiring and lie about
the pipeline.
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
from aicorellator.store.context.upload import registry
from aicorellator.store.context.upload.context import UserUploadContext

MAIN = "app/main.py"
LITERAL = "/uploads/report.txt"

#: The handler's `def` line — `FunctionDef.lineno`, and therefore
#: `UploadSite.line` — in the fixture below.
UPLOAD_LINE = 8

FILES: dict[str, str] = {
    MAIN: '''\
"""A FastAPI app that accepts an upload and reads it back."""
from fastapi import FastAPI, UploadFile

app = FastAPI()


@app.post("/upload")
async def upload_report(file: UploadFile):
    """Persist the upload."""
    with open("/uploads/report.txt", "wb") as fh:
        fh.write(await file.read())
    return {"status": "ok"}


@app.get("/read")
def read_report():
    """Read it back."""
    with open("/uploads/report.txt", "rb") as fh:
        return fh.read()
''',
}

#: The same app with the upload in a helper that is not a route: nothing
#: an HTTP client can reach, so nothing it writes is an upload's store.
HELPER_FILES: dict[str, str] = {
    MAIN: '''\
"""A FastAPI app whose upload helper is not a route."""
from fastapi import FastAPI, UploadFile

app = FastAPI()


async def save_upload(file: UploadFile):
    """Not a route: nothing can reach it."""
    with open("/uploads/other.txt", "wb") as fh:
        fh.write(await file.read())


@app.get("/read")
def read_report():
    """Read it back."""
    with open("/uploads/report.txt", "rb") as fh:
        return fh.read()
''',
}


def _scan(tmp_path: Path, files: dict[str, str], *, capabilities: bool = True):
    """The delta a real scan of `files` produces.

    The real chain, as `run_scan.py` runs it: `run_tsa` for the functions
    and classes the late stages read, `run_entry_points` for the routes,
    the call graph and the import table, `capability_extractor` for the
    `sink` node a `open(..., "wb")` earns, then `normalize` for the
    pipeline — which is where `StoreResolverStage` and the upload step
    live. Nothing in this file registers a detector or names one:
    importing the store subsystem already did (`test_registration.py`
    pins that).

    `capabilities=False` is the same scan without the sink node. It is
    the shape in which the resolver's writer for a write inside a handler
    is the `entry_point` node itself (a `sink` node is what it looks for
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


def _sites(delta) -> list:
    """The sites the context identifies over the delta's own nodes.

    The file's AST is parsed from the fixture source: the detector reads
    the signature, and that is the same AST the scan read (the stage
    hands the detector `ctx.module_for(file_path)`).
    """
    nodes = [
        node for node in _of_kind(delta, "entry_point")
        if node.attributes.get("source_file") == MAIN
    ]
    return UserUploadContext().identify(ast.parse(FILES[MAIN]), MAIN, nodes)


# ============================================================
# E1 — the upload handler and its store
# ============================================================

def test_the_detector_is_registered_for_the_scan() -> None:
    """The pipeline found it without this file naming it."""
    assert "fastapi" in registry.all_names()


def test_the_detector_finds_the_upload_route(delta) -> None:
    """E1a: one site, for the handler that takes the `UploadFile`.

    `GET /read` takes nothing, so it is not an upload site even though it
    is a route in the same file — the signature is what decides.
    """
    sites = _sites(delta)
    assert len(sites) == 1
    site = sites[0]
    assert site.framework == "fastapi"
    assert site.file_path == MAIN
    assert site.line == UPLOAD_LINE
    assert site.entry_point.id == _entry(delta, "POST /upload").id
    assert site.entry_point.attributes["handler"] == "upload_report"


def test_the_upload_handler_writes_the_store(delta) -> None:
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


def test_the_family_only_claim_is_kept_beside_the_upload_claim(delta) -> None:
    """The resolver's `sink → store` edge survives; the upload adds one.

    `open(..., "wb")` is a `writes_filesystem` sink, so the write's own
    component is a `sink` node and the resolver's writer is that node,
    not the handler. Two links into one store, from two different
    writers: the sink that makes the call (0.776, family-only §8) and the
    handler that accepts the upload (0.872, revision 2). Both are true and
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
    """One link, one id, one row: the stronger claim wins.

    Without a `sink` node the resolver's writer for a write inside a
    handler is the `entry_point` node itself — the *same*
    `(entry_point, store, writes_to)` the upload writer names. An edge id
    is the graph's primary key, so the stage merges them and keeps the
    upload writer's 0.872 over the family-only 0.776. Two rows here would
    mean the delta holds one link twice.
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


def test_the_read_side_gets_no_edge_and_no_role(delta) -> None:
    """E2, as the pipeline holds it today: the writer edge, nothing more.

    A filesystem read is not one of 1.3a's source types, so the reader
    has no `untrusted_source` node — and giving a read its role is the
    read side's step (B.3). There is therefore nothing for
    `attribute_source_type` to stamp, which is the documented reason the
    attribution is inert end to end; the rule itself is pinned in
    `tests/test_store_upload_core/test_attribution.py`.
    """
    assert [e for e in delta.edges if e.kind == "reads_from"] == []
    assert [
        node for node in delta.nodes
        if node.kind == "untrusted_source"
    ] == []
    assert [
        node for node in delta.nodes
        if node.attributes.get("source_type") == "user_upload"
    ] == []


# ============================================================
# The negative: a handler that is not a route is not a writer
# ============================================================

def test_a_plain_helper_is_not_a_writer(tmp_path: Path) -> None:
    """The same signature, no route: no site, and no edge from an entry.

    The helper writes `/uploads/other.txt`, so the `fs` family reports
    the store and the resolver builds the node — a store is a place, and
    the place exists — and the `sink` node the write earns gets the
    resolver's own family-only edge, exactly as it would in a module with
    no upload in it at all. What is missing is the upload writer's edge:
    no client can call the helper, so the upload step identifies no site
    and no `entry_point` claims the write.
    """
    delta = _scan(tmp_path, HELPER_FILES)

    assert [
        node.name for node in _of_kind(delta, "store")
    ] == ["fs:/uploads/other.txt", f"fs:{LITERAL}"]
    assert _sites_any(delta) == []
    writes = [e for e in delta.edges if e.kind == "writes_to"]
    assert [e.source_id.split(":")[0] for e in writes] == ["sink"]
    assert not [
        edge for edge in writes
        if any(edge.source_id == node.id
               for node in _of_kind(delta, "entry_point"))
    ]


def _sites_any(delta) -> list:
    """The sites for the helper fixture (its own source, not `FILES`)."""
    nodes = [
        node for node in _of_kind(delta, "entry_point")
        if node.attributes.get("source_file") == MAIN
    ]
    return UserUploadContext().identify(
        ast.parse(HELPER_FILES[MAIN]), MAIN, nodes
    )
