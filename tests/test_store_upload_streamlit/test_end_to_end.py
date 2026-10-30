"""The Streamlit detector through the pipeline (E1–E2).

The fixture is a real Streamlit module: a module-level
`st.file_uploader` and a module-body write of `/uploads/report.txt`. The
entry-point node is the one the *real* `entry_points` detector and
`EntryPointStage` produce — `entry_kind="streamlit"`, `path=
"file_uploader"`, no `handler` — so what is pinned here is the whole
seam: a call the scan found, a site the detector reported, a store the
`fs` family recognized, and the `writes_to` edge the writer's
**module-level** branch joins them with.

E1 is that edge: `entry_point → store` at `combine_signals(
UPLOAD_WRITER_BASE_MODULE, [equal literal, same module])` = **0.808** —
base 0.4 (the module body is the entry point's own body), an equal
literal, and the same module. The context-in-body signal does not fire: it
is the *handler's* body, and a Streamlit entry has no handler.

E2 is the second fixture, a file that holds a Streamlit upload **and** a
FastAPI route: the route is a function-level site and its own store is
0.872, and the Streamlit entry beside it earns exactly one edge — its own
module body's write. What that fixture used to show, and no longer does,
is the module-level site reaching the route handler's store: the
module-level reach starts from the names the module **body** calls, and a
`@app.post` decorator registers a function without calling it
(B.3b-user_upload-core-module-followup-2). The old pin
(`test_the_module_level_site_reaches_the_routes_function`) is now the
negative — `..._does_not_reach_...` — and the positive beside it is
`test_the_module_level_site_reaches_a_helper_it_calls`, whose module body
does call the function that writes.

What is **not** pinned as working is the attribution: `user_upload` is a
read *role*, and 1.3a has no `open(...)` source type, so a scan of this
fixture produces no `untrusted_source` node for the reader at all. The
state is asserted here and the rule itself is pinned in
`test_attribution.py`.
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
    CODE_CONTEXT_SAME_MODULE,
    LITERAL_EQUAL,
    combine_signals,
)
from aicorellator.store.context.upload import registry
from aicorellator.store.context.upload.context import UserUploadContext
from aicorellator.store.context.upload.writer import (
    UPLOAD_WRITER_BASE,
    UPLOAD_WRITER_BASE_MODULE,
)

MAIN = "app/streamlit_app.py"
LITERAL = "/uploads/report.txt"
API_LITERAL = "/uploads/api_report.txt"
HELPER_LITERAL = "/uploads/archive.txt"

#: The `st.file_uploader` call's line — `EntryPointStage`'s `line_start`,
#: and therefore `UploadSite.line` — in both fixtures below.
UPLOAD_LINE = 4
ROUTE_UPLOAD_LINE = 7

#: The route handler's `def` line — `FunctionDef.lineno`, and therefore
#: `UploadSite.line` for the `fastapi` site.
ROUTE_LINE = 14

FILES: dict[str, str] = {
    MAIN: '''\
"""A Streamlit app that accepts an upload and reads it back."""
import streamlit as st

uploaded = st.file_uploader("Upload a report")

with open("/uploads/report.txt", "wb") as fh:
    fh.write(uploaded.getbuffer())


def read_back():
    """Read it back."""
    with open("/uploads/report.txt", "rb") as fh:
        return fh.read()
''',
}

#: The same app plus a FastAPI route that accepts an upload of its own:
#: two entry points, two sites, two writers (E2).
ROUTE_FILES: dict[str, str] = {
    MAIN: '''\
"""A file with a Streamlit upload and a FastAPI route."""
import streamlit as st
from fastapi import FastAPI, UploadFile

app = FastAPI()

uploaded = st.file_uploader("Upload a report")

with open("/uploads/report.txt", "wb") as fh:
    fh.write(uploaded.getbuffer())


@app.post("/upload")
async def upload_report(file: UploadFile):
    """Persist the upload."""
    with open("/uploads/api_report.txt", "wb") as fh:
        fh.write(await file.read())
    return {"status": "ok"}
''',
}

#: A Streamlit app whose module body **calls** a helper that writes: the
#: positive beside `ROUTE_FILES`' negative (X2). The module body's own
#: `st.file_uploader` call is an attribute call and names nothing, so the
#: helper is the only start name the body has.
CALLED_HELPER_FILES: dict[str, str] = {
    MAIN: '''\
"""A Streamlit app that hands the upload to a helper."""
import streamlit as st

uploaded = st.file_uploader("Upload a report")


def save_report(payload):
    """Persist the upload."""
    with open("/uploads/archive.txt", "wb") as fh:
        fh.write(payload)


save_report(uploaded)
''',
}

#: A Streamlit app whose only Streamlit call is a button: module-level,
#: and not an upload. The file still writes a store, so the store node
#: exists — the missing thing is the writer.
BUTTON_FILES: dict[str, str] = {
    MAIN: '''\
"""A Streamlit app that shows a button and writes a file."""
import streamlit as st

if st.button("Send"):
    with open("/uploads/report.txt", "wb") as fh:
        fh.write(b"sent")
''',
}


def _scan(tmp_path: Path, files: dict[str, str], *, capabilities: bool = True):
    """The delta a real scan of `files` produces.

    The real chain, as `run_scan.py` runs it: `run_tsa` for the functions
    and classes the late stages read, `run_entry_points` for the entry
    points, the call graph and the import table, `capability_extractor`
    for the `sink` nodes, then `normalize` for the pipeline — which is
    where `StoreResolverStage` and the upload step live. Nothing in this
    file registers a detector or names one: importing the store subsystem
    already did (`test_registration.py` pins that).
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
    """The pipeline's output for the Streamlit fixture."""
    return _scan(tmp_path, FILES)


