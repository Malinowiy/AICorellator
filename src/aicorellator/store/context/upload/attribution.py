"""`source_type='user_upload'` for the read sites an upload writer owns.

ADR-0021 §10: the *store* is the technology (`store.family`) and the
*semantic role* of the read is the `source_type` on the
`untrusted_source`. An upload handler that writes into `/uploads/` and a
tool that reads from it are a `store('fs', '/uploads/')` pair; what makes
the pair "an upload" rather than "a cache" is this function.

The rule, and it is the whole rule:

  - a source node the writer can claim lives in the **writer's file** —
    the upload handler's — or in a file the writer's file shares an
    import path with, in either direction;
  - a source node that already carries a `source_type` is **not
    overwritten**: the first writer's scope wins, which is what keeps
    B.2's `mcp_tool_description` from being relabelled by an upload
    handler that happens to sit in the same module.

The file, not a line range, decides, and that is what makes a
**module-level** site (`writer._module_level`: `entry_kind` `"cli"` or
`"streamlit"`) need no extra rule (B.3b-user_upload-core-module): the
module is its own scope, so every read in that file is in scope, exactly
as the same-file clause already answers.

**The scope is the handler's, not the stores it was found to write
into.** Accepting an upload is the fact that makes the reads around it
upload reads, and a handler whose stores this scan did not recognize is
still an upload handler; tying the attribution to the writer edges would
make it depend on the `fs` family's coverage, which is the wrong
dependency.

**The writer's file is the handler's file, and not the helper's.** A
write the handler reaches through a helper in another module is the
handler's write (`writer.find_upload_stores`), but that file's reads are
not claimed here: the rule is the entry point's scope, and widening it to
every file a helper lives in would attribute reads the upload has nothing
to do with. A false negative, stated.

Two rules are restated rather than imported, because the store subsystem
does not reach into the parsing stage package (the stage package imports
this one): `cross_file._module_matches_file` (B.1, trailing path
segments) and `untrusted_source_writers._shares_an_import`'s *either
direction* reading of it. Both are index building and comparisons, not
logic that drifts.

**Inert end-to-end today, and honestly so.** Every `untrusted_source`
node the pipeline builds carries a `source_type` already — 1.3a's read
rules set it (`untrusted_sources`), and the brief adds no rule to
`patterns.py` — so the "not overwritten" clause is what applies to a real
scan, and the nodes this function changes are the ones a future producer
builds. The step is correct and ready; the `user_upload` read side has no
producer yet. That is the same shape as B.4-gap's "the edges wait for the
nodes", and it is why the no-op invariant holds on every corpus.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Mapping

from ....graph.models import Node
from .base import UploadSite

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....parsing.ast_normalizer import GraphDelta


#: The role this function stamps (ADR-0021 §1, revised): a *source type*,
#: and not a store family.
SOURCE_TYPE = "user_upload"

#: The node kind a read site is (ADR-0021 §10, 1.3a).
SOURCE_KIND = "untrusted_source"


def attribute_source_type(
    *,
    delta: "GraphDelta",
    upload_sites: list[UploadSite],
    imports_by_file: Mapping[str, list[str]],
) -> list[Node]:
    """Stamp `source_type='user_upload'` on the writer's read sites.

    Returns the nodes whose `source_type` was set, in delta order. The
    attributes are mutated **in place** — the node objects in the delta
    are the ones returned — so the value is what changed and not a
    second, detached copy the caller would have to find again; the caller
    (the stage) uses it for nothing more than that.

    `imports_by_file` is the scan's per-file import table
    (`ctx.entry_points.imports`): a dotted module name per file, which is
    what turns "these two files are related" into a path comparison. An
    empty table means only the same-file rule can fire, which is the
    conservative reading and not an error.

    A node with no `source_file` cannot be in any scope: a source is
    placed by the read site it was built for, and without that there is
    nothing to compare.
    """
    changed: list[Node] = []
    for node in delta.nodes:
        if node.kind != SOURCE_KIND:
            continue
        file_path = node.attributes.get("source_file")
        if not isinstance(file_path, str):
            continue
        if not _in_scope(file_path, upload_sites, imports_by_file):
            continue
        if _has_source_type(node):
            continue
        node.attributes["source_type"] = SOURCE_TYPE
        changed.append(node)
    return changed


def _in_scope(
    file_path: str,
    upload_sites: Iterable[UploadSite],
    imports_by_file: Mapping[str, list[str]],
) -> bool:
    """True if an upload writer's scope covers `file_path`.

    Any site is enough: two handlers in two files are two writers, and a
    read in either file is in one of their scopes.

    The same-file clause is the whole file, for either kind of site, and
    that is the point of stating it here: a **function-level** site's
    scope is its file because the handler's line range would say nothing
    about a read elsewhere in the module (the reads an upload handler's
    file makes are the handler's neighbourhood, and the ADR's scope rule
    is the file), and a **module-level** site (`writer._module_level`,
    B.3b-user_upload-core-module) *is* its file — the module is the
    scope, so a read at any line of it is in scope, with no range to
    consult. The two readings coincide, which is why the clause needs no
    branch for the module-level case; `test_attribution_module.py` pins
    it for a module-level site.
    """
    for site in upload_sites:
        if file_path == site.file_path:
            return True
        if _shares_an_import_path(site.file_path, file_path, imports_by_file):
            return True
    return False


def _has_source_type(node: Node) -> bool:
    """True if the node already carries a role.

    `user_upload` is not the only source type, and a node that has one
    keeps it: the first writer's scope wins (the module docstring states
    the precedence). A present-but-empty value is not a role and is
    treated as unset, because a node whose `source_type` is `""` names
    nothing.
    """
    source_type = node.attributes.get("source_type")
    return isinstance(source_type, str) and bool(source_type)


def _shares_an_import_path(
    writer_file: str,
    source_file: str,
    imports_by_file: Mapping[str, list[str]],
) -> bool:
    """True if one file imports the other — either direction.

    `untrusted_source_writers._shares_an_import`'s rule (B.2), minus its
    same-file clause, which `_in_scope` applies first. Both directions
    are checked because the channel's halves can be on either side of the
    import: the route module that imports the store module, or a module
    that imports the route module.

    The comparison is `_module_matches_file`'s (B.1): trailing path
    segments, so a target root's prefix does not matter. A relative
    import is dropped by the entry-point parser and resolves to nothing
    here, as everywhere else.
    """
    return any(
        _module_matches_file(module, source_file)
        for module in imports_by_file.get(writer_file, ())
    ) or any(
        _module_matches_file(module, writer_file)
        for module in imports_by_file.get(source_file, ())
    )


def _module_matches_file(module: str, file_path: str) -> bool:
    """True if a dotted module name plausibly names a file path.

    `cross_file._module_matches_file`'s rule, restated: the module's
    dotted path must equal the file's path with slashes and the `.py`
    suffix, or be its trailing segments. `"tools"` matches `tools.py` and
    `pkg/tools.py`; `"tools"` does not match `tools/sub.py`, because that
    is `tools.sub`.
    """
    if not module:
        return False
    module_path = module.replace(".", "/") + ".py"
    return (
        file_path == module_path
        or file_path.endswith("/" + module_path)
    )


__all__ = [
    "SOURCE_KIND",
    "SOURCE_TYPE",
    "attribute_source_type",
]

