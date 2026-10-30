"""The upload attribution — the read sites in a writer's scope (A1–A4).

ADR-0021 §10 keeps the *role* on the `untrusted_source` and the
*technology* on the store, and this is what sets the role for an upload:
a source node in the upload writer's file, or in a file that file shares
an import path with, becomes `user_upload` — unless it already carries a
`source_type`, in which case the first writer's scope wins.

The scope is the handler's file and not the stores it was found to write
into, so the A-tests are about files and imports and need no stores at
all. The delta is hand-built: a read site's node is 1.3a's, and the
stamping half only edits one attribute on it.

The B-tests are B.3b-user_upload-attribution's other half — the creation
(ADR-0022 §1–§5) — and they need the three things that half reads: the
`fs` family's read sites, the store the writer's `writes_to` edge names,
and the parsed module the read lives in. The read sites come from
`FsFamily.detect_reads` rather than being hand-built, so what is pinned is
the shape the pipeline actually hands over.

The C-tests are B.3b-user_upload-attribution-followup: §8's code-context
rows on the reader's `reads_from` — the shared wrapper (+0.3), the same
module (+0.2) and the shared import (+0.1), the strongest first, one row
per pair — read off the entry-point import table and the parsed module
bodies. The numbers are computed from `confidence`'s constants, and the
one case the pipeline cannot reach (no row at all, `_code_context`'s
`0.0`) is pinned on the row itself.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping

from aicorellator.graph.models import Edge, Node
from aicorellator.parsing.ast_normalizer import GraphDelta
from aicorellator.parsing.entry_points import EntryPointOutput
from aicorellator.parsing.normalizer_stages.store_resolver import _modules_for
from aicorellator.store.confidence import (
    BASE,
    CODE_CONTEXT_SAME_MODULE,
    CODE_CONTEXT_SHARED_IMPORT,
    CODE_CONTEXT_SHARED_WRAPPER,
    LITERAL_EQUAL,
    combine_signals,
)
from aicorellator.store.context.upload.attribution import (
    SOURCE_TYPE,
    WRAPPER_CALLS,
    _code_context,
    attribute_source_type,
)
from aicorellator.store.context.upload.base import UploadSite
from aicorellator.store.families.fs import FsFamily

HANDLER = "app/routes.py"
READER = "app/tools.py"
UNRELATED = "app/other.py"


def _source(
    node_id: str,
    file_path: str,
    source_type: str | None = None,
) -> Node:
    attributes: dict[str, object] = {
        "source_file": file_path, "line_start": 3,
    }
    if source_type is not None:
        attributes["source_type"] = source_type
    return Node(
        id=node_id, kind="untrusted_source", name="src:open",
        host_id="localhost", attributes=attributes,
    )


def _site(file_path: str = HANDLER) -> UploadSite:
    entry = Node(
        id="entry_point:1", kind="entry_point", name="POST /upload",
        host_id="localhost",
        attributes={"source_file": file_path, "handler": "handle"},
    )
    return UploadSite(
        entry_point=entry, file_path=file_path, line=1, framework="fake",
    )


def _attribute(nodes: list[Node], *, sites=None, imports=None) -> list[Node]:
    return attribute_source_type(
        delta=GraphDelta(nodes=nodes, edges=[]),
        upload_sites=[_site()] if sites is None else sites,
        imports_by_file=imports or {},
    )


# ============================================================
# B — the creation (B.3b-user_upload-attribution)
# ============================================================

#: The writer's file: the upload handler, and a second function that
#: reads the same literal back. This is the canonical shape (a FastAPI
#: app with `POST /upload` and `GET /read` in one module).
SOURCE = '''\
def handle(payload):
    with open("/uploads/report.txt", "wb") as fh:
        fh.write(payload)


def read_back():
    with open("/uploads/report.txt", "rb") as fh:
        return fh.read()
'''
WRITE_LINE = 2
READ_LINE = 7

#: A reader in another module: the same literal, nothing else.
OTHER_SOURCE = '''\
def read_it():
    with open("/uploads/report.txt", "rb") as fh:
        return fh.read()
'''

#: A reader that names no store: the path is a parameter.
NAMELESS_SOURCE = '''\
def read_it(path):
    with open(path, "rb") as fh:
        return fh.read()
'''

#: A reader that names a *different* store.
ELSEWHERE_SOURCE = '''\
def read_it():
    with open("/elsewhere/report.txt", "rb") as fh:
        return fh.read()
'''

#: A writer with no read of its own: the store exists, the reader does
#: not, so there is nothing to link.
WRITER_ONLY_SOURCE = '''\
def handle(payload):
    with open("/uploads/report.txt", "wb") as fh:
        fh.write(payload)
'''

LITERAL = "/uploads/report.txt"

#: The writer's link, as the writer stage computes it (base 0.5, an equal
#: literal, the same module, the write in the handler's body). Only its
#: *size* matters here: the pair is the smaller of the two links.
WRITER_LINK = 0.872


def _entry(handler: str = "handle", file_path: str = HANDLER) -> Node:
    """The writer's entry point, as the pipeline's stage builds it."""
    return Node(
        id=f"entry_point:{handler}", kind="entry_point",
        name=f"POST {handler}", host_id="localhost",
        attributes={"source_file": file_path, "handler": handler},
    )


def _reader_entry(handler: str = "read_back", file_path: str = HANDLER) -> Node:
    """The reader's entry point — a route in the writer's own file."""
    return Node(
        id=f"entry_point:{handler}", kind="entry_point",
        name=f"GET {handler}", host_id="localhost",
        attributes={"source_file": file_path, "handler": handler},
    )


def _store(hint: str = LITERAL) -> Node:
    """A store node, as the resolver builds it (§1)."""
    return Node(
        id=f"store:{hint}", kind="store", name=f"fs:{hint}",
        host_id="localhost",
        attributes={"family": "fs", "identity_hint": hint},
    )


def _writes_to(entry: Node, store: Node) -> Edge:
    """The writer's edge, as the writer stage emits it (§3)."""
    return Edge(
        id=f"edge:writer:{store.id}", source_id=entry.id,
        target_id=store.id, kind="writes_to", host_id="localhost",
        flow_kind="structural", identity_confidence=WRITER_LINK,
    )