@pytest.fixture
def route_delta(tmp_path: Path):
    """The pipeline's output for the Streamlit **and** FastAPI fixture."""
    return _scan(tmp_path, ROUTE_FILES)


def _of_kind(delta, kind: str) -> list:
    """The delta's nodes of one kind, in delta order."""
    return [node for node in delta.nodes if node.kind == kind]


def _entry(delta, name: str):
    """The `entry_point` node named `name`."""
    return next(node for node in _of_kind(delta, "entry_point")
                if node.name == name)


def _store(delta, hint: str):
    """The `store` node whose identity hint is `hint`."""
    return next(node for node in _of_kind(delta, "store")
                if node.attributes.get("identity_hint") == hint)


def _sites(delta, source: str = FILES[MAIN]) -> list:
    """The sites the context identifies over the delta's own nodes."""
    nodes = [
        node for node in _of_kind(delta, "entry_point")
        if node.attributes.get("source_file") == MAIN
    ]
    return UserUploadContext().identify(ast.parse(source), MAIN, nodes)


def _writes(delta, source_id: str, target_id: str):
    """The one `writes_to` edge from `source_id` to `target_id`."""
    return [
        edge for edge in delta.edges
        if edge.kind == "writes_to" and edge.source_id == source_id
        and edge.target_id == target_id
    ]


# ============================================================
# E1 — the Streamlit upload and its store
# ============================================================

def test_the_detector_is_registered_for_the_scan() -> None:
    """The pipeline found it without this file naming it."""
    assert "streamlit" in registry.all_names()


def test_the_detector_finds_the_upload(delta) -> None:
    """E1a: one site, for the module whose `st.file_uploader` it is.

    The site is the module — `line` is the call's line, and the entry
    point carries `entry_kind="streamlit"` and no `handler`, which is
    what the writer's module-level branch reads.
    """
    sites = _sites(delta)

    assert len(sites) == 1
    site = sites[0]
    assert site.framework == "streamlit"
    assert site.file_path == MAIN
    assert site.line == UPLOAD_LINE
    assert site.entry_point.id == _entry(
        delta, f"Streamlit:{MAIN}:file_uploader"
    ).id
    assert site.entry_point.attributes["entry_kind"] == "streamlit"
    assert "handler" not in site.entry_point.attributes


