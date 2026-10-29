"""The Streamlit upload detector — shapes and non-shapes (D1–D7).

`StreamlitUploadDetector.detect` answers one question about one file: does
one of this file's entry points accept an upload? The answer is a module
question and not a handler question — a Streamlit app's entry point is the
module — so the detector reads the **entry-point node** and nothing else:

  - D1: `entry_kind="streamlit"` + `path="file_uploader"` → one site;
  - D2: the same kind with another Streamlit call name (`button`,
    `chat_input`, `form_submit_button`) → no site, because only
    `file_uploader` accepts a file;
  - D3–D5: a CLI node, an MCP node and an HTTP route node → no site;
  - D6: `button` and `file_uploader` in one file → one site;
  - D7: two `st.file_uploader` calls in one file → one site, because the
    stage records one entry point per distinct call name.

D6 and D7 run the **real** `entry_points` parse and `EntryPointStage`, so
what they pin is the pipeline's own node shape and the stage's own dedup;
the rest hand-build the nodes `EntryPointStage` produces, the way
`tests/test_store_upload_fastapi/test_detector.py` does. `tree` is passed
and not read — one test says so, because "the detector re-parses" would be
a second answer to a question the stage has answered.
"""

from __future__ import annotations

import ast

from aicorellator.graph.models import Node
from aicorellator.ingestion.discovery import FileEntry, FileManifest
from aicorellator.ingestion.target import Target
from aicorellator.parsing.ast_normalizer import GraphDelta
from aicorellator.parsing.entry_points import run as run_entry_points
from aicorellator.parsing.models import TSAOutput
from aicorellator.parsing.normalizer_context import make_context
from aicorellator.parsing.normalizer_stages import EntryPointStage
from aicorellator.store.context.upload.base import UploadDetector, UploadSite
from aicorellator.store.context.upload.detectors import streamlit as detector
from aicorellator.store.context.upload.detectors.streamlit import (
    ENTRY_KIND,
    UPLOAD_PATH,
    StreamlitUploadDetector,
)

FILE = "app/streamlit_app.py"

#: The line the fixture's `st.file_uploader` call is on, which is the line
#: `EntryPointStage` records and therefore `UploadSite.line`.
UPLOAD_LINE = 3

#: A Streamlit app with one upload and one click: two entry points, one
#: upload (D6).
SOURCE = '''\
import streamlit as st

uploaded = st.file_uploader("Upload a report")

if st.button("Send"):
    st.write("sent")
'''

#: The same app with two uploads and no other Streamlit call (D7).
TWO_UPLOADERS = '''\
import streamlit as st

first = st.file_uploader("First")
second = st.file_uploader("Second")
'''

#: A module with no Streamlit call at all, for the "the tree is not read"
#: test.
NO_STREAMLIT = '''\
def handle(payload):
    """Nothing Streamlit about this."""
    return payload
'''


def _node(
    path: str = UPLOAD_PATH,
    *,
    entry_kind: str = ENTRY_KIND,
    line: int = UPLOAD_LINE,
    file_path: str = FILE,
    node_id: str | None = None,
) -> Node:
    """The entry-point node `EntryPointStage` builds for a Streamlit call.

    The stage records `source_file`, `line_start`, `method`,
    `entry_kind` and `path`, and **no** `handler` — a Streamlit entry is
    the module, so there is no function to name.
    """
    return Node(
        id=node_id or f"entry_point:{path}", kind="entry_point",
        name=f"Streamlit:{file_path}:{path}", host_id="localhost",
        attributes={
            "source_file": file_path, "line_start": line,
            "method": "STREAMLIT", "entry_kind": entry_kind, "path": path,
        },
    )


def _cli() -> Node:
    """A CLI entry-point node, as the stage builds it."""
    return Node(
        id="entry_point:cli", kind="entry_point", name=f"CLI:{FILE}",
        host_id="localhost",
        attributes={
            "source_file": FILE, "line_start": 1, "method": "CLI",
            "entry_kind": "cli", "path": FILE,
        },
    )


