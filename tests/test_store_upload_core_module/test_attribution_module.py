"""Attribution for a module-level site — the module is the scope (A1–A3).

B.3b-user_upload-core-module adds no rule to
`attribution.attribute_source_type`, and this file is why: the scope rule
is a **file** rule, and a module-level site's file is the whole of its
scope — there is no handler line range to narrow it with. A read in the
module is in scope whatever line it is on, a read in a file that shares an
import path with the module is in scope, and a read elsewhere is not. The
tests pin all three for a site whose entry point is module-level
(`entry_kind` in the writer's closed set), because that is the shape no
test had exercised.

The delta is hand-built: a read site's node is 1.3a's, and this step only
edits one attribute on it.
"""

from __future__ import annotations

from aicorellator.graph.models import Node
from aicorellator.parsing.ast_normalizer import GraphDelta
from aicorellator.store.context.upload.attribution import (
    SOURCE_TYPE,
    attribute_source_type,
)

from .conftest import MODULE, UNRELATED, module_site

#: A module that shares an import path with `MODULE`, in one direction or
#: the other (the import map decides which).
READER = "app/tools.py"

#: The line a module-level site is on, and the lines a read can be on
#: relative to it: before and after. Neither matters to the scope, and
#: that is the point of the two tests below.
SITE_LINE = 10


def _source(
    node_id: str,
    file_path: str,
    line: int = 20,
    source_type: str | None = None,
) -> Node:
    attributes: dict[str, object] = {
        "source_file": file_path, "line_start": line,
    }
    if source_type is not None:
        attributes["source_type"] = source_type
    return Node(
        id=node_id, kind="untrusted_source", name="user_upload:open",
        host_id="localhost", attributes=attributes,
    )


def _attribute(nodes: list[Node], *, sites=None, imports=None) -> list[Node]:
    return attribute_source_type(
        delta=GraphDelta(nodes=nodes, edges=[]),
        upload_sites=(
            [module_site(line=SITE_LINE)] if sites is None else sites
        ),
        imports_by_file=imports or {},
    )


# ============================================================
# A1 — the read is in the module's own file
# ============================================================

def test_a_read_in_the_modules_own_file_is_an_upload_read() -> None:
    """A1 — same file, same scope, `user_upload`."""
    reader = _source("untrusted_source:1", MODULE)

    changed = _attribute([reader])

    assert changed == [reader]
    assert reader.attributes["source_type"] == SOURCE_TYPE == "user_upload"


def test_a_read_after_the_site_is_in_scope() -> None:
    """A1 — the scope is the file, and the read is below the site's line."""
    reader = _source("untrusted_source:1", MODULE, line=SITE_LINE + 40)

    assert _attribute([reader]) == [reader]


def test_a_read_before_the_site_is_in_scope() -> None:
    """A1 — and the read above it is too.

    The module is the scope: a module-level site has no body whose range
    could be compared against the read's line, and the reading the brief
    states — a read at any line of the module is in scope — is the one
    the code already implements. This test and the one above are here so
    the reading is a decision on the record and not an accident of the
    comparison.
    """
    reader = _source("untrusted_source:1", MODULE, line=2)

    assert _attribute([reader]) == [reader]


def test_a_read_inside_a_function_of_the_module_is_in_scope() -> None:
    """A1 — a read in one of the module's functions is the module's read."""
    reader = _source("untrusted_source:1", MODULE, line=99)

    assert _attribute([reader]) == [reader]


def test_the_returned_nodes_are_the_ones_in_the_delta() -> None:
    """The attributes are mutated in place; the return value is what moved."""
    reader = _source("untrusted_source:1", MODULE)
    delta = GraphDelta(nodes=[reader], edges=[])

    changed = attribute_source_type(
        delta=delta, upload_sites=[module_site(line=SITE_LINE)],
        imports_by_file={},
    )

    assert changed[0] is delta.nodes[0]
    assert delta.nodes[0].attributes["source_type"] == "user_upload"

# ============================================================
# A2 — the read shares an import path
# ============================================================

def test_a_read_in_a_file_the_module_imports_is_in_scope() -> None:
    """A2 — `app/app.py` imports `app.tools`, so `app/tools.py` counts."""
    reader = _source("untrusted_source:1", READER)

    changed = _attribute([reader], imports={MODULE: ["app.tools"]})

    assert changed == [reader]
    assert reader.attributes["source_type"] == "user_upload"


def test_a_read_in_a_file_that_imports_the_module_is_in_scope() -> None:
    """A2 — the other direction: the channel's halves are on either side."""
    reader = _source("untrusted_source:1", READER)

    changed = _attribute([reader], imports={READER: ["app.app"]})

    assert changed == [reader]


def test_a_shared_standard_library_import_is_not_a_shared_path() -> None:
    """A2's limit — `os` is not a path either file's module name names."""
    reader = _source("untrusted_source:1", READER)

    changed = _attribute(
        [reader], imports={MODULE: ["os"], READER: ["os"]},
    )

    assert changed == []


# ============================================================
# A3 — the read is elsewhere
# ============================================================

def test_a_read_in_an_unrelated_file_is_not_attributed() -> None:
    """A3 — no shared file, no shared import, no role."""
    reader = _source("untrusted_source:1", UNRELATED)

    changed = _attribute([reader], imports={MODULE: ["app.tools"]})

    assert changed == []
    assert "source_type" not in reader.attributes


def test_with_no_upload_site_nothing_is_attributed() -> None:
    """The empty-registry path, at the attribution's own level."""
    reader = _source("untrusted_source:1", MODULE)

    assert _attribute([reader], sites=[]) == []
    assert "source_type" not in reader.attributes


def test_a_role_that_is_already_set_is_not_overwritten() -> None:
    """The first writer's scope wins, module-level site or not.

    A module-level site is not a stronger claim than a function-level one
    for this rule: the node already carries a role, and B.2's
    `mcp_tool_description` is not relabelled because a module in the same
    file also declares an upload.
    """
    reader = _source("untrusted_source:1", MODULE, source_type="rag_corpus")

    changed = _attribute([reader])

    assert changed == []
    assert reader.attributes["source_type"] == "rag_corpus"


def test_an_empty_source_type_is_treated_as_unset() -> None:
    """A present-but-empty role names nothing, so it is not a role."""
    reader = _source("untrusted_source:1", MODULE, source_type="")

    changed = _attribute([reader])

    assert changed == [reader]
    assert reader.attributes["source_type"] == "user_upload"