def _build(
    *,
    files: dict[str, str],
    store: Node | None = None,
    entry: Node | None = None,
    imports: dict[str, list[str]] | None = None,
    nodes: list[Node] | None = None,
    modules: bool | Mapping[str, ast.Module] = True,
) -> tuple[GraphDelta, list[Node]]:
    """A delta with one writer, its store, and the files' `fs` read sites.

    The entry point and the reader's entry point are in the delta because
    both halves read it: the writer's `writes_to` edge is looked up by the
    entry point's id, and the reader of a created source is resolved
    against the delta's nodes (`attribution._reader_index`). `modules` off
    is the "the caller parsed nothing" mode; a **mapping** is what the
    stage parsed (`store_resolver._modules_for`), which is the third-file
    wrapper case — the map holds a module none of the files above is.
    """
    entry = entry or _entry()
    delta = GraphDelta(nodes=[entry, _reader_entry(), *(nodes or [])], edges=[])
    stores: list[Node] = []
    if store is not None:
        stores.append(store)
        delta.nodes.append(store)
        delta.edges.append(_writes_to(entry, store))

    site = UploadSite(
        entry_point=entry, file_path=HANDLER, line=1, framework="fake",
    )
    reads = [
        read
        for path, source in files.items()
        for read in FsFamily().detect_reads(ast.parse(source), path)
    ]
    if isinstance(modules, Mapping):
        parsed: dict[str, ast.Module] | None = dict(modules)
    else:
        parsed = (
            {path: ast.parse(source) for path, source in files.items()}
            if modules else None
        )
    created = attribute_source_type(
        delta=delta,
        upload_sites=[site],
        imports_by_file=imports or {},
        fs_reads=reads,
        stores=stores,
        modules=parsed,
    )
    return delta, created


def _edges(delta: GraphDelta, kind: str) -> list[Edge]:
    return [edge for edge in delta.edges if edge.kind == kind]


# ============================================================
# B1 — the canonical shape: one source and its two edges
# ============================================================

def test_a_read_in_the_writers_file_becomes_a_source() -> None:
    """B1 — the node 1.3a did not build, and the links that name it.

    The read is in the writer's own file, names the store's literal, and
    has no source of its own: the pass creates the node, links the store
    the writer wrote into, and hangs the source off the reader — the
    `read_back` entry point, resolved by the handler name.

    The created node is **not** appended to the delta: the caller (the
    stage) appends the nodes the delta does not already hold, which is
    what keeps a stamped node from being appended twice.
    """
    store = _store()
    delta, created = _build(files={HANDLER: SOURCE}, store=store)

    assert [node.name for node in created] == [f"{SOURCE_TYPE}:open"]
    source = created[0]
    assert source.attributes == {
        "source_type": SOURCE_TYPE,
        "call_target": "open",
        "source_file": HANDLER,
        "line_start": READ_LINE,
        "reader_kind": "function",
        "reader": "read_back",
    }
    assert source.id not in {node.id for node in delta.nodes}

    reads_from = _edges(delta, "reads_from")
    assert [(e.source_id, e.target_id) for e in reads_from] == [
        (store.id, source.id)
    ]
    assert reads_from[0].flow_kind == "structural"
    assert reads_from[0].observed_by == ["ast"]
    assert reads_from[0].data_flow_confirmed is None

    access = _edges(delta, "has_access_to")
    assert [(e.source_id, e.target_id) for e in access] == [
        (source.id, "entry_point:read_back")
    ]
    assert access[0].flow_kind == "control"


def test_a_write_site_earns_no_source() -> None:
    """B1's negative: the writer is the writer, not the reader (§4)."""
    delta, created = _build(files={HANDLER: WRITER_ONLY_SOURCE}, store=_store())

    assert created == []
    assert _edges(delta, "reads_from") == []
    assert _edges(delta, "has_access_to") == []


# ============================================================
# B2 — 1.3a's source wins (the precedence rule)
# ============================================================

def _existing_source(line: int = READ_LINE, target: str = "open") -> Node:
    """A source node 1.3a built, as `untrusted_sources._source_node` builds it."""
    return Node(
        id="untrusted_source:1", kind="untrusted_source",
        name="rag_corpus:retriever.invoke", host_id="localhost",
        attributes={
            "source_type": "rag_corpus", "call_target": target,
            "source_file": HANDLER, "line_start": line,
            "reader_kind": "function",
        },
    )


