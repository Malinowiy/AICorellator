"""Writer logic — from an upload handler to the stores it writes into.

ADR-0021 §8 (revision 2) makes an entry-point handler that *accepts* an
upload a **context-based writer**: for every store it writes into, the
`identity_confidence` base is 0.5 instead of 0.3, because the context —
this handler takes an upload — is evidence the family-only rules do not
have. The chain this module walks:

    UploadSite (entry_point, file, line)
      → the entry point node, which is one of two shapes:
          - `entry_kind` `"cli"` or `"streamlit"` → a **module-level**
            entry point: the module's top-level functions, BFS from each
            of them, and the writes directly in the module body
          - a `handler`, a function name → BFS from it over the call graph
      → the `fs` write sites in that reachable set
      → the `store` node each write names (family `fs` + its literal)
      → writes_to: entry_point → store, at the signals' confidence

The module-level branch is B.3b-user_upload-core-module's, and it is the
answer to "which writes does an upload at module level reach": the
module's own writes — its top-level functions and its body — and not
every sink in the target (`entry_to_sink`'s CLI fallback, deliberately
**not** copied: an upload writer's scope is its module). Its selector is
`entry_kind`, and not the `entry_points` stage's `<module>` sentinel
(B.3b-user_upload-core-module-followup): the sentinel is on the
`EntryPoint` **record**, and the **node** a site carries has no `handler`
at all — `EntryPointStage` writes that attribute for HTTP and WebSocket
entries only — so a branch keyed on the sentinel could not fire on a real
scan. `entry_kind`, which the stage writes for every entry it builds, is
the structural marker, and `handler` stays function-only, so
`ctx.entry_node_by_handler` is not polluted with a sentinel either.

The signals, all of them §8's:

  - **base 0.5** — the context signal, and the whole of the revision:
    the write is inside a function the entry point reaches, and a
    function is a discrete unit. A write in a **module-level** entry
    point's own body is weaker evidence — the module is a file — and
    carries `UPLOAD_WRITER_BASE_MODULE` (0.4) instead;
  - **literal match** — `confidence.literal_match` against the store's
    own literal. The store lookup is exact equality, so this is
    `LITERAL_EQUAL` (0.6) whenever a store is named at all;
  - **code context** — `CODE_CONTEXT_SAME_MODULE` (0.2) when the write
    lives in the entry point's own file. The other two rows of
    `confidence.code_context` compare two *read* sites' import facts, and
    the other side of this link is an entry point, so there is nothing
    here for them to compare (see "What this module does not do");
  - **context in body** (0.2) — the write is directly in the handler's
    body and not reached through a helper. The AST position is the
    check, and the *innermost* enclosing function is what decides it: a
    nested function inside the handler is inside the handler's line
    range but the write is the nested function's, so the signal does not
    fire for it and the handler merely calls it. **Never fires for a
    module-level entry point**: its body is the module, and the writes
    this branch claims are either in the module body (which is the 0.4
    base, not this signal) or in a top-level function (which is not the
    entry point's own body).

So a write in the handler's body, in the handler's own file, is
`1 − 0.5 × 0.4 × 0.8 × 0.8 = 0.872`; the same write reached through a
helper in the same file is `1 − 0.5 × 0.4 × 0.8 = 0.84`; through a
helper in another file `1 − 0.5 × 0.4 = 0.8`. A write the handler does is
more certainly the handler's write than a write a helper does and the
handler merely calls — which is what the 0.2 buys. `test_writer.py` pins
all three.

A module-level entry point's writes are the other two numbers, and they
are the same shape: a write in the module body is `1 − 0.6 × 0.4 × 0.8 =
0.808` (base 0.4, no context-in-body) and a write in one of the module's
top-level functions is `1 − 0.5 × 0.4 × 0.8 = 0.84` (base 0.5, no
context-in-body either). A function is a stronger signal than a module
body; `tests/test_store_upload_core_module/` pins both from the
constants.

**The rule lives here and only here.** The writer is called by the stage
(`normalizer_stages.store_resolver`), after the resolver's own edges
exist; the resolver is not involved, because its job is pairing a write
site with a read site and this is a claim about one side.

What this module does not do, and why:

  - **A write with no enclosing function is not a function-level entry
    point's write.** The handler does not run the module body, so a
    module-level `open(...)` is not the handler's write even when it
    shares the file. A **module-level** entry point is the other side of
    that rule and the reason the branch exists: the module body *is* its
    body, so the write is claimed — at the weaker 0.4 base.
  - **A module body write in another file is not the module-level entry
    point's.** The branch claims the module's **own** file: its top-level
    functions (through the call graph, which is scan-wide) and its own
    body. A module body elsewhere runs at that file's import, which this
    writer has no import graph to establish, so claiming it would be the
    CLI fallback's breadth by another route. A stated false negative.
  - **A module the caller did not parse yields no edge.** The
    module-level branch reads the module's top-level functions and its
    body from the AST, and a module that is not in `modules` is skipped
    rather than guessed — the same rule as the enclosing-function check
    below, and a false negative.
  - **`self.`-calls are not in the call graph** (ADR-0016: only
    `ast.Name` calls are recorded), so a method handler that calls
    `self._save()` does not reach the write `_save` makes. A false
    negative this step accepts, the same one `entry_to_sink` has.
  - **The call graph is name-keyed and scan-wide.** A same-named
    function elsewhere in the target can make a write reachable, which is
    a false positive, accepted for the same reason (`entry_to_sink`,
    ADR-0009).
  - **The store lookup is exact equality**, so the prefix row of
    `literal_match` is not reachable from here — the same observation
    `store.confidence` makes about the resolver.
  - **No modules, no edge.** The enclosing function of a write site is
    read from the file's AST, so a caller that passes no `modules` gets
    nothing back rather than a guess.

The `entry_points` map the brief sketched is not a parameter: a site
carries its own entry-point node (`UploadSite.entry_point`), which is
both the edge's source and the `handler` the BFS starts from, so a
handler → node map would be a second way to say one thing.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Callable, Iterable, Mapping

from ...confidence import (
    CODE_CONTEXT_SAME_MODULE,
    combine_signals,
    literal_match,
)
from ...families.base import WriteSite
from ....graph.models import Edge, Node
from ...resolver import STRUCTURAL
from .base import UploadSite

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....parsing.ast_normalizer import GraphDelta


#: §8's base for a **context-based** writer (revision 2): the handler
#: accepts an upload, and that is a signal on top of the call shape. A
#: family-only writer keeps `confidence.BASE` (0.3).
UPLOAD_WRITER_BASE = 0.5

#: The base for a write in a **module-level** entry point's own body
#: (B.3b-user_upload-core-module). Lower than `UPLOAD_WRITER_BASE`
#: because the claim is weaker: a function is a discrete unit that runs
#: when it is called, and a module body is a file that runs when it is
#: imported — the same write, one step further from "the upload handler
#: does this". A write inside one of the module's top-level functions
#: keeps `UPLOAD_WRITER_BASE`, because that write *is* in a function.
UPLOAD_WRITER_BASE_MODULE = 0.4

#: The context-in-body signal: the write is directly in the handler's
#: body, not reached through a helper. Separate from §8's code-context
#: rows, and added on top of them. Never fires for a module-level entry
#: point (`_confidence`).
CONTEXT_IN_BODY = 0.2

#: The family whose write sites the writer looks at. §8's first context
#: writer is an upload into a filesystem store, and the store lookup is
#: by `(family, literal)`, so the family is named once, here.
FS_FAMILY = "fs"

#: `entry_points.EntryPointStage`'s `entry_kind`s for a module-level entry
#: (CLI, Streamlit) — the closed set `_module_level` selects on
#: (B.3b-user_upload-core-module-followup). Closed, and a `frozenset` for
#: that reason: a third module-level entry kind is a separate step with
#: its own test, not a member added here. The stage writes `entry_kind`
#: for every node it builds — `"cli"` for a CLI entry and `"streamlit"`
#: for a Streamlit entry, and **no** `handler` for either — and an MCP
#: entry carries `entry_kind="mcp"`, which is a framework-detected server
#: and not an upload at module level.
MODULE_LEVEL_ENTRY_KINDS = frozenset({"cli", "streamlit"})

#: §9's default threshold (`graph.query.DEFAULT_STORE_CONFIDENCE_THRESHOLD`,
#: `config.yaml`'s `store_confidence_threshold`), restated rather than
#: imported: it is the writer's floor, and this package does not reach
#: into the query layer for one number.
DEFAULT_CONFIDENCE_THRESHOLD = 0.7


def find_upload_stores(
    *,
    delta: "GraphDelta",
    upload_sites: list[UploadSite],
    call_graph: Mapping[str, set[str]],
    fs_writes: list[WriteSite],
    store_nodes: Iterable[Node],
    modules: Mapping[str, ast.Module] | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> list[Edge]:
    """The `writes_to` edges an upload handler earns, in site order.

    One edge per `(entry point, store)`: two handlers that both accept an
    upload and write into `/uploads/` are two edges into one store, and
    one handler that writes into two stores is two edges — the second one
    possibly a false positive (a temp file written next to the upload),
    which the reader-in-scope rule is what decides the fate of
    (`attribution`).

    A site whose entry point carries a function-name `handler` reaches
    that function's writes; a site whose entry point is module-level
    (`_module_level`: `entry_kind` `"cli"` or `"streamlit"`) reaches the
    module's **own** writes — its top-level functions, and its body
    (`_reach`, `_is_module_body`). Nothing else: the CLI fallback's
    "every sink in the target" is not this rule.

    `delta` is read for its node ids and written by nobody: the edges
    come back, the way `StoreResolver.resolve` returns its own.
    `fs_writes` is the `fs` family's report and `store_nodes` the store
    nodes the resolver built into the delta; the caller (the stage) has
    both. `modules` is read for two things per site: the enclosing
    function of a write (either branch) and the module-level branch's
    top-level functions.

    `confidence_threshold` is a floor. §9 puts the threshold on the
    *join* (`find_chain_groups`) and the resolver states the same, so
    this is the one place a writer could drop a link the graph would
    otherwise hold; it never fires with the rule as it stands, because
    every writer edge carries an equal literal and the floor is 0.8. It
    is kept because it is where a weaker signal — a detector handing a
    prefix literal, a code-context-only write — would be filtered.
    """
    edges: dict[str, Edge] = {}
    node_ids = {node.id for node in delta.nodes}
    stores = _store_index(store_nodes)

    for site in upload_sites:
        entry_id = site.entry_point.id
        if entry_id not in node_ids:
            continue
        reach = _reach(site, call_graph, modules)
        if reach is None:
            continue
        reachable, module_level = reach
        for write in fs_writes:
            store = _named_store(write, stores)
            if store is None:
                continue
            chain = _enclosing_functions(
                _module_for(modules, write.file_path), write.line
            )
            module_body = _is_module_body(site, write, chain, module_level)
            if not module_body and not any(
                name in reachable for name in chain
            ):
                continue
            confidence = _confidence(
                site, write, store, chain, module_level, module_body,
            )
            if confidence is None or confidence < confidence_threshold:
                continue
            _keep_edge(edges, _writes_to_edge(entry_id, store, confidence))

    return list(edges.values())


def _reach(
    site: UploadSite,
    call_graph: Mapping[str, set[str]],
    modules: Mapping[str, ast.Module] | None,
) -> tuple[set[str], bool] | None:
    """`(the names the site reaches, is it module-level)`, or None.

    A function-level site reaches its handler and everything the call
    graph puts under it. A module-level site (`_module_level`) reaches its
    module's top-level functions and everything under **them**; its own
    body is not a name and is handled per write (`_is_module_body`).

    `None` means the site earns no edge at all: no `handler` attribute to
    start from, or a module-level site whose own module the caller did
    not parse — the branch is skipped rather than guessed, the same
    answer the enclosing-function check gives for a write it cannot
    place. The reachable set is a set of names and only membership is
    read from it, so the writer's output does not depend on its
    iteration order.
    """
    if _module_level(site.entry_point):
        module = _module_for(modules, site.file_path)
        if module is None:
            return None
        reachable: set[str] = set()
        for name in _top_level_functions(module):
            reachable |= _reachable(name, call_graph)
        return reachable, True

    handler = _handler_name(site)
    if handler is None:
        return None
    return _reachable(handler, call_graph), False


def _is_module_body(
    site: UploadSite,
    write: WriteSite,
    chain: list[str],
    module_level: bool,
) -> bool:
    """True if the write sits in a module-level entry point's own body.

    Three things, and all three are needed: the site is module-level, the
    write is inside no function (`chain` empty — the brief's "does the
    line fall inside a top-level function's range" read off the AST
    instead of computed), and the write is in the entry point's **own**
    file, because the module body that runs when the entry point's module
    is imported is that file's. A module body in another file is another
    module's, and this writer has no import graph to relate the two.

    A write in a class body — inside no function, inside a class — counts
    as module body, which is the brief's rule as written: the test is the
    function line ranges, and a class body is outside all of them.
    """
    return module_level and not chain and write.file_path == site.file_path


# ------------------------------------------------------------------
# One site, one write
# ------------------------------------------------------------------

def _handler_name(site: UploadSite) -> str | None:
    """The function the BFS starts from, or None.

    The entry-point node's `handler` attribute names the function the
    route decorator sits on — the same shape `entry_to_sink` reads. A
    module-level entry has no `handler` at all (`_module_level`), and a
    hand-built node may have none either; neither is a function name, and
    neither starts a BFS.
    """
    handler = site.entry_point.attributes.get("handler")
    if not isinstance(handler, str) or not handler:
        return None
    return handler


def _module_level(entry_point: Node) -> bool:
    """True for an entry point whose upload lives at module level.

    The marker is `entry_kind`, and the set is
    `MODULE_LEVEL_ENTRY_KINDS` — the two kinds
    `entry_points.EntryPointStage` gives a module-level entry
    (B.3b-user_upload-core-module-followup). The kind is read exactly,
    not as a prefix or a case, and the set is closed: a third `entry_kind`
    is a different entry kind and a separate step with its own test, not a
    member added here.

    Not the `entry_points` stage's `<module>` sentinel, which is what
    this branch read before: the sentinel is on the `EntryPoint`
    **record**, and the **node** the stage builds for a CLI or a
    Streamlit entry carries no `handler` at all, so the sentinel never
    appeared here and the branch could not fire on a real scan.
    `entry_kind` is on every node the stage builds. HTTP and WebSocket
    entries carry a `handler` and no `entry_kind`, and an MCP entry
    carries `entry_kind="mcp"` — a framework-detected server, not an
    upload at module level — so neither is module-level, and a node with
    neither attribute (a hand-built delta) is not either.
    """
    return entry_point.attributes.get("entry_kind") in MODULE_LEVEL_ENTRY_KINDS


def _top_level_functions(module: ast.Module) -> list[str]:
    """The names of a module's top-level functions, in source order.

    Top level means `module.body`, so a nested function is not one of
    them: it is reached through its enclosing function's name, which is
    how the call graph records the call (`_reachable`). A class body is
    not a top-level function either, so a method is not a BFS start —
    `self.`-calls are not in the call graph and a module-level entry point
    has no `self` to call it through.
    """
    return [
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _named_store(
    write: WriteSite, stores: Mapping[tuple[str, str], Node]
) -> Node | None:
    """The store a write site names, or None.

    The site's own literal is the identity: a store node exists because a
    literal named it (§1), so "the write's hint equals the store's hint"
    is the whole lookup. An unresolved site (`identity_hint is None`)
    names no store and gets no edge — §5's rule, and the reason a family
    reports `None` instead of guessing.
    """
    hint = write.identity_hint
    if hint is None:
        return None
    return stores.get((FS_FAMILY, hint))


def _confidence(
    site: UploadSite,
    write: WriteSite,
    store: Node,
    chain: list[str],
    module_level: bool,
    module_body: bool,
) -> float | None:
    """`identity_confidence` for one (entry point, store) link (§8).

    The base is `UPLOAD_WRITER_BASE`, or `UPLOAD_WRITER_BASE_MODULE` for
    a write in a module-level entry point's own body, and never the
    site's own `confidence_base`: the family reports a call site at 0.3,
    and the context is what raises this link to a context-based writer's
    numbers.

    The context-in-body signal is the handler's own body, so it needs a
    handler: a module-level site has none (`module_level`), and the two
    placements this branch claims are the module body (which the base
    already accounts for) and a top-level function (which is not the
    entry point's body — the entry point's body is the module). It
    therefore never fires here, and `test_writer_module.py` pins both of
    the numbers that follow from that.

    `None` means no link, which `literal_match` decides: two different
    literals name two stores, and a store this write did not name is not
    this write's (`resolver._attached_keys`' rule, reached from the other
    side).
    """
    literal = literal_match(
        write.identity_hint, store.attributes.get("identity_hint")
    )
    if literal is None:
        return None

    base = UPLOAD_WRITER_BASE_MODULE if module_body else UPLOAD_WRITER_BASE
    signals = [literal]
    if write.file_path == site.file_path:
        signals.append(CODE_CONTEXT_SAME_MODULE)
    if not module_level and chain and chain[0] == _handler_name(site):
        signals.append(CONTEXT_IN_BODY)
    return combine_signals(base, signals)


def _writes_to_edge(entry_id: str, store: Node, confidence: float) -> Edge:
    """`entry_point → store`, structural, `observed_by=["ast"]` (§3–§4).

    The same discipline the resolver applies to its own edges: a
    declaration and not a path, no data-flow claim (`data_flow_confirmed`
    stays `None`, the `Edge` default), and the confidence on
    `identity_confidence` because the question is whether the two call
    sites name one place.
    """
    host_id, edge_id = _graph_ids()
    return Edge(
        id=edge_id(entry_id, store.id, "writes_to"),
        source_id=entry_id,
        target_id=store.id,
        kind="writes_to",
        host_id=host_id,
        flow_kind=STRUCTURAL,
        identity_confidence=confidence,
    )


def _keep_edge(edges: dict[str, Edge], edge: Edge) -> None:
    """Keep an edge, or the stronger version of one already kept.

    One `writes_to` per (entry point, store): two call sites inside one
    handler are one link, and the edge keeps the strongest pairing that
    supports it. `StoreResolver._keep_edge`'s rule, restated — it is
    private to the resolver and this is a different edge set — and the
    insertion order is kept on replacement, so the edge list is a
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
# Reading the AST and the graph
# ------------------------------------------------------------------