def _mcp() -> Node:
    """An MCP entry-point node, as `mcp_entry_synthesis` builds it."""
    return Node(
        id="entry_point:mcp", kind="entry_point", name="MCP:findrive",
        host_id="localhost",
        attributes={
            "source_file": FILE, "mcp_server_name": "findrive",
            "framework": "fastmcp", "entry_kind": "mcp",
        },
    )


def _http() -> Node:
    """An HTTP route entry-point node, as the stage builds it."""
    return Node(
        id="entry_point:http", kind="entry_point", name="POST /upload",
        host_id="localhost",
        attributes={
            "source_file": FILE, "line_start": 9, "method": "POST",
            "path": "/upload", "handler": "upload_report",
        },
    )


def _sites(*nodes: Node, source: str = SOURCE) -> list[UploadSite]:
    """The sites `nodes` yield for one file's source."""
    return StreamlitUploadDetector().detect(
        ast.parse(source), FILE, list(nodes)
    )


def _stage_nodes(tmp_path, source: str) -> list[Node]:
    """The entry-point nodes the real parse and stage produce for `source`.

    Not hand-built: `run_entry_points` is the detector a scan runs and
    `EntryPointStage` the producer of the node, so D6 and D7 are pinned
    against the pipeline's own shapes — including the stage's dedup by
    call name, which is what makes two `st.file_uploader` calls one node.
    """
    root = tmp_path / "target"
    path = root / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    manifest = FileManifest(target=Target(root=root, kind="folder"))
    manifest.files.append(FileEntry(
        path=FILE, abs_path=path, language="python",
        content_hash="h0", size=len(source),
    ))
    ctx = make_context(
        manifest, TSAOutput(symbols=[]),
        entry_points=run_entry_points(manifest),
    )
    delta = GraphDelta()
    EntryPointStage().apply(delta, ctx)
    return [node for node in delta.nodes if node.kind == "entry_point"]


# ============================================================
# D1 — the shape
# ============================================================

def test_a_file_uploader_entry_is_one_site() -> None:
    """D1 — `entry_kind="streamlit"` + `path="file_uploader"`."""
    node = _node()

    sites = _sites(node)

    assert len(sites) == 1
    site = sites[0]
    assert site.entry_point is node
    assert site.file_path == FILE
    assert site.line == UPLOAD_LINE
    assert site.framework == "streamlit"


def test_the_site_names_the_module_and_not_a_handler() -> None:
    """D1 — the entry point is the module: the node carries no handler.

    The site is what the writer's module-level branch reads
    (`writer._module_level`, on `entry_kind`), and a `handler` here would
    send that writer down the function-level branch instead.
    """
    site = _sites(_node())[0]

    assert "handler" not in site.entry_point.attributes
    assert site.entry_point.attributes["entry_kind"] == ENTRY_KIND


def test_the_line_is_the_nodes_own() -> None:
    """D1 — `line` is `line_start`, the `st.file_uploader` call's line."""
    assert _sites(_node(line=31))[0].line == 31


# ============================================================
# D2 — a Streamlit entry that is not an upload
# ============================================================

def test_a_button_entry_is_not_an_upload() -> None:
    """D2 — a click accepts nothing."""
    assert _sites(_node("button")) == []


def test_a_chat_input_entry_is_not_an_upload() -> None:
    """D2 — a chat prompt is text, not a file."""
    assert _sites(_node("chat_input")) == []


def test_a_form_submit_button_is_not_an_upload() -> None:
    """D2 — the fourth `STREAMLIT_ENTRY_CALLS` member is a click too."""
    assert _sites(_node("form_submit_button")) == []


def test_an_unknown_streamlit_path_is_not_an_upload() -> None:
    """D2 — a call name outside the stage's set is not an upload either.

    A `path` this detector has never seen is a node the stage does not
    build today; the filter is an equality, so a future Streamlit call
    needs a line in this module and not a lucky match.
    """
    assert _sites(_node("camera_input")) == []


# ============================================================
# D3–D5 — the other entry kinds
# ============================================================

def test_a_cli_entry_is_not_a_streamlit_upload() -> None:
    """D3 — module-level, but not a Streamlit file uploader."""
    assert _sites(_cli()) == []


def test_an_mcp_entry_is_not_a_streamlit_upload() -> None:
    """D4 — `entry_kind="mcp"`, and no `path` at all."""
    assert _sites(_mcp()) == []