def test_a_source_on_the_read_site_line_is_not_duplicated() -> None:
    """B2 — the read site already has a source; the pass skips it (§3)."""
    existing = _existing_source()
    delta, created = _build(
        files={HANDLER: SOURCE}, store=_store(), nodes=[existing]
    )

    assert created == []
    assert _edges(delta, "reads_from") == []
    assert _edges(delta, "has_access_to") == []
    assert existing.attributes["source_type"] == "rag_corpus"


def test_a_source_for_the_same_call_is_not_duplicated() -> None:
    """B2 — the identity is `(source_file, call_target)`, not the line.

    Two reads of one call target in one file are one source (1.3a's
    `untrusted_source_id`), so a source built for `open` earlier in the
    file is this read site's source too.
    """
    existing = _existing_source(line=1)
    delta, created = _build(
        files={HANDLER: SOURCE}, store=_store(), nodes=[existing]
    )

    assert created == []
    assert _edges(delta, "reads_from") == []


# ============================================================
# B3 — the scope: the writer's file, or a file it shares an import with
# ============================================================

def test_a_read_in_an_unrelated_file_is_not_a_source() -> None:
    """B3 — another file, no shared import: not the writer's read (§4)."""
    delta, created = _build(
        files={HANDLER: WRITER_ONLY_SOURCE, UNRELATED: OTHER_SOURCE},
        store=_store(),
    )

    assert created == []
    assert _edges(delta, "reads_from") == []


def test_a_read_in_a_file_the_writer_imports_is_a_source() -> None:
    """B3/F3 — the import clause widens the scope and weakens the link.

    The reader is in another file, so the code-context signal is the
    one-way import (+0.1) rather than the same module (+0.2): the reader's
    link is `1 − 0.7 × 0.4 × 0.9 = 0.748`.
    """
    reader_entry = Node(
        id="entry_point:read_it", kind="entry_point", name="GET /read_it",
        host_id="localhost",
        attributes={"source_file": READER, "handler": "read_it"},
    )
    delta, created = _build(
        files={HANDLER: WRITER_ONLY_SOURCE, READER: OTHER_SOURCE},
        store=_store(),
        imports={HANDLER: ["app.tools"]},
        nodes=[reader_entry],
    )

    assert [node.name for node in created] == [f"{SOURCE_TYPE}:open"]
    assert created[0].attributes["source_file"] == READER
    assert created[0].attributes["reader"] == "read_it"

    link = _edges(delta, "reads_from")[0].identity_confidence
    assert round(link, 4) == 0.748
    assert round(
        combine_signals(
            BASE, [LITERAL_EQUAL, CODE_CONTEXT_SHARED_IMPORT]
        ), 4,
    ) == 0.748
    assert _edges(delta, "has_access_to")[0].target_id == reader_entry.id

# ============================================================
# A1 — the reader is in the writer's own file
# ============================================================

def test_a_reader_in_the_writers_file_is_an_upload_read() -> None:
    """A1 — same file, same scope, `user_upload`."""
    reader = _source("untrusted_source:1", HANDLER)

    changed = _attribute([reader])

    assert changed == [reader]
    assert reader.attributes["source_type"] == SOURCE_TYPE == "user_upload"


def test_the_returned_nodes_are_the_ones_in_the_delta() -> None:
    """The attributes are mutated in place; the return value is what moved."""
    reader = _source("untrusted_source:1", HANDLER)
    delta = GraphDelta(nodes=[reader], edges=[])

    changed = attribute_source_type(
        delta=delta, upload_sites=[_site()], imports_by_file={},
    )

    assert changed[0] is delta.nodes[0]
    assert delta.nodes[0].attributes["source_type"] == "user_upload"


# ============================================================
# A2 — the reader shares an import path
# ============================================================

def test_a_reader_in_a_file_the_writer_imports_is_in_scope() -> None:
    """A2 — `app/routes.py` imports `app.tools`, so `app/tools.py` counts."""
    reader = _source("untrusted_source:1", READER)

    changed = _attribute(
        [reader], imports={"app/routes.py": ["app.tools"]},
    )

    assert changed == [reader]
    assert reader.attributes["source_type"] == "user_upload"


def test_a_reader_that_imports_the_writer_is_in_scope_too() -> None:
    """Either direction: the channel's halves can be on either side."""
    reader = _source("untrusted_source:1", READER)

    changed = _attribute(
        [reader], imports={"app/tools.py": ["app.routes"]},
    )

    assert changed == [reader]


def test_a_reader_in_a_subpackage_of_the_writer_is_in_scope() -> None:
    """`_module_matches_file` is trailing segments, not a prefix rule."""
    reader = _source("untrusted_source:1", "pkg/app/tools.py")

    changed = _attribute(
        [reader],
        sites=[_site("pkg/app/routes.py")],
        imports={"pkg/app/routes.py": ["app.tools"]},
    )

    assert changed == [reader]


# ============================================================
# A3 — the reader is out of scope
# ============================================================