def _module_for(
    modules: Mapping[str, ast.Module] | None, file_path: str
) -> ast.Module | None:
    """One file's parsed module, or None when the caller passed none."""
    if modules is None:
        return None
    return modules.get(file_path)


def _enclosing_functions(module: ast.Module | None, line: int) -> list[str]:
    """Names of the functions whose body contains `line`, innermost first.

    `resolver._enclosing_names`' rule for one side of it — functions only,
    innermost (latest start) first — because the question here is which
    function *makes* the write. A definition whose end is unknown is
    treated as one line long, the conservative reading: it can only match
    the line it starts on.

    The innermost name is what the context-in-body signal tests: a nested
    function inside the handler is not the handler's own body.
    """
    if module is None:
        return []
    hits = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= line <= (node.end_lineno or node.lineno)
    ]
    ordered: list[str] = []
    for node in sorted(hits, key=lambda node: node.lineno, reverse=True):
        if node.name not in ordered:
            ordered.append(node.name)
    return ordered


def _reachable(start: str, call_graph: Mapping[str, set[str]]) -> set[str]:
    """BFS from `start` through the call graph, including `start` itself.

    `entry_to_sink.reachable_functions`' rule, restated rather than
    imported: the store subsystem does not reach into the parsing stage
    package (the stage package imports this one), and this is a
    name-keyed walk, not logic that drifts. Only `ast.Name` calls are in
    the graph (ADR-0016), so `self.`-calls are not followed.
    """
    visited: set[str] = set()
    queue: list[str] = [start]
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        for name in call_graph.get(current, ()):
            if name not in visited:
                queue.append(name)
    return visited


