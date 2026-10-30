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
context-based writer, and the step is four calls —
`UserUploadContext.identify` over the files that have entry points,
`writer.find_upload_stores` for the `writes_to` edges,
`attribution.attribute_source_type` for the `source_type` on the read
sites in a writer's scope, and — since B.3b-user_upload-attribution — the
`untrusted_source` nodes and the `reads_from` / `has_access_to` edges
that pass creates (ADR-0022). Its detector registry holds **four names**
today — `fastapi` (B.3b-user_upload-fastapi), `streamlit`
(B.3b-user_upload-streamlit), `django` (B.3b-user_upload-django) and
`flask` (B.3b-user_upload-flask) — so the step identifies an upload
wherever one of them recognizes an entry point, and stays a no-op on the
files it does not.

Why the context is called from the stage and not from the resolver: the
resolver's job is pairing a write site with a read site, and the upload
writer is a claim about *one* side (this handler is a writer). The
resolver's `resolve` is unchanged by this step, and the context is a
second producer on top of a family's sites, which is where the pipeline
sees the delta.

B.3b-llm_fallback adds a **third** step, after both of those: the
store-identity LLM fallback (ADR-0023). It is the last resort for the
sites no deterministic producer could name — a `Bucket=var`, an
`open(path)`, an f-string with no literal part — and it runs here because
this is the only place that holds all three of its inputs at once: the
families' sites (`_family_sites`), the upload sites whose scope rule
decides which of them are in scope, and the delta both ends are resolved
against. Its output is a `store` with `identity_source='llm'` and its
`writes_to` / `reads_from` edges, at `identity_confidence=0.2` — weak, so
the group it can form is out of the default enumeration. It is **opt-in**
(`llm_fallback.mode`, `off` by default) and needs the scan's policy and
two paths, which arrive on the context (`scan_config`, `config_path`,
`cache_path`): a context without them means the scan did not offer the
layer, and the step is a no-op. `run_scan.py` is what fills them in, and
it is what reports `FallbackConfigError` / `FallbackError` as a scan
failure — the first a policy that asks for a layer nobody configured, the
second `mode: required` and a site the model could not name.

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
from ...store.families.base import ReadSite, WriteSite
from ...store.llm_fallback import FamilySite
from ...store.llm_fallback import run as run_fallback
from ...store.registry import (
    registered_families,
    resolve as resolve_families,
)
from ...store.resolver import StoreResolver
from ..framework_detectors.cross_file import _module_matches_file
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

        The fallback runs last (B.3b-llm_fallback), on the upload sites
        the second step identified: it is the only step that reaches a
        model, and it is a no-op unless the scan offered it a policy and
        two paths (`ctx.scan_config` / `config_path` / `cache_path`) and
        the policy says `auto` or `required`.
        """
        delta.edges.extend(self._resolver.resolve(delta, ctx))
        upload_sites = self._link_uploads(delta, ctx)
        self._fill_identity_gaps(delta, ctx, upload_sites)
        return delta

    # --------------------------------------------------------
    # The upload context (B.3b-user_upload-core)
    # --------------------------------------------------------

    def _link_uploads(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> list[UploadSite]:
        """`entry_point → store` for the handlers that accept an upload.

        Four calls, in the order the brief names them: identify the
        uploads, find the stores each writer reaches, attribute the read
        sites in the writers' scope — which is where the sources 1.3a did
        not build are created and the store's `reads_from` and the
        source's `has_access_to` are emitted. With no upload site the
        first call returns nothing and the step stops there — before the
        `fs` family is asked for its sites, so the no-op costs a delta
        scan and no parse.

        The nodes the attribution created are appended here and not by it
        (`_merge_nodes`): a node the pass only *stamped* is already in the
        delta, and appending it again would put one node in the list
        twice.

        Returns the upload sites it identified, because the fallback's
        scope rule is theirs (ADR-0022 §1) and re-identifying them would
        be a second pass over the entry points for the same answer.
        """
        sites = self._upload_sites(delta, ctx)
        if not sites:
            return []

        fs_writes, fs_reads = _fs_sites(ctx)
        store_nodes = [node for node in delta.nodes if node.kind == "store"]
        modules = _modules_for(ctx, sites, fs_writes, fs_reads)

        _merge_edges(delta, find_upload_stores(
            delta=delta,
            upload_sites=sites,
            call_graph=ctx.entry_points.call_graph,
            fs_writes=fs_writes,
            store_nodes=store_nodes,
            modules=modules,
        ))
        _merge_nodes(delta, attribute_source_type(
            delta=delta,
            upload_sites=sites,
            imports_by_file=ctx.entry_points.imports,
            fs_reads=fs_reads,
            stores=store_nodes,
            modules=modules,
        ))
        return sites

    # --------------------------------------------------------
    # The store-identity fallback (B.3b-llm_fallback)
    # --------------------------------------------------------

    def _fill_identity_gaps(
        self,
        delta: "GraphDelta",
        ctx: NormalizeContext,
        upload_sites: list[UploadSite],
    ) -> None:
        """`store` + `writes_to` / `reads_from` for the sites nothing named.

        ADR-0023's step, and the stage's only call into a model. It runs
        *after* the resolver and the attribution, because its input is the
        sites those two could not name and its output is a store the
        attribution's `reads_from` may already point at — a fallback store
        can be the target of an attributed source, and that link is weak
        like the rest.

        The guard is the context: no policy, no config path or no cache
        path means the scan did not offer the layer, and the step is a
        no-op. With the policy at `off` the step returns before the
        families are walked, so an opt-out scan pays nothing for the
        sites it will not use.

        `FallbackConfigError` and `FallbackError` propagate: the first is
        a policy that asks for a layer nobody configured, the second is
        `mode: required` and a gap, and both are the caller's to report
        (`run_scan.py` exits on them). A raising *family* is not possible
        here — `_family_sites` applies the resolver's own rule, and the
        fallback's own failure modes are the two above.
        """
        if (
            ctx.scan_config is None
            or ctx.config_path is None
            or ctx.cache_path is None
        ):
            return
        if ctx.scan_config.llm_fallback.mode == "off":
            return

        stores, edges = run_fallback(
            sites=_family_sites(ctx),
            delta=delta,
            upload_sites=upload_sites,
            imports_by_file=ctx.entry_points.imports,
            ctx=ctx,
            scan=ctx.scan_config,
            config_path=ctx.config_path,
            cache_path=ctx.cache_path,
            host_id=ctx.host_id,
        )
        _merge_nodes(delta, stores)
        _merge_edges(delta, edges)

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


def _merge_nodes(delta: "GraphDelta", nodes: list["Node"]) -> None:
    """Append the nodes the delta does not already hold, by id.

    The attribution returns the nodes it gave the role to, and only the
    ones it *created* are new: a stamped node is already in the delta, so
    appending it again would put one node in the list twice and the
    repository's primary key would reject the second row. The id is the
    graph's primary key, so this is the same discipline `_merge_edges`
    applies to an edge, minus the confidence comparison — two nodes with
    one id are one node, and the delta's copy is the one already there.
    """
    existing = {node.id for node in delta.nodes}
    for node in nodes:
        if node.id in existing:
            continue
        existing.add(node.id)
        delta.nodes.append(node)


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


def _fs_sites(
    ctx: NormalizeContext,
) -> tuple[list[WriteSite], list[ReadSite]]:
    """The `fs` family's write and read sites, over the scan's files.

    The resolver runs the same pass for its own edges
    (`StoreResolver._sites`) and does not hand its sites out, and the
    upload step does not change the resolver, so this is the stage's own
    copy of the loop: the family from the registry, the manifest's Python
    files, and `ctx.module_for` — already parsed by the resolver in the
    same scan, so this costs the detection and not a second parse. The
    duplication is the seam's price, and the fix, when it is wanted, is
    one line in `StoreResolver` remembering the sites it collected.

    Both directions come out of one loop because both are read off the
    same module (`FsFamily` walks it once per `detect_*` call) and both
    are needed together: the writer links an entry point to the stores
    the *writes* name, and the attribution links those stores to the
    sources the *reads* name (B.3b-user_upload-attribution).

    A family that raises is logged and contributes nothing (ADR-0011's
    rule, the same one `StoreResolver._detect` applies).
    """
    writes: list[WriteSite] = []
    reads: list[ReadSite] = []
    for family in resolve_families([FS_FAMILY]):
        for entry in ctx.manifest.files:
            if entry.language != "python":
                continue
            module = ctx.module_for(entry.path)
            if module is None:
                continue
            try:
                writes.extend(family.detect_writes(module, entry.path))
                reads.extend(family.detect_reads(module, entry.path))
            except Exception:
                log.exception(
                    "store family %s failed on %s", family.name, entry.path,
                )
    return writes, reads


def _family_sites(ctx: NormalizeContext) -> list[FamilySite]:
    """Every registered family's sites, with the family that reported them.

    The fallback's input (B.3b-llm_fallback). The resolver runs the same
    loop for its own edges and does not hand its sites out, so this is the
    stage's second copy of it (`_fs_sites` is the first, for the upload
    step) — the same price the stage already pays, for the same seam.

    All families, not `fs` alone: the store an unresolved `Bucket=var`
    names is `s3`'s, and one an unresolved `cursor.execute` names is
    `db`'s. Both directions are collected, because a site is a write *or*
    a read and the fallback creates the edge the site's kind asks for.

    No `context` is passed to the families: the resolver hands its own,
    and the stage builds a resolver with none (`StoreResolver()`), so this
    is the same call the pipeline's resolver makes. `ctx.module_for` is
    the scan-wide parse cache the resolver has already filled, so a family
    costs the detection and not a second parse. A family that raises is
    logged and contributes nothing (ADR-0011's rule, the same one
    `StoreResolver._detect` and `_fs_sites` apply).
    """
    sites: list[FamilySite] = []
    for family in registered_families():
        for entry in ctx.manifest.files:
            if entry.language != "python":
                continue
            module = ctx.module_for(entry.path)
            if module is None:
                continue
            for detect in (family.detect_writes, family.detect_reads):
                try:
                    found = detect(module, entry.path)
                except Exception:
                    log.exception(
                        "store family %s failed on %s",
                        family.name, entry.path,
                    )
                    continue
                sites.extend(
                    FamilySite(family.name, site) for site in found
                )
    return sites


def _modules_for(
    ctx: NormalizeContext,
    sites: list[UploadSite],
    fs_writes: list[WriteSite],
    fs_reads: list[ReadSite],
) -> dict[str, ast.Module]:
    """The parsed modules the writer and the attribution need, by path.

    The writer reads one thing out of a file: which function a write call
    lives in (`writer._enclosing_functions`). The attribution reads two:
    which definition a read call lives in (`attribution._enclosing_symbol`,
    and therefore the source node's `reader_kind` and its reader), and
    whether a module both files reach defines the client the store is
    reached through (`attribution._defines_a_client`, §8's shared-wrapper
    row). The files they need are therefore the entry points' files, the
    sites' files — all already parsed in this scan, by the identify pass
    and by `_fs_sites` — and, since B.3b-user_upload-attribution-followup-2,
    **the modules those files import** (`_imported_modules`): a wrapper in
    a third file is the shape §8's row exists for, and it has no body to
    read until this parses it. Nothing else is offered, so a scan with an
    upload in one file does not hand over the whole target.
    """
    needed = {site.file_path for site in sites}
    needed.update(write.file_path for write in fs_writes)
    needed.update(read.file_path for read in fs_reads)
    modules: dict[str, ast.Module] = {}
    for file_path in needed:
        module = ctx.module_for(file_path)
        if module is not None:
            modules[file_path] = module
    for file_path in _imported_modules(ctx, modules):
        module = ctx.module_for(file_path)
        if module is not None:
            modules[file_path] = module
    return modules


def _imported_modules(
    ctx: NormalizeContext, known: dict[str, ast.Module]
) -> list[str]:
    """The manifest files the `known` files import, one hop out (§8).

    The import table is the entry-point pass's (`entry_points.run`
    records the dotted names each file imports, relative imports
    skipped), and a name is resolved with
    `cross_file._module_matches_file` — the trailing-segment match the
    cross-file detectors already use, so a name that resolves to a file
    for them resolves to it here. No new import resolution is introduced,
    and the parse is `ctx.module_for`'s cache, which the resolver has
    already filled for every file it visited.

    **One hop.** A module imported by an imported module is not parsed:
    the wrapper §8's row is about is the module the *sites* name, and a
    two-hop chain (a module that imports the client from a third one) is
    a longer walk with its own false-positive surface, so it is a
    separate step.

    **Ambiguity is skipped.** A dotted name matches by trailing path
    segments, so `storage` matches `storage.py` and `pkg/storage.py`
    alike. When more than one manifest file matches, the name is dropped
    and **neither** file is parsed: the table says which *name* a file
    imports, not which file it means, and picking one would put an
    unrelated module's constructor in front of the attribution. The
    wrapper row then does not fire for that pair, which is the safe
    answer — the row is a signal, not an obligation.

    The result is sorted and deduplicated, so the map is a property of
    the imports and not of the order the files were visited in.
    """
    imports = ctx.entry_points.imports
    paths = [
        entry.path
        for entry in ctx.manifest.files
        if entry.language == "python"
    ]
    found: set[str] = set()
    for file_path in sorted(known):
        for module in imports.get(file_path, ()):
            matches = [
                path for path in paths if _module_matches_file(module, path)
            ]
            if len(matches) == 1:
                found.add(matches[0])
    return sorted(found)


__all__ = ["StoreResolverStage"]
