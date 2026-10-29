"""`StoreResolver` — from a family's call sites to store facts (ADR-0021).

The resolver is the one place a family's report becomes graph facts. It
runs **once per scan**, on the delta and the context, at the end of the
normalizer pipeline (`normalizer_stages.store_resolver`), and returns
the edges the caller appends:

    writes_to    writer → store              (the write chain's last hop)
    reads_from   store → untrusted_source    (the read site)
    store        one node per `(family, identity_hint)` the scan sees a
                 literal for (§1)

What it does, in order:

  1. **Ask each family.** For every Python file in the manifest, once,
     `detect_writes` and `detect_reads`. A family that raises is logged
     and contributes nothing (ADR-0011's rule), so one broken family
     cannot cost a scan the others.
  2. **Collect the stores.** The literal hints the family saw — from
     either side — are the stores of that family (§1: one node per
     `(family, identity_hint)` a literal names). No literal on either
     side means no store at all (§5).
  3. **Attach the sites.** A site with a literal attaches to its own
     store; an unresolved site (`identity_hint is None`) attaches to
     every store of its family, because §5 keeps the store the literal
     side named and leaves the unnamed side pointing at it with less
     confidence.
  4. **Resolve both ends.** A write site becomes an edge only when the
     delta holds a node for the symbol the call lives in; a read site
     only when the delta holds the `untrusted_source` it reads. No
     node, no edge — a component the graph does not know cannot be the
     first hop of a write chain, and a read of nothing cannot carry a
     `reads_from`.
  5. **Score the link** (§8, `store.confidence`): the literal signal,
     plus the strongest code-context signal against the sites on the
     other side of the same store.

Direction, flow kind and provenance are ADR-0021 §3–§4: `writes_to`
goes writer → store, `reads_from` store → source, both `structural`
(a declaration, not a path), `observed_by=["ast"]`, and
`data_flow_confirmed` `NULL` — the AST sees the call sites, and the
claim that they share a store is stated as a probability, not as a
trace.

The resolver does not read `store.confidence_threshold`. The threshold
governs the *join* (§9), and the join is `graph.query.find_chain_groups`
(B.3a): the graph holds every link the signals support, and the query
decides which of them are strong enough to enumerate by default.
"""

from __future__ import annotations

import ast
import logging
from typing import TYPE_CHECKING, Any, Callable

from ..graph.models import Edge, Node
from .confidence import (
    code_context,
    combine_signals,
    literal_match,
    module_facts,
)
from .families.base import Context, Family, ReadSite, WriteSite
from .registry import registered_families

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..parsing.ast_normalizer import GraphDelta
    from ..parsing.normalizer_context import NormalizeContext

log = logging.getLogger(__name__)


#: The flow kind of every edge this resolver emits (ADR-0021 §4): a
#: declaration — the AST sees a call site write to a store and a call
#: site read from one — and not a path, so `find_chains` never
#: traverses it (ADR-0015, decision 5).
STRUCTURAL = "structural"


def store_node_id(family: str, identity_hint: str) -> str:
    """Canonical id for a `store` node: `family` and the literal (§1).

    Content-derived like every other node id, so the same
    `(family, identity_hint)` is the same node on a re-scan and the
    store is updated instead of duplicated. It is the id
    `find_chain_groups` joins on (§6) and the id a `ChainGroup` carries
    as `store_id`.
    """
    host_id, _, sha256 = _graph_ids()
    key = f"store:{family}:{identity_hint}:{host_id}"
    return f"store:{sha256(key)}"


def _graph_ids() -> tuple[str, Callable[[str, str, str], str], Any]:
    """`(host_id, edge_id, sha256)` — the pipeline's id convention.

    Imported here and not at module level, on purpose:
    `parsing.normalizer_stages.store_resolver` imports this module, so a
    module-level import of the helpers would close a cycle for a caller
    that imports `aicorellator.store` first — this package would ask for
    `aicorellator.parsing`, whose stage package would ask for this
    module back while it is still being executed. The callers run once
    per scan, and the import is a `sys.modules` lookup by then.
    """
    from ..parsing.normalizer_helpers import HOST_ID, edge_id, sha256

    return HOST_ID, edge_id, sha256


