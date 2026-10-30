"""`source_type='user_upload'` for the read sites an upload writer owns.

ADR-0021 §10: the *store* is the technology (`store.family`) and the
*semantic role* of the read is the `source_type` on the
`untrusted_source`. An upload handler that writes into `/uploads/` and a
tool that reads from it are a `store('fs', '/uploads/')` pair; what makes
the pair "an upload" rather than "a cache" is this function.

**Two halves, one scope rule** (ADR-0022 §1). The halves are one function
because the scope is one rule, and because the store's source and the
store's link are created together or not at all:

  - **stamp** an `untrusted_source` node 1.3a built
    (`untrusted_sources`), when it is in a writer's scope and carries no
    role;
  - **create** one for an `fs` read site in a writer's scope that 1.3a
    did **not** recognize, with `source_type='user_upload'`, and link the
    store the writer wrote into to it: `reads_from store → source`, and
    `has_access_to source → reader` so the source is the first hop of a
    read chain (`graph.query._read_chains_from`).

The rule, and it is the whole rule:

  - a source node, or a read site, that the writer can claim lives in the
    **writer's file** — the upload handler's — or in a file the writer's
    file shares an import path with, in either direction;
  - a source node that already carries a `source_type` is **not
    overwritten**, and a read site that already has a source node is
    **not given a second one**: the first producer's scope wins, which is
    what keeps B.2's `mcp_tool_description` from being relabelled by an
    upload handler that happens to sit in the same module, and what keeps
    a `rag_corpus` read in that module from becoming a second source.

**The two producers are disjoint, and precedence is 1.3a's.** 1.3a reads
a call *shape* (a `retriever.invoke`, a `client.list_tools`); this pass
reads a *context* (an `fs` read inside an upload handler's scope). A read
site both could claim — impossible in 1.3a's rules, where no source type
calls `open` — keeps 1.3a's answer: this pass skips a read site that
already has an `untrusted_source` node, by the read site's line
(`resolver._source_index`'s by-line rule, restated) or by its
`(source_file, call_target)` identity (ADR-0022 §3).

**A source and its store are created together, or not at all** (ADR-0022
§1, §4). A read site earns a node only when there is a store to link it
to: the store the writer's `writes_to` edge names, whose literal the read
site's literal matches (`confidence.literal_match` — the `fs` family's
rule, and the reason a read site with no literal prefix earns nothing:
§5's "no identity" is the store's, and a site that cannot name one cannot
be linked to it).

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
dependency. The creating half reads the writer's `writes_to` edges
because it needs the *link target* and not a permission: with no store
there is nothing to link, and the site is still an upload handler.

**The writer's file is the handler's file, and not the helper's.** A
write the handler reaches through a helper in another module is the
handler's write (`writer.find_upload_stores`), but that file's reads are
not claimed here: the rule is the entry point's scope, and widening it to
every file a helper lives in would attribute reads the upload has nothing
to do with. A false negative, stated.

**The reader's base is 0.3, and the pair is the weaker side** (ADR-0022
§5's rationale). The writer's base 0.5 (ADR-0021 §8, revision 2) is a
*writer-side* signal; the reader is a plain `fs` read, so the `reads_from`
link starts from the read site's own `confidence_base` (0.3, the family's)
and takes the literal signal plus the code-context row this pass can
read — the shared wrapper (+0.3), the same module (+0.2) or the one-way
import (+0.1), which are `confidence.code_context`'s three rows, ranked
the ADR's way (`_code_context`). A same-module pair is
`1 − 0.7 × 0.4 × 0.8 = 0.776`, a shared-import pair is
`1 − 0.7 × 0.4 × 0.9 = 0.748`, and a pair that shares a wrapper is
`1 − 0.7 × 0.4 × 0.7 = 0.804` (B.3b-user_upload-attribution-followup:
the row reads the parsed module bodies, `_defines_a_client`);
`find_chain_groups` takes the `min` of the two links, which is the
reader's number here because the writer's is higher (0.84–0.872).

Four rules are restated rather than imported, because the store subsystem
does not reach into the parsing stage package (the stage package imports
this one): `cross_file._module_matches_file` (B.1, trailing path
segments), `untrusted_source_writers._shares_an_import`'s *either
direction* reading of it, `untrusted_sources`'s reader resolution (the
enclosing definition, then the class, read off the file's AST and the
delta's nodes) and the `fs` family's dotted-call spelling. All four are
index building and comparisons, not logic that drifts.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Callable, Iterable, Mapping

from ....graph.models import Edge, Node
from ...confidence import (
    CODE_CONTEXT_SAME_MODULE,
    CODE_CONTEXT_SHARED_IMPORT,
    CODE_CONTEXT_SHARED_WRAPPER,
    combine_signals,
    literal_match,
)
from ...families.base import ReadSite
from .base import UploadSite
from .writer import MODULE_LEVEL_ENTRY_KINDS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....parsing.ast_normalizer import GraphDelta


#: The role this function stamps (ADR-0021 §1, revised): a *source type*,
#: and not a store family.
SOURCE_TYPE = "user_upload"

#: The node kind a read site is (ADR-0021 §10, 1.3a).
SOURCE_KIND = "untrusted_source"

#: The wrapper-constructor spellings the shared-wrapper row accepts
#: (ADR-0021 §8's "a module that defines the client the store is
#: reached through"). A closed set of **spellings**, read as a dotted
#: call name (`_call_target`), and deliberately not a name table: an
#: aliased import, a class the list does not name and a client returned
#: by a function are all outside it, because the spelling is the test
#: (B.3b-user_upload-attribution-followup).
WRAPPER_CALLS = frozenset({
    "boto3.client",
    "boto3.resource",
    "Storage",
    "Chroma",
    "Client",
    "connect",
})


def attribute_source_type(
    *,
    delta: "GraphDelta",
    upload_sites: list[UploadSite],
    imports_by_file: Mapping[str, list[str]],
    fs_reads: Iterable[ReadSite] = (),
    stores: Iterable[Node] = (),
    modules: Mapping[str, ast.Module] | None = None,
) -> list[Node]:
    """Stamp, and create, the `user_upload` read sites of the writers.

    Returns the nodes this pass gave the role to, in delta order with the
    created ones after the stamped ones. A **stamped** node is mutated in
    place — it is already in the delta, so the value returned is what
    changed and not a second, detached copy — and a **created** node is
    *not* appended here: the caller (the stage) appends the nodes the
    delta does not already hold, the way it appends the resolver's store
    nodes. The edges the creating half emits are appended to
    `delta.edges` here, because a node has the return value and an edge
    has no second slot; they are `reads_from store → source` and
    `has_access_to source → reader`, one each per created source.

    `fs_reads` is the `fs` family's read sites and `stores` the store
    nodes the resolver built. `modules` is the parsed modules the stage
    offers — the entry points' files, the write sites' files and the
    read sites' files — and the pass reads two things out of them: the
    reader's enclosing symbol, which is where the created source's
    `reader_kind` and its reader come from
    (`untrusted_sources._reads_in`'s attribution rule, restated), and
    the body of a module both files reach, which is §8's shared-wrapper
    row (`_defines_a_client`). The three are what the creating half
    needs and the stamping half does not, so a caller that passes none
    of them gets the stamping behaviour alone — the mode
    B.3b-user_upload-core shipped. A read site whose file is not in
    `modules` earns no source: its `reader_kind` is not knowable, and
    `writer.find_upload_stores`'s "no modules, no edge" is the same
    answer to the same question.

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

    created, edges = _create_sources(
        delta=delta,
        upload_sites=upload_sites,
        imports_by_file=imports_by_file,
        fs_reads=fs_reads,
        stores=stores,
        modules=modules,
    )
    delta.edges.extend(edges)
    return [*changed, *created]


# ------------------------------------------------------------------
# The creating half (ADR-0022 §1)
# ------------------------------------------------------------------

def _create_sources(
    *,
    delta: "GraphDelta",
    upload_sites: list[UploadSite],
    imports_by_file: Mapping[str, list[str]],
    fs_reads: Iterable[ReadSite],
    stores: Iterable[Node],
    modules: Mapping[str, ast.Module] | None,
) -> tuple[list[Node], list[Edge]]:
    """The `user_upload` sources the writers own, and the edges naming them.

    One source per read site, and one store link per source: the first
    writer (site order) whose scope covers the read site and whose store
    the read site's literal matches claims it, and a writer after it sees
    the source and adds nothing — one source, one `reads_from`, which is
    ADR-0022 §1's "one store, one or more sources" read from the other
    side. Two read sites that share a call target in one file are one
    source (1.3a's identity, `untrusted_source_id`), so the second is
    claimed too.

    The skips are the step's stated limits, each a fact about the read
    site and not a case that went wrong: 1.3a already built a source for
    it (§3's precedence), it names no literal (§4), the writer's store
    does not exist (no store, no pair), its literal names a different
    store (no link), or its file was not parsed (no reader, no node).
    """
    store_nodes = {node.id: node for node in stores}
    by_line, by_identity = _source_index(delta)
    readers = _reader_index(delta)
    reads = list(fs_reads)
    created: list[Node] = []
    edges: dict[str, Edge] = {}
    claimed: set[str] = set()

    for site in upload_sites:
        linked = _writer_stores(site, delta, store_nodes)
        if not linked:
            continue
        for read in reads:
            if read.identity_hint is None:
                continue
            if not _site_covers(site, read.file_path, imports_by_file):
                continue
            target = _call_target(read.call_site)
            if target is None:
                continue
            node_id = _untrusted_source_id(SOURCE_TYPE, target, read.file_path)
            if node_id in claimed:
                continue
            if _already_a_source(read, target, node_id, by_line, by_identity):
                continue
            for store in linked:
                confidence = _reader_link_confidence(
                    read, store, site, imports_by_file, modules
                )
                if confidence is None:
                    continue
                node = _created_source(
                    read, target, node_id, modules, site
                )
                if node is None:
                    break
                claimed.add(node_id)
                created.append(node)
                _add(edges, _reads_from_edge(store.id, node_id, confidence))
                reader_id = _reader_node_id(read, node, site, readers)
                if reader_id is not None:
                    _add(edges, _has_access_to_edge(node_id, reader_id))
                break

    return created, list(edges.values())


def _writer_stores(
    site: UploadSite,
    delta: "GraphDelta",
    stores: Mapping[str, Node],
) -> list[Node]:
    """The stores this writer's `writes_to` edges name, in delta order.

    The store the creating half links to is the one the writer stage
    created (`writer.find_upload_stores`), read back off the delta rather
    than recomputed: the edge is the fact, and the stage has already
    merged it (`store_resolver._merge_edges`). An empty list is "no
    store, no pair": a handler whose writes this scan did not recognize
    is still an upload handler, and still earns no source.
    """
    targets = {
        edge.target_id
        for edge in delta.edges
        if edge.kind == "writes_to" and edge.source_id == site.entry_point.id
    }
    return [node for node in stores.values() if node.id in targets]


def _created_source(
    read: ReadSite,
    target: str,
    node_id: str,
    modules: Mapping[str, ast.Module] | None,
    site: UploadSite,
) -> Node | None:
    """The `untrusted_source` node for one read site (ADR-0022 §5).

    The shape is 1.3a's (`untrusted_sources._source_node`), attribute for
    attribute: `source_type`, `call_target`, `source_file`, `line_start`,
    `reader_kind`, and `reader` / `reader_container` when there is an
    enclosing symbol. No `parent_server` (that is an
    `mcp_tool_description` attribute), and no capabilities: a source is a
    place, and a place does not act.

    `None` means the read site's file was not parsed, so the reader is
    not knowable — see `attribute_source_type`.
    """
    module = _module_for(modules, read.file_path)
    if module is None:
        return None
    reader, container = _enclosing_symbol(module, read.line)
    attributes: dict[str, object] = {
        "source_type": SOURCE_TYPE,
        "call_target": target,
        "source_file": read.file_path,
        "line_start": read.line,
        "reader_kind": _reader_kind(reader, container),
    }
    if reader is not None:
        attributes["reader"] = reader
    if container is not None:
        attributes["reader_container"] = container
    return Node(
        id=node_id,
        kind=SOURCE_KIND,
        name=f"{SOURCE_TYPE}:{target}",
        host_id=_graph_ids()[0],
        attributes=attributes,
        content_hash=None,
    )


def _reader_link_confidence(
    read: ReadSite,
    store: Node,
    site: UploadSite,
    imports_by_file: Mapping[str, list[str]],
    modules: Mapping[str, ast.Module] | None,
) -> float | None:
    """`identity_confidence` for one `reads_from store → source` (§8).

    `resolver._link_confidence`'s shape for the reader's side of a store,
    with the two differences ADR-0022 §5 names: the base is the read
    site's own (`ReadSite.confidence_base`, the family's 0.3 — the
    writer's 0.5 is the *writer's* signal and it is on the other link),
    and the code-context row is read off the import table and the parsed
    modules rather than off two counter-sites, because the writer's side
    here is a file and not a site (`_code_context`).

    `None` means no link, and `literal_match` decides it: two different
    literals name two stores, and neither side naming one is §5's "no
    store". A read site with no literal cannot reach this function — the
    caller skips it — so the one-side row is the store's alone.
    """
    hint = store.attributes.get("identity_hint")
    if not isinstance(hint, str):
        return None
    literal = literal_match(read.identity_hint, hint)
    if literal is None:
        return None

    signals = [literal]
    context = _code_context(
        site.file_path, read.file_path, imports_by_file, modules
    )
    if context > 0.0:
        signals.append(context)
    return combine_signals(read.confidence_base, signals)


def _code_context(
    writer_file: str,
    reader_file: str,
    imports_by_file: Mapping[str, list[str]],
    modules: Mapping[str, ast.Module] | None = None,
) -> float:
    """§8's code-context signal for the reader's link.

    The three rows are `confidence.code_context`'s, and one of them is
    the answer — the strongest, the ADR-0021 §8 way — in this order:

      - a **shared wrapper** both files reach → +0.3;
      - else the **same module** → +0.2;
      - else **one file importing the other** → +0.1;
      - else `0.0`.

    The weights are `confidence`'s constants and the ranking is the
    brief's (B.3b-user_upload-attribution-followup's O2: a same-file pair
    that also shares a wrapper scores the wrapper's +0.3, not `0.2 + 0.3`
    and not the same module's). `confidence.code_context` itself checks
    the same module first and pins that order in its own test; it is also
    not callable from here, because its inputs are two `WriteSite`s'
    `ModuleFacts` and this pass's writer side is a *file* — and its
    wrapper row is a shared *local import*, where this one reads the
    parsed module bodies (`confidence.module_facts` needs the manifest's
    `path_by_dotted` map, which this stage does not have).

    A pair in scope is the same file or an import apart (`_site_covers`),
    so the last row is unreachable **through the pipeline** and is the
    answer to the question the function is asked rather than a case that
    happens: a read site out of scope is skipped before this runs.
    """
    wrapper = _shared_wrapper(
        writer_file, reader_file, imports_by_file, modules
    )
    if wrapper is not None:
        return CODE_CONTEXT_SHARED_WRAPPER
    if reader_file == writer_file:
        return CODE_CONTEXT_SAME_MODULE
    if _shares_an_import_path(writer_file, reader_file, imports_by_file):
        return CODE_CONTEXT_SHARED_IMPORT
    return 0.0


def _shared_wrapper(
    writer_file: str,
    reader_file: str,
    imports_by_file: Mapping[str, list[str]],
    modules: Mapping[str, ast.Module] | None,
) -> str | None:
    """The path of a wrapper module both files reach, or None (§8).

    A wrapper is a module whose body defines a module-level client
    (`_defines_a_client`) and that **both** files reach — the file
    itself, or a module it imports (`_reaches_file`). The candidates are
    the parsed modules the stage offered, because the body is what says
    whether a module is a wrapper: a file this scan did not parse has no
    body to read, which is `_module_for`'s "no modules, no edge" answer
    to a different question.

    A file reaching *itself* is what makes one of the pair a wrapper
    rather than a third module: a handler module that defines
    `s3 = boto3.client("s3")` and a reader that imports it share it, and
    so does a same-file pair whose own file defines one. A wrapper only
    one of the two files reaches is not shared.

    The answer is a path rather than a `bool` so the caller could name
    it, and the candidates are walked in sorted order so the answer does
    not depend on the stage's dict order.
    """
    if modules is None:
        return None
    for path in sorted(modules):
        if not _defines_a_client(modules[path]):
            continue
        if _reaches_file(writer_file, path, imports_by_file) and (
            _reaches_file(reader_file, path, imports_by_file)
        ):
            return path
    return None


def _reaches_file(
    file_path: str,
    candidate: str,
    imports_by_file: Mapping[str, list[str]],
) -> bool:
    """True if a file *is* `candidate`, or imports it.

    `_module_matches_file` on each import of the file — the rule
    `_shares_an_import_path` reads, and no new import resolution — plus
    the file's own path, because the wrapper can be one of the two files
    and a file does not import itself (`_shared_wrapper`).
    """
    if file_path == candidate:
        return True
    return any(
        _module_matches_file(module, candidate)
        for module in imports_by_file.get(file_path, ())
    )


def _defines_a_client(tree: ast.Module) -> bool:
    """True if a module defines a module-level client constructor (§8).

    The position and the spelling are the whole rule: a **module-level
    assignment** whose value is a call in `WRAPPER_CALLS`' closed set.
    `s3 = boto3.client("s3")` is a wrapper, and so is the annotated
    spelling of it; `def get_s3(): return boto3.client("s3")` is not
    (the client is not a module-level name), a module that only imports
    `boto3` is not (no constructor in its body), and a constructor
    nested in a top-level `if` or `try` is not either — the body is read
    one level deep, which is the brief's "module level". The call's
    return value is not followed and its receiver is not resolved: the
    spelling is the test.
    """
    for statement in tree.body:
        value = _assigned_value(statement)
        if isinstance(value, ast.Call) and (
            _call_target(value) in WRAPPER_CALLS
        ):
            return True
    return False


def _assigned_value(statement: ast.stmt) -> ast.expr | None:
    """The expression a top-level statement binds to a name, or None.

    `s3 = boto3.client(...)` and `s3: Client = boto3.client(...)` bind
    one; an `import`, a `def`, a `class` and a bare call do not, which is
    what keeps the wrapper rule on module-level *names*
    (`_defines_a_client`).
    """
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        return statement.value
    return None


def _reads_from_edge(store_id: str, source_id: str, confidence: float) -> Edge:
    """`store → source`, structural, `observed_by=["ast"]` (§3–§4).

    The same discipline the resolver applies to its own `reads_from`
    edges: a declaration and not a path, no data-flow claim
    (`data_flow_confirmed` stays `None`, the `Edge` default), and the
    confidence on `identity_confidence` because the question is whether
    the two call sites name one place.
    """
    host_id, edge_id = _graph_ids()
    return Edge(
        id=edge_id(store_id, source_id, "reads_from"),
        source_id=store_id,
        target_id=source_id,
        kind="reads_from",
        host_id=host_id,
        flow_kind="structural",
        identity_confidence=confidence,
    )


def _has_access_to_edge(source_id: str, reader_id: str) -> Edge:
    """`source → reader`, control, `observed_by=["ast"]` (1.3a's read edge).

    `untrusted_sources._read_edge`'s shape, and it is what makes the
    created source an **anchor** the query layer can walk: a read chain
    starts at the source and its first hop is this edge
    (`graph.query._read_chains_from`). `flow_kind` is left at the model's
    `control` — what `FlowKindStage` pins for a read edge, and the
    attribution runs after it — and no data-flow claim is made
    (ADR-0016, decision 3).
    """
    host_id, edge_id = _graph_ids()
    return Edge(
        id=edge_id(source_id, reader_id, "has_access_to"),
        source_id=source_id,
        target_id=reader_id,
        kind="has_access_to",
        host_id=host_id,
    )


def _add(edges: dict[str, Edge], edge: Edge) -> None:
    """Keep one edge per id, first wins.

    An edge id is `(source, target, kind)`, so two read sites that share
    a call target in one file — one source, 1.3a's identity — would
    otherwise append the same link twice. The delta is a list and the id
    is its primary key, so the second row is the one that must not be
    written.
    """
    edges.setdefault(edge.id, edge)


def _source_index(
    delta: "GraphDelta",
) -> tuple[dict[tuple[str, int], str], dict[tuple[str, str], str]]:
    """The delta's existing sources, by line and by `(file, call_target)`.

    `resolver._source_index`'s by-line table, plus the identity ADR-0022
    §3 names: a source node is placed by the read site it was built for,
    so `(source_file, line_start)` is that read site, and
    `(source_file, call_target)` is 1.3a's `untrusted_source_id` key minus
    the type this pass does not know. Either hit is "this read site
    already has a source", and the creating half skips it.
    """
    by_line: dict[tuple[str, int], str] = {}
    by_identity: dict[tuple[str, str], str] = {}
    for node in delta.nodes:
        if node.kind != SOURCE_KIND:
            continue
        file_path = node.attributes.get("source_file")
        if not isinstance(file_path, str):
            continue
        line = node.attributes.get("line_start")
        if isinstance(line, int):
            by_line.setdefault((file_path, line), node.id)
        target = node.attributes.get("call_target")
        if isinstance(target, str) and target:
            by_identity.setdefault((file_path, target), node.id)
    return by_line, by_identity


def _already_a_source(
    read: ReadSite,
    target: str,
    node_id: str,
    by_line: Mapping[tuple[str, int], str],
    by_identity: Mapping[tuple[str, str], str],
) -> bool:
    """True if this read site already has an `untrusted_source` node.

    Three readings, and any of them is enough: the read site's own line
    (the source 1.3a built for this very call), the read site's
    `(source_file, call_target)` (a source for the same call spelled at
    another line), and the id this pass would create (a node the pass
    itself, or the delta it was handed, already holds).
    """
    if (read.file_path, read.line) in by_line:
        return True
    if (read.file_path, target) in by_identity:
        return True
    return node_id in by_line.values() or node_id in by_identity.values()


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
        if _site_covers(site, file_path, imports_by_file):
            return True
    return False


def _site_covers(
    site: UploadSite,
    file_path: str,
    imports_by_file: Mapping[str, list[str]],
) -> bool:
    """True if **this** writer's scope covers `file_path`.

    The per-site half of `_in_scope`, and the one the creating half
    needs: a read site is claimed by a particular writer, because the
    store that link comes out of is that writer's. `_in_scope` is the
    same question with "any site" as the answer, which is what the
    stamping half asks.
    """
    if file_path == site.file_path:
        return True
    return _shares_an_import_path(site.file_path, file_path, imports_by_file)


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


def _reader_index(delta: "GraphDelta") -> dict[tuple[str, str], str]:
    """`(source_file, name)` → node id, for the delta's nodes.

    `untrusted_sources._reader_index`'s rule, restated rather than
    imported: two names map to one node — the node's own `name` and, for
    an entry point, its `handler` — and first write wins, so the delta's
    stage order breaks a tie in favour of the earlier, more specific node.
    A node without a `source_file` is not a reader candidate.
    """
    index: dict[tuple[str, str], str] = {}
    for node in delta.nodes:
        file_path = node.attributes.get("source_file")
        if not isinstance(file_path, str):
            continue
        index.setdefault((file_path, node.name), node.id)
        handler = node.attributes.get("handler")
        if isinstance(handler, str):
            index.setdefault((file_path, handler), node.id)
    return index


def _reader_node_id(
    read: ReadSite,
    source: Node,
    site: UploadSite,
    readers: Mapping[tuple[str, str], str],
) -> str | None:
    """The node the created source's read is attributed to, or None.

    The enclosing definition first (a function or a method), then the
    enclosing class — `untrusted_sources._reader_node_id`'s order, and
    for its reason: a method that was never classified has no node, but
    the class that exercises the capability does. A read of a
    **module-level** entry point then falls back to the site's own entry
    point node: a Streamlit read in a top-level function has no
    definition node to hang off (the module is the entry, and its node is
    the module's), and without the fallback the source would be an anchor
    with no read chain and the writer's store would pair with nothing.

    `None` is 1.3a's answer for a read with no node to reach: no edge
    rather than an invented target.
    """
    reader = source.attributes.get("reader")
    container = source.attributes.get("reader_container")
    for name in (reader, container):
        if not isinstance(name, str):
            continue
        node_id = readers.get((read.file_path, name))
        if node_id is not None:
            return node_id
    if _module_level(site):
        return site.entry_point.id
    return None


def _enclosing_symbol(
    module: ast.Module, line: int
) -> tuple[str | None, str | None]:
    """The definition whose body contains `line`: `(reader, container)`.

    `untrusted_sources._reads_in`'s attribution rule, restated: the
    innermost **top-level** definition — a top-level function, or a
    method of a top-level class with the class as the container — and
    `(None, None)` for a call in the module body, where the reader is the
    module itself. A call inside a nested function belongs to the
    definition that lexically encloses it, which is what `ast.walk` does
    there and what the range check here does.
    """
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _spans(node, line):
                return node.name, None
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(
                    item, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and _spans(item, line):
                    return item.name, node.name
    return None, None


def _spans(node: ast.AST, line: int) -> bool:
    """True if a definition's body contains `line` (one line if unknown)."""
    return node.lineno <= line <= (node.end_lineno or node.lineno)


def _reader_kind(reader: str | None, container: str | None) -> str:
    """Where the read happens: a method, a function, or the module.

    `untrusted_sources._reader_kind`'s rule, on the two names
    `_enclosing_symbol` read off the AST.
    """
    if container is not None:
        return "method"
    if reader is not None:
        return "function"
    return "module"


def _module_level(site: UploadSite) -> bool:
    """True for an upload whose entry point lives at module level.

    `writer._module_level`'s rule, on the same constant
    (`MODULE_LEVEL_ENTRY_KINDS`): the marker is `entry_kind`, and not the
    `entry_points` stage's `<module>` sentinel, which is on the record and
    never on the node.
    """
    return (
        site.entry_point.attributes.get("entry_kind")
        in MODULE_LEVEL_ENTRY_KINDS
    )


def _module_for(
    modules: Mapping[str, ast.Module] | None, file_path: str
) -> ast.Module | None:
    """One file's parsed module, or None when the caller passed none."""
    if modules is None:
        return None
    return modules.get(file_path)


def _call_target(call: ast.Call) -> str | None:
    """The dotted call a read site is spelled with, or None.

    `capability_extractor._dotted_name`'s walk (`open`, `aiofiles.open`,
    `builtins.open`), restated with the `fs` family's spelling. A
    `Path(...)` method has no dotted receiver — the walk answers `None`
    for a call — so the method's own name is the target there
    (`read_text`), and the file completes the identity. `None` is a call
    shape with no name at all, and a read site with no target cannot be
    given an identity: it earns nothing.
    """
    func = call.func
    parts: list[str] = []
    current: ast.expr = func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        parts.reverse()
        return ".".join(parts)
    attr = getattr(func, "attr", None)
    return attr if isinstance(attr, str) and attr else None


def _untrusted_source_id(source_type: str, target: str, file_path: str) -> str:
    """1.3a's `untrusted_source_id`, imported lazily (see `_graph_ids`)."""
    from ....parsing.normalizer_helpers import untrusted_source_id

    return untrusted_source_id(source_type, target, file_path)


def _graph_ids() -> tuple[str, Callable[[str, str, str], str]]:
    """`(host_id, edge_id)` — the pipeline's id convention.

    Imported here and not at module level, on purpose:
    `parsing.normalizer_stages.store_resolver` imports this module, so a
    module-level import of the helpers would close a cycle for a caller
    that imports `aicorellator.store` first. `writer._graph_ids` is the
    same accessor for the same reason; this is the attribution's copy of
    the two names it needs.
    """
    from ....parsing.normalizer_helpers import HOST_ID, edge_id

    return HOST_ID, edge_id


__all__ = [
    "SOURCE_KIND",
    "SOURCE_TYPE",
    "WRAPPER_CALLS",
    "attribute_source_type",
]