def test_a_reader_with_no_path_from_the_writer_is_not_attributed() -> None:
    """A3 — a shared standard-library import is not a shared path."""
    reader = _source("untrusted_source:1", READER)

    changed = _attribute(
        [reader], imports={"app/routes.py": ["os"], "app/tools.py": ["os"]},
    )

    assert changed == []
    assert "source_type" not in reader.attributes


def test_a_reader_in_an_unrelated_file_is_not_attributed() -> None:
    reader = _source("untrusted_source:1", UNRELATED)

    changed = _attribute(
        [reader], imports={"app/routes.py": ["app.tools"]},
    )

    assert changed == []


def test_a_reader_that_is_not_a_source_is_not_touched() -> None:
    """Only `untrusted_source` nodes carry a role (ADR-0021 §10)."""
    tool = Node(
        id="tool:1", kind="tool", name="read_report", host_id="localhost",
        attributes={"source_file": HANDLER, "line_start": 3},
    )

    assert _attribute([tool]) == []
    assert "source_type" not in tool.attributes


def test_a_source_with_no_file_is_not_attributed() -> None:
    """A source is placed by the read site it was built for."""
    node = Node(
        id="untrusted_source:1", kind="untrusted_source", name="src:open",
        host_id="localhost", attributes={"source_type": ""},
    )

    assert _attribute([node]) == []


def test_with_no_upload_site_nothing_is_attributed() -> None:
    """The empty-registry path, at the attribution's own level."""
    reader = _source("untrusted_source:1", HANDLER)

    assert _attribute([reader], sites=[]) == []
    assert "source_type" not in reader.attributes


# ============================================================
# A4 — a role that is already set wins
# ============================================================

def test_an_existing_source_type_is_not_overwritten() -> None:
    """A4 — B.2's `mcp_tool_description` stays; the first writer wins."""
    reader = _source("untrusted_source:1", HANDLER, "mcp_tool_description")

    changed = _attribute([reader])

    assert changed == []
    assert reader.attributes["source_type"] == "mcp_tool_description"


def test_an_empty_source_type_is_treated_as_unset() -> None:
    """A present-but-empty role names nothing, so it is not a role."""
    reader = _source("untrusted_source:1", HANDLER, "")

    changed = _attribute([reader])

    assert changed == [reader]
    assert reader.attributes["source_type"] == "user_upload"


def test_every_source_in_scope_moves_and_the_rest_are_left_alone() -> None:
    """Several sources, one scope: the ones in scope move, in delta order."""
    inside = _source("untrusted_source:1", HANDLER)
    outside = _source("untrusted_source:2", UNRELATED)
    named = _source("untrusted_source:3", HANDLER, "rag_corpus")

    changed = _attribute([inside, outside, named])

    assert changed == [inside]
    assert outside.attributes.get("source_type") is None
    assert named.attributes["source_type"] == "rag_corpus"


def test_two_sites_widen_the_scope() -> None:
    """Two handlers in two files are two writers, and either scope counts."""
    here = _source("untrusted_source:1", HANDLER)
    there = _source("untrusted_source:2", READER)

    changed = _attribute(
        [here, there],
        sites=[_site(HANDLER), _site(READER)],
    )

    assert changed == [here, there]


# ============================================================
# B4 — the literal is what names the store
# ============================================================

def test_a_read_with_no_literal_earns_nothing() -> None:
    """B4 — §4: no literal prefix, no store, no source.

    The read site is in the writer's file and the writer has a store, and
    the site names no literal: it cannot say which store it reads, and
    `literal_match`'s one-side row is not a link this pass invents. No
    source, no `reads_from`.
    """
    delta, created = _build(files={HANDLER: NAMELESS_SOURCE}, store=_store())

    assert created == []
    assert _edges(delta, "reads_from") == []


def test_a_read_of_another_store_earns_nothing() -> None:
    """B4 — two different literals name two stores (§8's no-link row)."""
    delta, created = _build(files={HANDLER: ELSEWHERE_SOURCE}, store=_store())

    assert created == []
    assert _edges(delta, "reads_from") == []


# ============================================================
# B5 — a source and its store are created together, or not at all
# ============================================================

def test_a_writer_with_no_store_earns_no_source() -> None:
    """B5 — no store, no pair (ADR-0022 §1)."""
    delta, created = _build(files={HANDLER: SOURCE}, store=None)

    assert created == []
    assert _edges(delta, "reads_from") == []
    assert _edges(delta, "has_access_to") == []


def test_a_writer_whose_read_site_is_out_of_scope_earns_no_source() -> None:
    """B5 — the store is there and the read is not the writer's (§4)."""
    delta, created = _build(
        files={HANDLER: WRITER_ONLY_SOURCE, UNRELATED: OTHER_SOURCE},
        store=_store(),
    )

    assert created == []
    assert _edges(delta, "reads_from") == []


def test_with_no_modules_nothing_is_created() -> None:
    """The caller parsed nothing: no reader, no node (the writer's rule).

    `reader_kind` is part of the node's shape (ADR-0022 §5), and it comes
    off the file's AST; a read site whose file was not parsed earns no
    source rather than a node that says nothing about its reader.
    """
    delta, created = _build(
        files={HANDLER: SOURCE}, store=_store(), modules=False
    )

    assert created == []
    assert _edges(delta, "reads_from") == []


