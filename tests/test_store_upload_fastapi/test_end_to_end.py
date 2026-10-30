"""The FastAPI detector through the pipeline (B.3b-user_upload-fastapi, E1–E2).

The fixture is a real FastAPI module: a `POST /upload` handler that takes
an `UploadFile` and writes `/uploads/report.txt`, and a `GET /read`
handler that reads the same literal back. The entry-point nodes are the
ones the *real* `entry_points` detector and `EntryPointStage` produce —
not hand-built — so what is pinned here is the whole seam: a route the
scan found, a signature the detector read, a store the `fs` family
recognized, and the `writes_to` edge that joins them.

The read side is pinned since B.3b-user_upload-attribution: `GET /read`
reads the same literal in the writer's own file, so the scan *creates* a
`user_upload` source for it (ADR-0022 §1), links the store to it with
`reads_from`, and gives the source 1.3a's `has_access_to` edge to the
reader — the `GET /read` entry point, resolved by the handler name. The
second fixture adds a sink to the reader's path, which is the shape
`find_chain_groups` needs to pair the store's write chain with the read
chain (ADR-0021 §6).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aicorellator import GraphStore
from aicorellator.ingestion import discover, ingest
from aicorellator.parsing import make_context, normalize, run_tsa, write_graph
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

MAIN = "app/main.py"
LITERAL = "/uploads/report.txt"

#: The handler's `def` line — `FunctionDef.lineno`, and therefore
#: `UploadSite.line` — in the fixture below.
UPLOAD_LINE = 8

#: The read's own line and the call it is spelled with: the source node's
#: `line_start` and `call_target` (`attribution._call_target`).
READ_LINE = 18
READ_TARGET = "open"

#: The reader's link, from the constants: base 0.3 (the family's), an
#: equal literal, and the same module — §8's reader side (ADR-0022 §5).
READER_LINK = combine_signals(
    BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
)

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


#: The same app, with the reader's data on its way to a network sink. A
#: read chain has to end at a sink (ADR-0010), so this is the shape that
#: produces a group; the canonical fixture above stops at the read.
SINK_FILES: dict[str, str] = {
    MAIN: '''\
"""A FastAPI app that accepts an upload and posts what it reads back."""
import requests
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
    """Read it back and send it on."""
    with open("/uploads/report.txt", "rb") as fh:
        return requests.post("https://example.test/ingest", data=fh.read())
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


def test_the_read_side_gets_the_source_and_the_links(delta) -> None:
    """E2 — the read side is produced (B.3b-user_upload-attribution).

    `GET /read` reads the literal the upload wrote, in the writer's own
    file, so the read site is a `user_upload` source (ADR-0022 §1): the
    scan creates the node, the store gets `reads_from` to it, and the
    source gets 1.3a's `has_access_to` edge to the reader — the `GET
    /read` entry point, resolved by the handler name.

    The reader's link is §8's reader side (ADR-0022 §5): base 0.3 (the
    family's, not the writer's 0.5), an equal literal, the same module —
    `1 − 0.7 × 0.4 × 0.8 = 0.776`, which is also the pair's number,
    because the writer's 0.872 is higher and the join takes the `min`.
    """
    store = _of_kind(delta, "store")[0]
    sources = _of_kind(delta, "untrusted_source")
    assert [node.name for node in sources] == [f"user_upload:{READ_TARGET}"]
    source = sources[0]
    assert source.attributes == {
        "source_type": "user_upload",
        "call_target": READ_TARGET,
        "source_file": MAIN,
        "line_start": READ_LINE,
        "reader_kind": "function",
        "reader": "read_report",
    }

    reads_from = [e for e in delta.edges if e.kind == "reads_from"]
    assert len(reads_from) == 1
    edge = reads_from[0]
    assert (edge.source_id, edge.target_id) == (store.id, source.id)
    assert edge.flow_kind == "structural"
    assert edge.observed_by == ["ast"]
    assert edge.data_flow_confirmed is None
    assert round(edge.identity_confidence, 4) == 0.776
    assert round(READER_LINK, 4) == 0.776

    access = [
        e for e in delta.edges
        if e.kind == "has_access_to" and e.source_id == source.id
    ]
    assert [e.target_id for e in access] == [_entry(delta, "GET /read").id]


def test_the_group_joins_the_read_chain_to_the_write_chain(
    tmp_path: Path,
) -> None:
    """E1 — the store is the shared node (ADR-0021 §6).

    The write chain `POST /upload → store` and the read chain
    `source → sink` meet at the store's id, and the group's confidence is
    the weaker link — the reader's 0.776 against the writer's 0.872 — so
    it is above §9's default threshold and not weak.

    The fixture's writer reaches the store twice (the handler's own
    `writes_to` at 0.872 and the `open(..., "wb")` sink's family-only
    link at 0.776), which is why the store joins more than one write
    chain: one group per (write chain, read chain) pair, all on the same
    source and store.
    """
    delta = _scan(tmp_path, SINK_FILES)
    source = _of_kind(delta, "untrusted_source")[0]
    store = _of_kind(delta, "store")[0]

    with GraphStore(tmp_path / "graph.db") as graph:
        write_graph(graph, delta)
        groups = graph.query.find_chain_groups()

    assert groups
    assert {group.store_id for group in groups} == {store.id}
    assert {group.untrusted_source_id for group in groups} == {source.id}
    assert {round(group.store_confidence, 4) for group in groups} == {0.776}
    assert not any(group.weak for group in groups)
    assert all(
        chain[0] == source.id
        for group in groups
        for chain in group.read_chains
    )


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