def test_an_http_route_entry_is_not_a_streamlit_upload() -> None:
    """D5 — the route carries `handler` and no `entry_kind`."""
    assert _sites(_http()) == []


def test_a_path_without_the_kind_is_not_a_streamlit_upload() -> None:
    """D5's twin: both attributes are the test, not either one.

    A hand-built node carrying `path="/upload"` and no `entry_kind` — the
    HTTP shape — is not a Streamlit entry, and neither is a node carrying
    the kind with a path the stage never records for a Streamlit call.
    """
    assert _sites(_node("/upload")) == []
    assert _sites(_node(UPLOAD_PATH, entry_kind="http")) == []


# ============================================================
# D6 — a button and an upload in one file
# ============================================================

def test_a_button_and_a_file_uploader_is_one_site(tmp_path) -> None:
    """D6 — one site, and it is the `file_uploader` node.

    The stage records one entry point per distinct call name, so this
    fixture has two nodes; the `button` node is an entry point that is
    not an upload and stays in the graph as one.
    """
    nodes = _stage_nodes(tmp_path, SOURCE)

    assert [node.attributes["path"] for node in nodes] == [
        UPLOAD_PATH, "button",
    ]

    sites = StreamlitUploadDetector().detect(
        ast.parse(SOURCE), FILE, nodes,
    )

    assert len(sites) == 1
    assert sites[0].entry_point is nodes[0]
    assert sites[0].line == UPLOAD_LINE


# ============================================================
# D7 — two uploads in one file
# ============================================================

def test_two_file_uploader_calls_are_one_site(tmp_path) -> None:
    """D7 — one entry point, because the stage dedupes by call name.

    Two `st.file_uploader` calls with two prompts are two calls to one
    call name, so the stage records one node and the detector returns one
    site. The site is the module: the writer's BFS is over the module
    body, and both writes are the module's.
    """
    nodes = _stage_nodes(tmp_path, TWO_UPLOADERS)

    assert len(nodes) == 1
    assert nodes[0].attributes["path"] == UPLOAD_PATH

    sites = StreamlitUploadDetector().detect(
        ast.parse(TWO_UPLOADERS), FILE, nodes,
    )

    assert len(sites) == 1
    assert sites[0].entry_point is nodes[0]
    assert sites[0].line == 3


# ============================================================
# The readings the brief leaves to the code
# ============================================================

def test_the_answer_is_a_function_of_the_nodes_and_not_of_the_tree() -> None:
    """The tree is not read: a node is the whole question.

    `tree` and `file_path` are part of the interface (the context hands
    every detector the same three arguments) and this detector reads
    neither — the node carries the file, the line and the call name. The
    source here declares no Streamlit call at all and the site is still
    reported, which is what "no walk of the tree" means.
    """
    node = _node(line=7)

    sites = _sites(node, source=NO_STREAMLIT)

    assert len(sites) == 1
    assert sites[0].file_path == node.attributes["source_file"]
    assert sites[0].line == 7


def test_the_sites_are_in_entry_point_order() -> None:
    """Determinism: `entry_points` order, which is delta order."""
    first = _node(node_id="a")
    second = _node(node_id="b")

    assert [site.entry_point.id for site in _sites(first, second)] == [
        "a", "b",
    ]
    assert _sites(first, second) == _sites(first, second)


def test_the_detector_declares_its_name() -> None:
    """The registry key, the class's `name` and the site's `framework`."""
    det = StreamlitUploadDetector()

    assert isinstance(det, UploadDetector)
    assert det.name == detector.NAME == "streamlit"
    assert _sites(_node())[0].framework == det.name


def test_the_marker_constants_are_the_stages_words() -> None:
    """`ENTRY_KIND` and `UPLOAD_PATH` are the stage's own spellings.

    Restated as literals on purpose: the constants are the module's
    contract with `entry_points.EntryPointStage`, and a rename on either
    side would silence the filter without failing anything else.
    """
    assert ENTRY_KIND == "streamlit"
    assert UPLOAD_PATH == "file_uploader"
    assert detector.__all__ == [
        "ENTRY_KIND", "NAME", "UPLOAD_PATH", "StreamlitUploadDetector",
    ]


