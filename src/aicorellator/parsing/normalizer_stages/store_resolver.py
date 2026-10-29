"""StoreResolverStage — store nodes, `writes_to` and `reads_from` (B.3b).

Run order: 19 of 20 (see `normalizer_stages.STAGES`).

ADR-0021 §12: store identity is decided by **families** — one module per
technology a store is reached through — registered in
`aicorellator.store.registry`. This stage is the seam between that
subsystem and the pipeline: it builds a `StoreResolver` and calls it once
on the delta, and the resolver returns the edges. B.3b-core shipped the
seam with an **empty registry**, so the stage was a no-op on every scan;
B.3b-fs then registered `fs` — a family module plus a line in
`families/__init__.py` — and the stage produces an edge wherever that
family recognizes a store literal, with no change here. That is the
shape a second family reuses.

B.3b-user_upload-core adds a second step to the same seam, after the
resolver's own edges are in: the **upload context**. ADR-0021 §8
(revision 2) makes an entry-point handler that accepts an upload a
context-based writer, and the step is three calls —
`UserUploadContext.identify` over the files that have entry points,
`writer.find_upload_stores` for the `writes_to` edges, and
`attribution.attribute_source_type` for the `source_type` on the read
sites in a writer's scope. Its detector registry holds **two names**
today — `fastapi` (B.3b-user_upload-fastapi) and `streamlit`
(B.3b-user_upload-streamlit) — so the step identifies an upload wherever
one of them recognizes an entry point, and stays a no-op on the files it
does not.

Why the context is called from the stage and not from the resolver: the
resolver's job is pairing a write site with a read site, and the upload
writer is a claim about *one* side (this handler is a writer). The
resolver's `resolve` is unchanged by this step, and the context is a
second producer on top of a family's sites, which is where the pipeline
sees the delta.

Why a stage, and not a call in `run_scan.py` after `normalize`:

  - `normalize()` is where every node and edge in this graph is built,
    and the pipeline is what a reader consults to find out which step
    produced something (`NormalizerStage.name` exists for exactly that).
    A producer that ran in the CLI and not in `normalize` would leave
    two pipelines in the tree: `run_scan.py` with stores, and every
    other caller of `normalize` — the pipeline tests among them —
    without them. B.3b-core has nothing to emit, which is the moment to
    choose the seam, not a moment at which the difference is invisible.
  - the resolver needs the whole graph: the component a write call
    belongs to, and the `untrusted_source` a read belongs to, are nodes
    stages 2–10 created or replaced (`detector_merge` re-points ids), so
    "after `normalize`" and "after the merge" are the same requirement.
    The upload step needs the entry-point nodes the same way, and it
    reads the store nodes the resolver has just put in the delta.

Why *here*, and not after `guardrail_edges` (stage 20): the two share
their only constraint — the merge — and read nothing the other writes.
`guardrail_edges` documents itself as the last slot, which is a claim
about *its* position and not about this one, so the newer stage goes
before it and neither docstring lies.

What this stage does **not** do: `flow_kind` (stage 12) is not a
constraint, because the resolver classifies its own edges `structural`
(ADR-0021 §4) and `FlowKindStage` skips `STRUCTURAL_EDGE_KINDS` anyway —
and the upload writer's edges, added after stage 12, carry their
`flow_kind` themselves for the same reason. `find_chain_groups` (B.3a)
is not a constraint either: the join lives in the query layer and reads
what this stage wrote.
"""

from __future__ import annotations

import ast
import logging
from typing import TYPE_CHECKING

from ...graph.models import Edge
from ...store.context.upload.attribution import attribute_source_type
from ...store.context.upload.base import UploadSite
from ...store.context.upload.context import UserUploadContext
from ...store.context.upload.writer import FS_FAMILY, find_upload_stores
from ...store.families.base import WriteSite
from ...store.registry import resolve as resolve_families
from ...store.resolver import StoreResolver
from ..normalizer_context import NormalizeContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...graph.models import Node
    from ..ast_normalizer import GraphDelta