def _store_index(store_nodes: Iterable[Node]) -> dict[tuple[str, str], Node]:
    """`(family, identity_hint)` → the store node, for store nodes only.

    The same key the resolver builds a store from, read back: a store is
    one node per `(family, identity_hint)` (§1), so two stores sharing a
    key is impossible by construction and `setdefault` is the honest
    tie-break for a hand-built delta. A node missing either attribute is
    not a store this writer can use.
    """
    index: dict[tuple[str, str], Node] = {}
    for node in store_nodes:
        if node.kind != "store":
            continue
        family = node.attributes.get("family")
        hint = node.attributes.get("identity_hint")
        if not isinstance(family, str) or not isinstance(hint, str):
            continue
        index.setdefault((family, hint), node)
    return index


def _graph_ids() -> tuple[str, Callable[[str, str, str], str]]:
    """`(host_id, edge_id)` — the pipeline's id convention.

    Imported here and not at module level, on purpose:
    `parsing.normalizer_stages.store_resolver` imports this module, so a
    module-level import of the helpers would close a cycle for a caller
    that imports `aicorellator.store` first. `resolver._graph_ids` is the
    same accessor for the same reason; this is the writer's copy of the
    two names it needs.
    """
    from ....parsing.normalizer_helpers import HOST_ID, edge_id

    return HOST_ID, edge_id


__all__ = [
    "CONTEXT_IN_BODY",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "FS_FAMILY",
    "MODULE_LEVEL_ENTRY_KINDS",
    "UPLOAD_WRITER_BASE",
    "UPLOAD_WRITER_BASE_MODULE",
    "find_upload_stores",
]

