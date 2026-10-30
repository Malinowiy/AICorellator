"""Attribution for the Flask site — the view's file is the scope (A1).

`attribute_source_type` gives the read sites in a writer's scope the
`user_upload` role. A **function-level** site's scope is its **file** —
the same-file clause, and not the handler's line range: the reads an
upload handler's file makes are the handler's neighbourhood, and the
ADR's scope rule is the file. So a reader in the view's own file is in
scope and a reader elsewhere is not. The rule itself is
`tests/test_store_upload_core/test_attribution.py`'s and
`tests/test_store_upload_core_module/test_attribution_module.py`'s; what
this file pins is that the site **this detector reports** is the shape
that rule reads, and what the pipeline holds today.

End to end the read side is produced since
B.3b-user_upload-attribution: the reader in the view's own file is in the
site's scope, so the scan creates the `user_upload` source and the links
that name it — the store's `reads_from` and the source's
`has_access_to` to the `GET /read` entry point. The last test asserts
that state; the rule itself is pinned in
`tests/test_store_upload_core/test_attribution.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from aicorellator.graph.models import Node
from aicorellator.ingestion import discover, ingest
from aicorellator.parsing import make_context, normalize, run_tsa
from aicorellator.parsing.ast_normalizer import GraphDelta
from aicorellator.parsing.entry_points import run as run_entry_points
from aicorellator.store.confidence import (
    BASE,
    CODE_CONTEXT_SAME_MODULE,
    LITERAL_EQUAL,
    combine_signals,
)
from aicorellator.store.context.upload.attribution import (
    SOURCE_TYPE,
    attribute_source_type,
)
from aicorellator.store.context.upload.context import UserUploadContext

MAIN = "app/views.py"
OTHER = "app/worker.py"
LITERAL = "/uploads/report.txt"

#: The `with open(..., "rb")` line in `FILES`: the source node's
#: `line_start` and the read site's line.
READ_LINE = 18

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


@app.get("/read")
def read_report():
    """Read it back."""
    with open("/uploads/report.txt", "rb") as fh:
        return fh.read()
''',
    OTHER: '''\
"""A module that reads the same literal and shares nothing else."""


def read_it():
    """Read it."""
    with open("/uploads/report.txt", "rb") as fh:
        return fh.read()
''',
}


def _scan(tmp_path: Path, files: dict[str, str]):
    """The delta a real scan of `files` produces (the real chain)."""
    root = tmp_path / "target"
    for rel_path, source in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    target = ingest(root, kind="folder")
    manifest = discover(target)
    return normalize(make_context(
        manifest, run_tsa(manifest, target.root), run_entry_points(manifest),
    ))


def _site(delta, source: str = FILES[MAIN]):
    """The one Flask site the context identifies over `delta`."""
    nodes = [
        node for node in delta.nodes
        if node.kind == "entry_point"
        and node.attributes.get("source_file") == MAIN
    ]
    return UserUploadContext().identify(ast.parse(source), MAIN, nodes)[0]


def _reader(
    node_id: str,
    file_path: str,
    line: int = 20,
    source_type: str | None = None,
) -> Node:
    """A read site's node — 1.3a's shape, not this step's."""
    attributes: dict = {"source_file": file_path, "line_start": line}
    if source_type is not None:
        attributes["source_type"] = source_type
    return Node(
        id=node_id, kind="untrusted_source", name="user_upload:open",
        host_id="localhost", attributes=attributes,
    )


# ============================================================
# A1 — the site the detector reports is the scope the rule reads
# ============================================================

def test_a_reader_in_the_views_own_file_is_in_scope(tmp_path: Path) -> None:
    """A1 — the site's file is the reader's file, so the role applies.

    The site is the real one (the pipeline's own node, the detector's own
    reading) and the reader is the node 1.3a would build: this is the
    seam, with the fixture writing only the half the pipeline does not
    produce today.
    """
    delta = _scan(tmp_path, FILES)
    site = _site(delta)
    reader = _reader("untrusted_source:1", MAIN)

    changed = attribute_source_type(
        delta=GraphDelta(nodes=[reader], edges=[]),
        upload_sites=[site],
        imports_by_file={},
    )

    assert changed == [reader]
    assert reader.attributes["source_type"] == SOURCE_TYPE == "user_upload"