# ============================================================
# B6 — one source, one link: the first writer (site order) claims it
# ============================================================

def test_the_first_writer_claims_the_read_site() -> None:
    """B6 — two writers, one read: the first store gets the `reads_from`.

    Both sites cover the read (they share the file), and ADR-0022 §1 gives
    the read site **one** source and **one** store link: the first writer
    creates it and links its own store, and the second sees a source that
    exists and adds nothing. The second store, which the read site's
    literal also names by containment, gets no link to the same source.
    """
    first, second = _entry("handle"), _entry("save")
    here, there = _store(), _store("/uploads/")
    delta = GraphDelta(
        nodes=[first, second, _reader_entry(), here, there],
        edges=[_writes_to(first, here), _writes_to(second, there)],
    )
    sites = [
        UploadSite(
            entry_point=entry, file_path=HANDLER, line=1, framework="fake",
        )
        for entry in (first, second)
    ]

    created = attribute_source_type(
        delta=delta,
        upload_sites=sites,
        imports_by_file={},
        fs_reads=FsFamily().detect_reads(ast.parse(SOURCE), HANDLER),
        stores=[here, there],
        modules={HANDLER: ast.parse(SOURCE)},
    )

    assert len(created) == 1
    reads_from = _edges(delta, "reads_from")
    assert len(reads_from) == 1
    assert reads_from[0].source_id == here.id
    assert reads_from[0].target_id == created[0].id
    assert _edges(delta, "has_access_to")[0].source_id == created[0].id


# ============================================================
# B7 — the reader's link is the reader's own (§8, ADR-0022 §5)
# ============================================================

def test_the_reader_link_is_the_readers_own_base() -> None:
    """B7 — base 0.3, and the pair is the weaker of the two links.

    The writer's 0.5 is a *writer-side* signal (ADR-0021 §8, revision 2)
    and it is on the `writes_to` edge; the reader is a plain `fs` read, so
    the `reads_from` link starts from the family's 0.3 and is smaller than
    the writer's link — which is why `find_chain_groups`'s `min` is the
    reader's number.
    """
    delta, created = _build(files={HANDLER: SOURCE}, store=_store())

    reader_link = _edges(delta, "reads_from")[0].identity_confidence
    writer_link = _edges(delta, "writes_to")[0].identity_confidence

    assert round(reader_link, 4) == round(
        combine_signals(
            BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
        ), 4,
    )
    assert round(reader_link, 4) == 0.776
    assert reader_link < writer_link
    assert round(min(reader_link, writer_link), 4) == 0.776


# ============================================================
# C — §8's code-context rows (B.3b-user_upload-attribution-followup)
# ============================================================

#: A wrapper module: the client both files reach through it (ADR-0021
#: §8). The body is the signal, and the name it binds is module-level.
WRAPPER = "app/storage.py"
WRAPPER_SOURCE = '''\
import boto3

s3 = boto3.client("s3")
'''

#: The same client, annotated: an `AnnAssign` binds a module-level name
#: too, and `_assigned_value` reads both spellings.
ANNOTATED = "app/annotated.py"
ANNOTATED_SOURCE = '''\
import boto3

s3: "Client" = boto3.client("s3")
'''

#: A module that only imports the client library: no constructor in its
#: body, so it is not a wrapper (S6).
IMPORTER = "app/aws.py"
IMPORTER_SOURCE = '''\
import boto3
'''

#: A client behind a function: not a wrapper either, because the client
#: is not a module-level name (S6's second shape).
FACTORY = "app/factory.py"
FACTORY_SOURCE = '''\
import boto3


def get_s3():
    return boto3.client("s3")
'''

#: The writer's file when the *writer* is the wrapper: the handler, its
#: write, and the client a reader in another file reaches through it.
WRAPPER_WRITER_SOURCE = '''\
import boto3

s3 = boto3.client("s3")


def handle(payload):
    with open("/uploads/report.txt", "wb") as fh:
        fh.write(payload)
'''

#: The writer's file when the file is its own wrapper and its own
#: reader: the client, the write and the read in one module.
SELF_WRAPPER_SOURCE = '''\
import boto3

s3 = boto3.client("s3")


def handle(payload):
    with open("/uploads/report.txt", "wb") as fh:
        fh.write(payload)


def read_back():
    with open("/uploads/report.txt", "rb") as fh:
        return fh.read()
'''


def _link_for(context: float) -> float:
    """The reader's link for one code-context row (§8).

    The base, one equal literal, and the row's weight — the constants,
    so a re-tuned weight moves every number here with it.
    """
    return combine_signals(BASE, [LITERAL_EQUAL, context])


# ------------------------------------------------------------
# The rows
# ------------------------------------------------------------