class StoreResolver:
    """Calls the registered store families and returns their edges.

    `families=None` means **the registry, read when `resolve` runs**,
    not when the resolver is built: `normalizer_stages.STAGES` is built
    once at import, so a stage that captured instances at construction
    would never see a family registered later in the same process.
    `families=[]` is the other, explicit answer — no families, whatever
    the registry holds — and it is what a test uses to keep a scan's
    store edges under its own control.

    `context` is the per-family context (`families.base.Context`), and
    it is `None` until a context exists (B.3b-user_upload-core). It is
    threaded to every `detect_*` call, which is the whole of what the
    interface promises a context: a family may read it, and a family
    that needs nothing ignores it.
    """

    def __init__(
        self,
        families: list[Family] | None = None,
        context: Context | None = None,
    ) -> None:
        self._families = families
        self._context = context

    # --------------------------------------------------------
    # Entry point
    # --------------------------------------------------------

    def resolve(
        self, delta: "GraphDelta", ctx: "NormalizeContext"
    ) -> list[Edge]:
        """The store edges this delta earns, in deterministic order.

        **Nodes go into `delta`, edges come back.** A `store` node has no
        other way out — the return value is the edges, by the interface —
        and the delta is what the caller is about to write, so the
        resolver appends the store nodes to `delta.nodes` and hands the
        edges back for the caller to append (`normalizer_stages.
        store_resolver` is that caller). Nothing else in the delta is
        touched: the resolver reads its nodes and writes only stores.

        `ctx` supplies the files, their parsed modules, the scan-wide
        caches and the manifest paths the families and the code-context
        signal need.

        With no families registered — the state of the tree in
        B.3b-core — this returns `[]` and does not touch the delta.
        """
        families = (
            registered_families() if self._families is None else self._families
        )
        if not families:
            return []

        host_id, edge_id, _ = _graph_ids()
        components = _component_index(delta)
        by_line, by_type = _source_index(delta)
        path_by_dotted = _path_by_dotted(ctx.manifest)

        existing = {node.id for node in delta.nodes}
        edges: dict[str, Edge] = {}

        for family in families:
            writes, reads = self._sites(family, ctx, path_by_dotted)
            keys = _store_keys(writes, reads)

            for key in keys:
                node = _store_node(family.name, key, host_id)
                if node.id not in existing:
                    existing.add(node.id)
                    delta.nodes.append(node)

            for site in writes:
                writer_id = _writer_node_id(site, ctx, components)
                if writer_id is None:
                    continue
                for key in _attached_keys(site, keys):
                    confidence = _link_confidence(
                        site, key, keys, reads, write_side=True
                    )
                    if confidence is None:
                        continue
                    store_id = store_node_id(family.name, key)
                    _keep_edge(edges, Edge(
                        id=edge_id(writer_id, store_id, "writes_to"),
                        source_id=writer_id,
                        target_id=store_id,
                        kind="writes_to",
                        host_id=host_id,
                        flow_kind=STRUCTURAL,
                        identity_confidence=confidence,
                    ))

            for site in reads:
                target_id = _read_target_id(site, by_line, by_type)
                if target_id is None:
                    continue
                for key in _attached_keys(site, keys):
                    confidence = _link_confidence(
                        site, key, keys, writes, write_side=False
                    )
                    if confidence is None:
                        continue
                    store_id = store_node_id(family.name, key)
                    _keep_edge(edges, Edge(
                        id=edge_id(store_id, target_id, "reads_from"),
                        source_id=store_id,
                        target_id=target_id,
                        kind="reads_from",
                        host_id=host_id,
                        flow_kind=STRUCTURAL,
                        identity_confidence=confidence,
                    ))

        return list(edges.values())

    # --------------------------------------------------------
    # Site collection
    # --------------------------------------------------------

    def _sites(
        self,
        family: Family,
        ctx: "NormalizeContext",
        path_by_dotted: dict[str, str],
    ) -> tuple[list[WriteSite], list[ReadSite]]:
        """Every site one family reports, over the manifest's Python files.

        The files are visited in manifest order and each is parsed at
        most once (`ctx.module_for`, the scan-wide cache), so two
        families cost one parse between them. A file's facts are built
        only when it contributed a site: a module no family recognized
        costs nothing beyond the detector calls themselves.

        The family's own order is kept — within a file, and across
        files — so the edges it earns arrive in that order and the
        result is a property of the code and not of the walk.
        """
        writes: list[WriteSite] = []
        reads: list[ReadSite] = []

        for entry in ctx.manifest.files:
            if entry.language != "python":
                continue
            module = ctx.module_for(entry.path)
            if module is None:
                continue

            found_writes = self._detect(
                family, family.detect_writes, module, entry.path
            )
            found_reads = self._detect(
                family, family.detect_reads, module, entry.path
            )
            if found_writes or found_reads:
                facts = module_facts(entry.path, module, path_by_dotted)
                for site in [*found_writes, *found_reads]:
                    if site.facts is None:
                        site.facts = facts
            writes.extend(found_writes)
            reads.extend(found_reads)

        return writes, reads

    def _detect(
        self,
        family: Family,
        detect: Callable[..., list],
        module: ast.Module,
        file_path: str,
    ) -> list:
        """One detector call, with a raising family logged and not fatal.

        ADR-0011's rule for detectors, and the reason a family is not
        asked to be careful: a store that cannot be recognized costs its
        own edges and nothing else — not the family's other files, and
        not the other families.
        """
        try:
            return list(detect(module, file_path, context=self._context))
        except Exception:
            log.exception(
                "store family %s failed on %s", family.name, file_path,
            )
            return []