def test_the_module_body_write_is_the_uploads_store(delta) -> None:
    """E1: `entry_point → store`, at the module-level writer's base.

    The write is in the module body of the entry point's own file, so the
    signals are base 0.4, an equal literal and the same module:
    `1 − (1 − 0.4)(1 − 0.6)(1 − 0.2) = 0.808`. A declaration, not a
    data-flow verdict (§4, ADR-0016 decision 3).
    """
    entry = _entry(delta, f"Streamlit:{MAIN}:file_uploader")
    store = _store(delta, LITERAL)

    found = _writes(delta, entry.id, store.id)

    assert len(found) == 1
    edge = found[0]
    assert round(edge.identity_confidence, 4) == 0.808
    assert edge.flow_kind == "structural"
    assert edge.observed_by == ["ast"]
    assert edge.data_flow_confirmed is None


def test_the_edge_number_comes_from_the_constants(delta) -> None:
    """E1's number is `combine_signals`, not the brief's prose sum.

    The brief's diagram adds the signals (0.4 + 0.6 + 0.2) and states
    0.896; the rule is `combine_signals`, which is what the writer calls
    and what `test_writer_module.py` pins. Restated here from the
    constants so a change to either is a visible change to the number.
    """
    entry = _entry(delta, f"Streamlit:{MAIN}:file_uploader")
    store = _store(delta, LITERAL)

    expected = combine_signals(
        UPLOAD_WRITER_BASE_MODULE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE],
    )

    assert round(_writes(delta, entry.id, store.id)[0].identity_confidence,
                 4) == round(expected, 4)
    assert round(expected, 4) == 0.808
    assert round(expected, 4) != 0.896


def test_the_store_is_the_one_the_family_named(delta) -> None:
    """The store is §1's: one node per `(family, identity_hint)`."""
    stores = _of_kind(delta, "store")

    assert [node.name for node in stores] == [f"fs:{LITERAL}"]
    assert stores[0].attributes == {
        "family": "fs", "identity_hint": LITERAL,
    }
    assert stores[0].confidence == 1.0


def test_the_two_scans_of_one_file_agree(tmp_path: Path) -> None:
    """Determinism: the same fixture, scanned twice, is the same delta."""
    first = _scan(tmp_path / "one", FILES)
    second = _scan(tmp_path / "two", FILES)

    def shape(scanned) -> list:
        return sorted(
            (edge.source_id, edge.target_id, edge.kind)
            for edge in scanned.edges
        )

    assert shape(first) == shape(second)
    assert [(n.kind, n.name) for n in first.nodes] == [
        (n.kind, n.name) for n in second.nodes
    ]


# ============================================================
# E2 — a FastAPI route in the same file
# ============================================================

def test_the_streamlit_site_is_the_module_in_the_mixed_file(
    route_delta,
) -> None:
    """E2 — the Streamlit site is unchanged by the route beside it."""
    sites = _sites(route_delta, ROUTE_FILES[MAIN])

    streamlit = [site for site in sites if site.framework == "streamlit"]

    assert len(streamlit) == 1
    assert streamlit[0].line == ROUTE_UPLOAD_LINE
    assert "handler" not in streamlit[0].entry_point.attributes


def test_the_route_in_the_same_file_is_a_function_level_site(
    route_delta,
) -> None:
    """E2 — the FastAPI route is a site of its own, at 0.872.

    The two detectors see two different nodes: the route's entry point
    carries a `handler` and no `entry_kind`, and its write is in the
    handler's body — base 0.5, an equal literal, the same module and the
    body: `1 − (1 − 0.5)(1 − 0.6)(1 − 0.2)(1 − 0.2) = 0.872`. The
    Streamlit entry beside it changes nothing about that number.
    """
    sites = _sites(route_delta, ROUTE_FILES[MAIN])
    fastapi = [site for site in sites if site.framework == "fastapi"]

    assert len(fastapi) == 1
    assert fastapi[0].line == ROUTE_LINE

    entry = _entry(route_delta, "POST /upload")
    store = _store(route_delta, API_LITERAL)
    found = _writes(route_delta, entry.id, store.id)

    assert len(found) == 1
    assert round(found[0].identity_confidence, 4) == 0.872
    assert round(
        combine_signals(
            UPLOAD_WRITER_BASE,
            [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE, 0.2],
        ),
        4,
    ) == 0.872