def test_a_shared_wrapper_scores_the_wrapper_row() -> None:
    """S1/O1 — both files reach the wrapper, so the row is the +0.3 one.

    The pair is in scope because the writer imports the reader's file
    (`_site_covers`), and it *also* imports `app/storage.py`, which the
    reader imports too. Two rows apply and the strongest wins, so the
    reader's link is `1 − 0.7 × 0.4 × 0.7 = 0.804` and not the shared
    import's 0.748.
    """
    reader_entry = _reader_entry("read_it", READER)
    delta, created = _build(
        files={
            HANDLER: WRITER_ONLY_SOURCE,
            READER: OTHER_SOURCE,
            WRAPPER: WRAPPER_SOURCE,
        },
        store=_store(),
        imports={
            HANDLER: ["app.tools", "app.storage"],
            READER: ["app.storage"],
        },
        nodes=[reader_entry],
    )

    assert [node.attributes["source_file"] for node in created] == [READER]
    link = _edges(delta, "reads_from")[0].identity_confidence
    assert round(link, 4) == round(_link_for(CODE_CONTEXT_SHARED_WRAPPER), 4)
    assert round(link, 4) == 0.804
    assert link > _link_for(CODE_CONTEXT_SHARED_IMPORT)
    assert _edges(delta, "has_access_to")[0].target_id == reader_entry.id


def test_the_row_weights_are_ranked_as_the_adr_lists_them() -> None:
    """O1 — shared-wrapper > same-module > shared-import.

    One row per pair, the strongest, and the ranking is ADR-0021 §8's:
    the wrapper is the strongest code-context signal, the import the
    weakest. The numbers are the constants', and they are the three the
    pipeline reaches (S1, S2, S3).
    """
    assert (
        CODE_CONTEXT_SHARED_WRAPPER
        > CODE_CONTEXT_SAME_MODULE
        > CODE_CONTEXT_SHARED_IMPORT
        > 0.0
    )
    assert _link_for(CODE_CONTEXT_SHARED_WRAPPER) > _link_for(
        CODE_CONTEXT_SAME_MODULE
    ) > _link_for(CODE_CONTEXT_SHARED_IMPORT)
    assert round(_link_for(CODE_CONTEXT_SHARED_WRAPPER), 4) == 0.804
    assert round(_link_for(CODE_CONTEXT_SAME_MODULE), 4) == 0.776
    assert round(_link_for(CODE_CONTEXT_SHARED_IMPORT), 4) == 0.748


def test_a_same_file_pair_with_a_wrapper_scores_the_wrapper_row() -> None:
    """O2 — the same file *and* a wrapper: +0.3, not +0.2 and not +0.5.

    `SOURCE` holds the write and the read, so the same-module row applies
    by itself; the file also imports `app/storage.py`, whose body defines
    the client. The wrapper is the stronger row, and the rows do not add:
    the reader's link is the wrapper's 0.804 — not the same module's
    0.776, and not a pair with `0.2 + 0.3` in it.
    """
    delta, created = _build(
        files={HANDLER: SOURCE, WRAPPER: WRAPPER_SOURCE},
        store=_store(),
        imports={HANDLER: ["app.storage"]},
    )

    assert created[0].attributes["source_file"] == HANDLER
    link = _edges(delta, "reads_from")[0].identity_confidence
    assert round(link, 4) == round(_link_for(CODE_CONTEXT_SHARED_WRAPPER), 4)
    combined = combine_signals(
        BASE,
        [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE + CODE_CONTEXT_SHARED_WRAPPER],
    )
    assert round(link, 4) != round(combined, 4)


def test_the_writers_own_file_can_be_the_wrapper() -> None:
    """S7 — the wrapper is one of the pair, not a third module.

    The handler's module defines `s3 = boto3.client("s3")`, and the
    reader's file imports it: the writer reaches the wrapper by *being*
    it and the reader by importing it. §8's "both files import the module
    the store is reached through" reads this way when the module is one
    of the two.
    """
    reader_entry = _reader_entry("read_it", READER)
    delta, created = _build(
        files={HANDLER: WRAPPER_WRITER_SOURCE, READER: OTHER_SOURCE},
        store=_store(),
        imports={READER: ["app.routes"]},
        nodes=[reader_entry],
    )

    assert [node.attributes["source_file"] for node in created] == [READER]
    link = _edges(delta, "reads_from")[0].identity_confidence
    assert round(link, 4) == round(_link_for(CODE_CONTEXT_SHARED_WRAPPER), 4)
    assert _edges(delta, "has_access_to")[0].target_id == reader_entry.id


def test_a_same_file_pair_shares_the_client_it_defines() -> None:
    """O2/S7 — the file is its own wrapper, with no import at all.

    `SELF_WRAPPER_SOURCE` defines the client and reads the store in one
    module, and the scan's import table is empty: this is
    `_reaches_file`'s "a file is itself" clause, and the row is the
    wrapper's rather than the same module's.
    """
    delta, created = _build(
        files={HANDLER: SELF_WRAPPER_SOURCE}, store=_store(),
    )

    assert created[0].attributes["reader"] == "read_back"
    link = _edges(delta, "reads_from")[0].identity_confidence
    assert round(link, 4) == round(_link_for(CODE_CONTEXT_SHARED_WRAPPER), 4)
    assert link != _link_for(CODE_CONTEXT_SAME_MODULE)


# ------------------------------------------------------------
# The rows' negatives
# ------------------------------------------------------------