# ------------------------------------------------------------------
# Sites → stores
# ------------------------------------------------------------------

def _store_keys(
    writes: list[WriteSite], reads: list[ReadSite]
) -> list[str]:
    """The stores one family's sites name, sorted (§1).

    The literals seen on either side are the stores: "one node per
    `(family, identity_hint)` the scan sees a literal for". An empty
    list is §5's answer — no literal, no store — and it is what keeps
    an unresolved pair from becoming a store.

    Sorted, so the store nodes do not depend on the order the family
    reported its sites in. The family's order still decides the order
    *within* a store (the `writes_to` edges of one store keep their
    sites' order).
    """
    hints = {site.identity_hint for site in [*writes, *reads]}
    return sorted(hint for hint in hints if hint is not None)


def _attached_keys(site: WriteSite | ReadSite, keys: list[str]) -> list[str]:
    """The stores one site can name.

    A site with a literal names exactly its own store — a literal is an
    identity, and two call sites naming different literals name
    different stores (§8, "no link"). A site *without* one is a
    wildcard: §5 keeps the store the literal side named and lets the
    unnamed side point at it with a lower confidence, so an unresolved
    site attaches to every store its family has. With no literal at all
    on either side, `keys` is empty and this is empty too.
    """
    if site.identity_hint is None:
        return list(keys)
    return [site.identity_hint] if site.identity_hint in keys else []


def _link_confidence(
    site: WriteSite | ReadSite,
    key: str,
    keys: list[str],
    counterparts: list,
    *,
    write_side: bool,
) -> float | None:
    """`identity_confidence` for one (site, store) link (§8).

    The tools are `confidence.literal_match` and
    `confidence.code_context`, combined by `confidence.combine_signals`
    from the site's own `confidence_base` — the base the site carries,
    because a context raises it for the side it recognized. `None` means
    no link: the pair names different stores, or names none.

    The code-context signal is the **strongest** one the site has
    against the sites on the other side of the same store: several
    counterparts in the same module are one observation of the layout,
    and counting them one by one would raise the confidence on the
    number of call sites instead of on what the layout says. A store
    with no counterpart yet (a write whose store nothing reads) keeps
    the literal signal alone, which is the honest reading — the literal
    is what named the store.

    `literal_match` is asked about the *store's* identity, not about a
    counterpart: the store exists because a literal named it (§1), so
    "both sides have a literal and they are equal" is exactly "this
    site's literal is the store's", and "one side has a literal" is an
    unresolved site pointing at a store it did not name.

    Its cases, in the order it tests them, are the whole of whether this
    link exists at all: equal hints give `LITERAL_EQUAL` (+0.6); a site
    with **no** hint gives `LITERAL_ONE_SIDE` (+0.2) — `key` is never
    `None`, since a store exists only where a literal named one, so this
    branch is the unresolved site and not a symmetric one; a hint
    *containing* the other gives `LITERAL_PREFIX` (+0.3, B.3b-fs's row:
    `startswith` in both directions, nothing normalised); and two hints
    that differ and contain neither way give `None` — no link, and the
    pair is dropped. The function's other `None` (neither side has a
    hint) cannot arise here, for the same reason `key` cannot.
    """
    literal = literal_match(site.identity_hint, key)
    if literal is None:
        return None

    context = 0.0
    for other in counterparts:
        if key not in _attached_keys(other, keys):
            continue
        signal = (
            code_context(site, other)
            if write_side
            else code_context(other, site)
        )
        context = max(context, signal)

    signals = [literal]
    if context > 0.0:
        signals.append(context)
    return combine_signals(site.confidence_base, signals)


