"""The upload attribution — the read sites in a writer's scope (A1–A4).

ADR-0021 §10 keeps the *role* on the `untrusted_source` and the
*technology* on the store, and this is what sets the role for an upload:
a source node in the upload writer's file, or in a file that file shares
an import path with, becomes `user_upload` — unless it already carries a
`source_type`, in which case the first writer's scope wins.

The scope is the handler's file and not the stores it was found to write
into, so these tests are about files and imports and need no stores at
all. The delta is hand-built: a read site's node is 1.3a's, and this
step only edits one attribute on it.
"""

from __future__ import annotations

from aicorellator.graph.models import Node
from aicorellator.parsing.ast_normalizer import GraphDelta
from aicorellator.store.context.upload.attribution import (
    SOURCE_TYPE,
    attribute_source_type,
)
from aicorellator.store.context.upload.base import UploadSite

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