def test_a_shared_import_without_a_wrapper_scores_its_own_row() -> None:
    """S6/S3 — both files import a module, and it is not a wrapper.

    `app/aws.py` only imports `boto3`: it defines no client, so the
    wrapper row does not fire even though both files import it. The pair
    is in scope through the writer's import of the reader's file, and the
    row is the shared import's +0.1.
    """
    reader_entry = _reader_entry("read_it", READER)
    delta, created = _build(
        files={
            HANDLER: WRITER_ONLY_SOURCE,
            READER: OTHER_SOURCE,
            IMPORTER: IMPORTER_SOURCE,
        },
        store=_store(),
        imports={HANDLER: ["app.tools", "app.aws"], READER: ["app.aws"]},
        nodes=[reader_entry],
    )

    assert [node.attributes["source_file"] for node in created] == [READER]
    link = _edges(delta, "reads_from")[0].identity_confidence
    assert round(link, 4) == round(_link_for(CODE_CONTEXT_SHARED_IMPORT), 4)
    assert round(link, 4) == 0.748


def test_a_client_behind_a_function_is_not_a_wrapper() -> None:
    """S6 — the client has to be a module-level *name*.

    `app/factory.py` defines `def get_s3(): return boto3.client("s3")`:
    the constructor is in the body and not at module level, so the module
    is not a wrapper and the row is the shared import's.
    """
    reader_entry = _reader_entry("read_it", READER)
    delta, created = _build(
        files={
            HANDLER: WRITER_ONLY_SOURCE,
            READER: OTHER_SOURCE,
            FACTORY: FACTORY_SOURCE,
        },
        store=_store(),
        imports={
            HANDLER: ["app.tools", "app.factory"],
            READER: ["app.factory"],
        },
        nodes=[reader_entry],
    )

    assert [node.attributes["source_file"] for node in created] == [READER]
    link = _edges(delta, "reads_from")[0].identity_confidence
    assert round(link, 4) == round(_link_for(CODE_CONTEXT_SHARED_IMPORT), 4)


def test_an_annotated_client_assignment_is_a_wrapper() -> None:
    """S6's positive twin — `s3: Client = boto3.client("s3")` binds a name.

    `_assigned_value` reads `AnnAssign` as well as `Assign`: the rule is a
    module-level *name*, and an annotation does not make the binding any
    less one.
    """
    reader_entry = _reader_entry("read_it", READER)
    delta, created = _build(
        files={
            HANDLER: WRITER_ONLY_SOURCE,
            READER: OTHER_SOURCE,
            ANNOTATED: ANNOTATED_SOURCE,
        },
        store=_store(),
        imports={
            HANDLER: ["app.tools", "app.annotated"],
            READER: ["app.annotated"],
        },
        nodes=[reader_entry],
    )

    assert [node.attributes["source_file"] for node in created] == [READER]
    link = _edges(delta, "reads_from")[0].identity_confidence
    assert round(link, 4) == round(_link_for(CODE_CONTEXT_SHARED_WRAPPER), 4)


def test_a_wrapper_only_one_file_reaches_is_not_shared() -> None:
    """S5 — the wrapper has to be reached by *both* files.

    The writer imports the wrapper and the reader's file; the reader
    imports neither. The pair is in scope (the writer's import) and the
    row is the import's +0.1: a wrapper one of the two files reaches is
    not shared.
    """
    reader_entry = _reader_entry("read_it", READER)
    delta, created = _build(
        files={
            HANDLER: WRITER_ONLY_SOURCE,
            READER: OTHER_SOURCE,
            WRAPPER: WRAPPER_SOURCE,
        },
        store=_store(),
        imports={HANDLER: ["app.tools", "app.storage"], READER: []},
        nodes=[reader_entry],
    )

    assert [node.attributes["source_file"] for node in created] == [READER]
    link = _edges(delta, "reads_from")[0].identity_confidence
    assert round(link, 4) == round(_link_for(CODE_CONTEXT_SHARED_IMPORT), 4)
    assert round(link, 4) != round(_link_for(CODE_CONTEXT_SHARED_WRAPPER), 4)


def test_an_unrelated_pair_scores_no_row() -> None:
    """S4 — neither file is the other, and no wrapper is shared: 0.

    Unreachable through `attribute_source_type` — a read site out of
    scope is skipped before the row is read (`_site_covers`) — so it is
    pinned on `_code_context`, the function this step changed, with the
    modules present and absent. The literal alone is
    `1 − 0.7 × 0.4 = 0.72`.
    """
    imports = {UNRELATED: ["app.storage"]}

    assert _code_context(HANDLER, UNRELATED, imports) == 0.0
    assert _code_context(
        HANDLER, UNRELATED, imports, {WRAPPER: ast.parse(WRAPPER_SOURCE)}
    ) == 0.0
    assert round(combine_signals(BASE, [LITERAL_EQUAL]), 4) == 0.72


def test_the_wrapper_calls_are_the_closed_set() -> None:
    """§8's wrapper shapes: six spellings, and adding one is a decision.

    The set is *spellings*, read by `_call_target`, so an aliased import
    or a class the list does not name is not a wrapper — and it is closed
    on purpose, which is why this test exists: a new shape is a change to
    the ADR's table and not a name a helper learns.
    """
    assert WRAPPER_CALLS == frozenset({
        "boto3.client", "boto3.resource",
        "Storage", "Chroma", "Client", "connect",
    })
    assert "Minio" not in WRAPPER_CALLS


# ------------------------------------------------------------
# The pair, and the scope the row does not widen
# ------------------------------------------------------------