def _keep_edge(edges: dict[str, Edge], edge: Edge) -> None:
    """Keep an edge, or the stronger version of one already kept.

    One `writes_to` per (writer, store) and one `reads_from` per
    (store, source): two call sites inside one component are one link,
    and the edge keeps the strongest pairing that supports it.
    `identity_confidence` is a per-*link* number while a pair is what
    the query joins on; on the 1:1 pairing the threshold is tuned for,
    `min(w, r)` over a group is exactly that pair's own confidence
    (§7). With several sites on one side it is the strongest pairing
    the link has from each end, which is the most a per-link field can
    say about a pairing the field does not record.

    Insertion order is kept on replacement, so the edge list is a
    function of the sites and not of their confidence.
    """
    existing = edges.get(edge.id)
    if existing is None:
        edges[edge.id] = edge
        return
    old = existing.identity_confidence or 0.0
    new = edge.identity_confidence or 0.0
    if new > old:
        edges[edge.id] = edge


# ------------------------------------------------------------------
# Nodes and their lookup indexes
# ------------------------------------------------------------------

def _store_node(family: str, identity_hint: str, host_id: str) -> Node:
    """The `store` node for one `(family, identity_hint)` (§1).

    `name` is `<family>:<identity_hint>` — the identity rendered for a
    human, the way an `untrusted_source` is named
    `<source_type>:<call_target>`. The attributes carry the two halves
    of that identity and nothing else: the store's *role* is the read
    site's `source_type` and stays on the source (§10), and how a report
    should show a store is an open question (§12), so the node holds no
    view of its own.

    `confidence` keeps its default. §1 settles it — "an entity is either
    in the graph or not" — so existence is not probabilistic; the
    probability belongs to the links that point here, and it is on them.
    """
    return Node(
        id=store_node_id(family, identity_hint),
        kind="store",
        name=f"{family}:{identity_hint}",
        host_id=host_id,
        attributes={"family": family, "identity_hint": identity_hint},
        content_hash=None,
    )