def test_the_module_level_site_does_not_reach_the_routes_function(
    route_delta,
) -> None:
    """M2/E2 — the route handler is not the Streamlit upload's writer.

    The module-level branch's start set is the names the module **body**
    calls (`writer._module_body_calls`,
    B.3b-user_upload-core-module-followup-2), and the body of this file
    never calls `upload_report` — a `@app.post` decorator registers a
    function, it does not call it. So the route handler's store is not in
    the Streamlit site's reach, and the Streamlit upload earns no edge
    into it.

    This is the negative the followup step exists for. Before it, the
    start set was the module's list of top-level functions, and the route
    handler — being one — handed the Streamlit upload a claim on the
    route's store at 0.84. That was a false positive and it is pinned as
    gone here rather than deleted, so a regression re-opens this test.
    """
    entry = _entry(route_delta, f"Streamlit:{MAIN}:file_uploader")
    store = _store(route_delta, API_LITERAL)

    found = _writes(route_delta, entry.id, store.id)

    assert found == []


def test_the_module_level_site_reaches_a_helper_it_calls(
    tmp_path: Path,
) -> None:
    """X2 — a function the module body calls **is** in the reach.

    The positive beside the negative above, on a real file: the module
    body ends with `save_report(uploaded)`, `save_report` writes
    `/uploads/archive.txt`, and the Streamlit site claims that store at
    the function base 0.5 — `1 − (1 − 0.5)(1 − 0.6)(1 − 0.8) = 0.84`. The
    narrowing is about *which* functions the body reaches, not about
    whether the writer follows a call at all.
    """
    delta = _scan(tmp_path, CALLED_HELPER_FILES)

    entry = _entry(delta, f"Streamlit:{MAIN}:file_uploader")
    store = _store(delta, HELPER_LITERAL)
    found = _writes(delta, entry.id, store.id)

    assert len(found) == 1
    assert round(found[0].identity_confidence, 4) == 0.84
    assert round(
        combine_signals(
            UPLOAD_WRITER_BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE],
        ),
        4,
    ) == 0.84


def test_the_mixed_file_has_two_writers_and_two_upload_edges(
    route_delta,
) -> None:
    """The whole shape of the mixed file, in one place.

    Two writers and two edges, not three: the Streamlit site's reach is
    the module body's — its own write of `/uploads/report.txt` at 0.808 —
    and the route handler's store belongs to the route handler alone, at
    0.872. The Streamlit entry is not silently credited with the route's
    store (B.3b-user_upload-core-module-followup-2).
    """
    entries = {
        node.id: node.name for node in _of_kind(route_delta, "entry_point")
    }
    stores = {
        node.id: node.attributes["identity_hint"]
        for node in _of_kind(route_delta, "store")
    }
    upload = sorted(
        (entries[edge.source_id], stores[edge.target_id],
         round(edge.identity_confidence, 4))
        for edge in route_delta.edges
        if edge.kind == "writes_to" and edge.source_id in entries
    )

    assert upload == [
        ("POST /upload", API_LITERAL, 0.872),
        (f"Streamlit:{MAIN}:file_uploader", LITERAL, 0.808),
    ]


# ============================================================
# The negative: a Streamlit entry that is not an upload
# ============================================================

def test_a_button_only_app_declares_no_upload(tmp_path: Path) -> None:
    """A Streamlit file with no `st.file_uploader` is not a writer.

    The entry-point node the stage builds is still there — `button` is a
    Streamlit entry — and the store node is still there, because the `fs`
    family named the literal the module body writes. What is missing is
    the upload: no site, and therefore no `writes_to` edge from any entry
    point.
    """
    delta = _scan(tmp_path, BUTTON_FILES)

    entries = _of_kind(delta, "entry_point")
    assert [node.attributes["path"] for node in entries] == ["button"]
    assert [node.name for node in _of_kind(delta, "store")] == [
        f"fs:{LITERAL}"
    ]

    assert _sites(delta, BUTTON_FILES[MAIN]) == []
    assert not [
        edge for edge in delta.edges
        if edge.kind == "writes_to"
        and edge.source_id in {node.id for node in entries}
    ]