def test_the_pair_is_the_weaker_link_with_a_wrapper() -> None:
    """P1 — `min(writer, reader)`, from the constants.

    The writer's `writes_to` carries the context-based writer's 0.5 base
    (ADR-0021 §8, revision 2) and the reader's `reads_from` the family's
    0.3; a wrapper pair is 0.804 on the reader's side, so the pair is the
    reader's number — the weaker side, the same answer as without a
    wrapper, one row higher.
    """
    reader_entry = _reader_entry("read_it", READER)
    delta, _ = _build(
        files={
            HANDLER: WRITER_ONLY_SOURCE,
            READER: OTHER_SOURCE,
            WRAPPER: WRAPPER_SOURCE,
        },
        store=_store(),
        imports={
            HANDLER: ["app.tools", "app.storage"],
            READER: ["app.storage"],
        },
        nodes=[reader_entry],
    )

    reader_link = _edges(delta, "reads_from")[0].identity_confidence
    writer_link = _edges(delta, "writes_to")[0].identity_confidence
    assert round(reader_link, 4) == round(
        _link_for(CODE_CONTEXT_SHARED_WRAPPER), 4
    )
    assert reader_link < writer_link
    assert round(min(reader_link, writer_link), 4) == round(reader_link, 4)


def test_a_pair_related_only_through_a_wrapper_is_out_of_scope() -> None:
    """The scope rule is unchanged: the wrapper is a *row*, not a scope.

    Both files import `app/storage.py` and neither imports the other, so
    the pair shares a wrapper and is still out of scope (`_site_covers`):
    the read site earns no source, no `reads_from` and no `has_access_to`.
    Widening the scope to a shared wrapper is a different decision, and
    this step does not take it.
    """
    delta, created = _build(
        files={
            HANDLER: WRITER_ONLY_SOURCE,
            READER: OTHER_SOURCE,
            WRAPPER: WRAPPER_SOURCE,
        },
        store=_store(),
        imports={HANDLER: ["app.storage"], READER: ["app.storage"]},
        nodes=[_reader_entry("read_it", READER)],
    )

    assert created == []
    assert _edges(delta, "reads_from") == []
    assert _edges(delta, "has_access_to") == []


# ============================================================
# C2 — the third-file wrapper (B.3b-user_upload-attribution-followup-2)
# ============================================================
#
# S1's wrapper is a file the stage already parsed, because it is a site's
# file. A wrapper in a *third* module — nobody's site file, imported by
# both — has no body in the map until `store_resolver._modules_for` parses
# it, so the row cannot fire however the wrapper is written. These tests
# take the map from the stage itself.


def _fs_sites(files: dict[str, str]):
    """The `fs` family's sites over a set of sources, as `_fs_sites` sees
    them — one write and one read per call site, by file."""
    writes = [
        write
        for path, source in files.items()
        for write in FsFamily().detect_writes(ast.parse(source), path)
    ]
    reads = [
        read
        for path, source in files.items()
        for read in FsFamily().detect_reads(ast.parse(source), path)
    ]
    return writes, reads


def test_the_stage_parses_the_third_file_and_the_wrapper_row_fires(
    project,
) -> None:
    """S1's row, with the wrapper where it belongs: in a third module.

    The writer's file imports the reader's file (so the pair is in scope)
    and `app/storage.py`, and so does the reader's file. `app/storage.py`
    is neither site's file, so it is in the stage's map only because
    `_modules_for` parsed the modules the sites' files import.

    With the map the stage builds, the reader's link is the wrapper row,
    `1 − 0.7 × 0.4 × 0.7 = 0.804`. Without the wrapper's body — the map
    the stage handed over before this step, the sites' files and nothing
    else — the same pair is still in scope and the row that wins is the
    shared import's, which is the 0.748 the S2 test pins. The number the
    pair's `min` takes is therefore what the parse changed.
    """
    files = {
        HANDLER: WRITER_ONLY_SOURCE,
        READER: OTHER_SOURCE,
        WRAPPER: WRAPPER_SOURCE,
    }
    imports = {HANDLER: ["app.tools", "app.storage"], READER: ["app.storage"]}
    built = project(
        files=files, entry_points=EntryPointOutput(imports=imports),
    )
    fs_writes, fs_reads = _fs_sites(files)
    modules = _modules_for(built.ctx, [], fs_writes, fs_reads)

    assert sorted(modules) == sorted([HANDLER, READER, WRAPPER])

    reader_entry = _reader_entry("read_it", READER)
    without, _ = _build(
        files=files,
        store=_store(),
        imports=imports,
        nodes=[reader_entry],
        modules={path: ast.parse(files[path]) for path in (HANDLER, READER)},
    )
    assert round(
        _edges(without, "reads_from")[0].identity_confidence, 4
    ) == round(_link_for(CODE_CONTEXT_SHARED_IMPORT), 4)

    delta, created = _build(
        files=files,
        store=_store(),
        imports=imports,
        nodes=[reader_entry],
        modules=modules,
    )
    assert [node.attributes["source_file"] for node in created] == [READER]
    link = _edges(delta, "reads_from")[0].identity_confidence
    assert round(link, 4) == round(_link_for(CODE_CONTEXT_SHARED_WRAPPER), 4)
    assert round(link, 4) == 0.804