log = logging.getLogger(__name__)


class StoreResolverStage:
    """One call to `StoreResolver`, on the delta the pipeline built."""

    name = "store_resolver"

    def __init__(
        self,
        resolver: StoreResolver | None = None,
        uploads: UserUploadContext | None = None,
    ) -> None:
        #: Default: the registry, read when `resolve` runs — see
        #: `StoreResolver`. A caller (a test, a future variant) may hand
        #: in one built with explicit families or a context.
        self._resolver = resolver or StoreResolver()
        #: The upload context, on the same rule: `UserUploadContext()`
        #: with no detectors reads the *detector* registry when
        #: `identify` runs, so a detector registered after this stage was
        #: built still counts — and `STAGES` is built at import, before
        #: any detector module is imported.
        self._uploads = uploads or UserUploadContext()

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        """Append the store edges the delta earns. No family, no edges.

        The resolver puts its store *nodes* in the delta itself (that is
        the only way a node can leave it) and returns the edges, which
        this stage appends. With the registry empty, `resolve` returns
        `[]` and the delta is untouched.

        The upload step runs after that, on the store nodes the resolver
        has just written: with no detector registered it identifies
        nothing, emits nothing and attributes nothing, so the delta is
        still the one B.3b-fs produced.
        """
        delta.edges.extend(self._resolver.resolve(delta, ctx))
        self._link_uploads(delta, ctx)
        return delta

    # --------------------------------------------------------
    # The upload context (B.3b-user_upload-core)
    # --------------------------------------------------------

    def _link_uploads(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> None:
        """`entry_point → store` for the handlers that accept an upload.

        Three calls, in the order the brief names them: identify the
        uploads, find the stores each writer reaches, attribute the read
        sites in the writers' scope. With no upload site the first call
        returns nothing and the step stops there — before the `fs`
        family is asked for its write sites, so the no-op costs a delta
        scan and no parse.

        `attribute_source_type` runs on the sites alone and not on the
        edges found: accepting an upload is what makes the reads in a
        handler's scope upload reads, and a handler whose stores this
        scan did not recognize is still an upload handler.
        """
        sites = self._upload_sites(delta, ctx)
        if not sites:
            return

        fs_writes = _fs_write_sites(ctx)
        store_nodes = [node for node in delta.nodes if node.kind == "store"]
        modules = _modules_for(ctx, sites, fs_writes)

        _merge_edges(delta, find_upload_stores(
            delta=delta,
            upload_sites=sites,
            call_graph=ctx.entry_points.call_graph,
            fs_writes=fs_writes,
            store_nodes=store_nodes,
            modules=modules,
        ))
        attribute_source_type(
            delta=delta,
            upload_sites=sites,
            imports_by_file=ctx.entry_points.imports,
        )

    def _upload_sites(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> list[UploadSite]:
        """Every upload the scan's files declare, in entry-point order.

        The files are the ones the delta holds an `entry_point` node
        for — a detector's sites name entry points, so a file with none
        has none — and each is parsed at most once (`ctx.module_for`).
        The entry-point nodes are read from the **delta** and not from
        `ctx.entry_node_by_handler`: the map is keyed by handler name
        alone, and the merge (stage 10) may have re-pointed the ids it
        holds.
        """
        sites: list[UploadSite] = []
        for file_path, nodes in _entry_nodes_by_file(delta).items():
            module = ctx.module_for(file_path)
            if module is None:
                continue
            sites.extend(self._uploads.identify(module, file_path, nodes))
        return sites


# ------------------------------------------------------------------
# What the upload step reads
# ------------------------------------------------------------------

def _merge_edges(delta: "GraphDelta", edges: list[Edge]) -> None:
    """Append the edges the delta does not already hold, by id.

    An edge id is `(source, target, kind)`, so two producers that claim
    one link produce one id — and the id is the graph's primary key, so
    the delta must not hold two rows for it. The stronger
    `identity_confidence` wins, which is the resolver's own
    `_keep_edge` rule: the upload writer's edge for a handler that writes
    into a store is stronger than the resolver's family-only edge for the
    same link (base 0.5 against 0.3), and this is where the two become
    one. Insertion order is kept on replacement, so the edge list stays a
    function of the sites.

    Only the upload edges go through this: the resolver's own edges are
    appended as they come (`resolve` keeps one per id itself), and a
    collision is possible exactly where two producers name one link —
    which is this step's case, because a plain route handler is not a
    `tool`, `sink` or `agent` node, so the resolver's writer for a write
    inside it is the `entry_point` node (`resolver._component_index`).
    """

    def confidence(edge: Edge) -> float:
        return edge.identity_confidence or 0.0

    positions = {edge.id: index for index, edge in enumerate(delta.edges)}
    for edge in edges:
        position = positions.get(edge.id)
        if position is None:
            positions[edge.id] = len(delta.edges)
            delta.edges.append(edge)
        elif confidence(edge) > confidence(delta.edges[position]):
            delta.edges[position] = edge


def _entry_nodes_by_file(
    delta: "GraphDelta",
) -> dict[str, list["Node"]]:
    """`source_file` → its `entry_point` nodes, in delta order.

    A detector is asked about one file with that file's entry points, so
    the index is what turns "which files may hold an upload" into a
    single pass over the delta — and into *no* pass over the manifest: a
    file the delta holds no entry point for cannot be one, so it is never
    parsed.
    """
    index: dict[str, list["Node"]] = {}
    for node in delta.nodes:
        if node.kind != "entry_point":
            continue
        file_path = node.attributes.get("source_file")
        if not isinstance(file_path, str):
            continue
        index.setdefault(file_path, []).append(node)
    return index


def _fs_write_sites(ctx: NormalizeContext) -> list[WriteSite]:
    """The `fs` family's write sites, over the scan's Python files.

    The resolver runs the same pass for its own edges
    (`StoreResolver._sites`) and does not hand its sites out, and
    B.3b-user_upload-core does not change the resolver, so this is the
    stage's own copy of the loop: the family from the registry, the
    manifest's Python files, and `ctx.module_for` — already parsed by the
    resolver in the same scan, so this costs the detection and not a
    second parse. The duplication is the seam's price, and the fix, when
    it is wanted, is one line in `StoreResolver` remembering the sites it
    collected.

    A family that raises is logged and contributes nothing (ADR-0011's
    rule, the same one `StoreResolver._detect` applies).
    """
    writes: list[WriteSite] = []
    for family in resolve_families([FS_FAMILY]):
        for entry in ctx.manifest.files:
            if entry.language != "python":
                continue
            module = ctx.module_for(entry.path)
            if module is None:
                continue
            try:
                writes.extend(family.detect_writes(module, entry.path))
            except Exception:
                log.exception(
                    "store family %s failed on %s", family.name, entry.path,
                )
    return writes


def _modules_for(
    ctx: NormalizeContext,
    sites: list[UploadSite],
    fs_writes: list[WriteSite],
) -> dict[str, ast.Module]:
    """The parsed modules the writer needs, keyed by path.

    The writer reads one thing out of a file: which function a write call
    lives in (`writer._enclosing_functions`). The files it needs are
    therefore the entry points' files and the write sites' files — both
    already parsed in this scan, by the identify pass and by
    `_fs_write_sites` — and nothing else is offered to it, so a scan with
    an upload in one file does not hand over the whole target.
    """
    needed = {site.file_path for site in sites}
    needed.update(write.file_path for write in fs_writes)
    modules: dict[str, ast.Module] = {}
    for file_path in needed:
        module = ctx.module_for(file_path)
        if module is not None:
            modules[file_path] = module
    return modules


__all__ = ["StoreResolverStage"]