def _component_index(delta: "GraphDelta") -> dict[tuple[str, str], str]:
    """`(source_file, name)` → node id, for every node in the delta.

    Two names map to one node: the node's own `name`, and its `handler`
    attribute when it has one — an entry point is named `"POST /upload"`
    while the call site lives in the function, and that function's name
    is the handler. First write wins (`setdefault`), so the delta's
    stage order breaks a tie between two nodes of one name: the earlier
    and more specific stage keeps it.

    The rule is `untrusted_sources._reader_index`'s, restated rather
    than imported: the store subsystem must not reach into the parsing
    stage package (the stage package imports this one), and this is
    index building, not logic that drifts.
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


def _source_index(
    delta: "GraphDelta",
) -> tuple[dict[tuple[str, int], str], dict[tuple[str, str], list[str]]]:
    """The `untrusted_source` lookup tables: by line, and by type.

    Both are keyed by the node's own `source_file` and come from the
    read site the source node was built for (1.3a): `line_start` is the
    read call's line, and `source_type` is the role the read carries.
    Only `untrusted_source` nodes are indexed — a store reads from a
    source, and a read site that belongs to no source has nothing to
    link to.
    """
    by_line: dict[tuple[str, int], str] = {}
    by_type: dict[tuple[str, str], list[str]] = {}
    for node in delta.nodes:
        if node.kind != "untrusted_source":
            continue
        file_path = node.attributes.get("source_file")
        if not isinstance(file_path, str):
            continue
        line_start = node.attributes.get("line_start")
        if isinstance(line_start, int):
            by_line.setdefault((file_path, line_start), node.id)
        source_type = node.attributes.get("source_type")
        if isinstance(source_type, str):
            by_type.setdefault((file_path, source_type), []).append(node.id)
    return by_line, by_type


def _read_target_id(
    site: ReadSite,
    by_line: dict[tuple[str, int], str],
    by_type: dict[tuple[str, str], list[str]],
) -> str | None:
    """The `untrusted_source` a read site reads from, or None.

    The call site itself first: a source node is one read site (1.3a),
    so the same file and the same line is the read the family saw.
    When the line does not match, the read is attributed to the source
    of the site's `source_type` in that file — and only when there is
    exactly one. With two sources of one type in a module the file
    cannot say which one this read points at, and §5's rule is that an
    identity the AST cannot resolve stays unresolved instead of being
    guessed. A site with no `source_type` has no second chance: it
    claims no source, so it gets none.
    """
    target = by_line.get((site.file_path, site.line))
    if target is not None:
        return target
    if site.source_type is None:
        return None
    candidates = by_type.get((site.file_path, site.source_type), [])
    if len(candidates) == 1:
        return candidates[0]
    return None


def _writer_node_id(
    site: WriteSite,
    ctx: "NormalizeContext",
    components: dict[tuple[str, str], str],
) -> str | None:
    """The node a write call site belongs to, or None.

    The writer is the component whose body makes the call: the innermost
    function the site's line falls in, else the class that encloses it —
    `untrusted_sources` resolves a reader in that order, and for the
    same reason (a method that was never classified has no node, but the
    class that exercises the capability does).

    No node, no edge: a component the graph does not know cannot be the
    first hop of a write chain, and inventing one here would put a
    component in the graph that no other stage has heard of.
    """
    names = _enclosing_names(
        ctx.functions_for(site.file_path),
        ctx.classes_for(site.file_path),
        site.line,
    )
    for name in names:
        node_id = components.get((site.file_path, name))
        if node_id is not None:
            return node_id
    return None


def _enclosing_names(functions: dict, classes: dict, line: int) -> list[str]:
    """Names of the definitions whose body contains `line`, innermost first.

    Functions before classes, and each innermost before outermost, so
    the most specific node the delta holds for the call site wins. A
    definition whose end is unknown (a hand-built index entry) is
    treated as one line long, which is the conservative reading: it can
    only match the line it starts on.
    """
    names = [definition.name for definition in _spanning(functions, line)]
    names += [definition.name for definition in _spanning(classes, line)]

    ordered: list[str] = []
    for name in names:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _spanning(index: dict, line: int) -> list:
    """Definitions whose body spans `line`, innermost (latest start) first."""
    hits = [
        definition
        for definition in index.values()
        if isinstance(getattr(definition, "lineno", None), int)
        and definition.lineno <= line
        <= (getattr(definition, "end_lineno", None) or definition.lineno)
    ]
    return sorted(hits, key=lambda definition: definition.lineno, reverse=True)


def _path_by_dotted(manifest: object) -> dict[str, str]:
    """`dotted module name` → manifest path, for the local imports (§8).

    `app/storage.py` is `app.storage`; a package's `__init__.py` is the
    package itself, because `from app import storage` names the package
    and the module inside it. The map is what makes the code-context
    signal mean the *same file* in two files that import it, and it is
    built once per scan from the manifest.
    """
    paths: dict[str, str] = {}
    for entry in getattr(manifest, "files", []):
        path = entry.path
        if not path.endswith(".py"):
            continue
        dotted = path[:-3].replace("/", ".")
        if dotted.endswith(".__init__"):
            dotted = dotted[: -len(".__init__")]
        paths.setdefault(dotted, path)
    return paths


__all__ = [
    "STRUCTURAL",
    "StoreResolver",
    "store_node_id",
]