def test_a_reader_in_another_file_is_not_in_scope(tmp_path: Path) -> None:
    """A1's limit — another file, no shared import, no role.

    The scope is the handler's file, not the modules that read the same
    literal: the store is what joins the two sides, and the role is the
    upload's neighbourhood.
    """
    delta = _scan(tmp_path, FILES)
    site = _site(delta)
    reader = _reader("untrusted_source:2", OTHER)

    changed = attribute_source_type(
        delta=GraphDelta(nodes=[reader], edges=[]),
        upload_sites=[site],
        imports_by_file={},
    )

    assert changed == []
    assert "source_type" not in reader.attributes


def test_a_reader_that_already_has_a_role_keeps_it(tmp_path: Path) -> None:
    """A1's precedence clause: the first writer's scope wins.

    A node that already carries a `source_type` is not overwritten, which
    is what keeps B.2's `mcp_tool_description` from being relabelled by a
    view that happens to sit in the same module.
    """
    delta = _scan(tmp_path, FILES)
    site = _site(delta)
    reader = _reader("untrusted_source:3", MAIN, source_type="rag_corpus")

    changed = attribute_source_type(
        delta=GraphDelta(nodes=[reader], edges=[]),
        upload_sites=[site],
        imports_by_file={},
    )

    assert changed == []
    assert reader.attributes["source_type"] == "rag_corpus"


def test_with_no_site_nothing_is_attributed(tmp_path: Path) -> None:
    """A module with no upload identifies no site, so it stamps nothing."""
    delta = _scan(tmp_path, FILES)
    reader = _reader("untrusted_source:1", MAIN)

    changed = attribute_source_type(
        delta=GraphDelta(nodes=[reader], edges=[]),
        upload_sites=[],
        imports_by_file={},
    )

    assert changed == []
    assert "source_type" not in reader.attributes


# ============================================================
# A1 — the pipeline's own read side
# ============================================================

def test_the_pipeline_builds_the_source_and_its_links(tmp_path: Path) -> None:
    """A1 end to end: the view's read is a `user_upload` source.

    The reader is in the site's own file, so the scan creates the source
    (B.3b-user_upload-attribution, ADR-0022 §1), links the store the view
    wrote to, and hangs the source off the `GET /read` entry point — the
    read's enclosing function is that route's handler.

    The reader's link is §8's reader side: base 0.3, an equal literal and
    the same module — `1 − 0.7 × 0.4 × 0.8 = 0.776`.
    """
    delta = _scan(tmp_path, FILES)

    sources = [
        node for node in delta.nodes if node.kind == "untrusted_source"
    ]
    assert [node.name for node in sources] == ["user_upload:open"]
    source = sources[0]
    assert source.attributes["source_type"] == SOURCE_TYPE == "user_upload"
    assert source.attributes["call_target"] == "open"
    assert source.attributes["source_file"] == MAIN
    assert source.attributes["line_start"] == READ_LINE
    assert source.attributes["reader_kind"] == "function"
    assert source.attributes["reader"] == "read_report"

    store = next(node for node in delta.nodes if node.kind == "store")
    reads_from = [edge for edge in delta.edges if edge.kind == "reads_from"]
    assert [(edge.source_id, edge.target_id) for edge in reads_from] == [
        (store.id, source.id)
    ]
    assert round(reads_from[0].identity_confidence, 4) == 0.776
    assert round(
        combine_signals(BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]), 4
    ) == 0.776

    readers = [
        node for node in delta.nodes
        if node.kind == "entry_point"
        and node.attributes.get("handler") == "read_report"
    ]
    access = [
        edge for edge in delta.edges
        if edge.kind == "has_access_to" and edge.source_id == source.id
    ]
    assert [edge.target_id for edge in access] == [readers[0].id]
