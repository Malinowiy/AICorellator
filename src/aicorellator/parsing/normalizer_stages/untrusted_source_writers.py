"""UntrustedSourceWriterStage — `writes_to` into a source (1.3a.1).

Run order: 11 of 20 (see `normalizer_stages.STAGES`).

C4: no pre-1.3 row. This is the write half of ADR-0010's channel. 1.3a
built the read side — a source node per read site, plus
`source → reader` — and a chain group is a *pair*: paths that write
into a source and paths that read from it. This stage emits the first
hop of the write side, so `find_chain_groups` (1.3b) has an incoming
edge to walk.

**Position is load-bearing**, and it is why this is not part of
`untrusted_sources` (stage 6):

  - The `mcp_server` nodes it matches against arrive in two waves.
    Path-heuristic servers (`mcp/servers/<name>/`) are stage 1, and a
    `*Server` class under `mcp/servers/` is classified by `class_nodes`,
    stage 2 — both before this stage; framework-detected ones
    (`FastMCP(...)`, `Server(...)`) arrive with the detector merge,
    stage 10. A loop inside `untrusted_sources` would see only the first
    wave — measured on the four corpora: 5 of 14 server nodes are
    path-heuristic, and the two corpora that contribute the other 9
    (dvmcp, mcploitable) are exactly the ones whose server nodes carry
    the file this rule matches on.
  - It must run before `flow_kind` (stage 12), so the edge it emits
    reaches `FlowKindStage` without a kind of its own and the kind is
    pinned in exactly one place (ADR-0016, decision 3).

**Two writers, two mechanisms** (B.2). A source family is written in
one of two ways, and which one it is decides how this stage finds the
writer:

  - **A declaration** — `mcp_tool_description`. The MCP server declares
    its tool catalogue (`@mcp.tool()`) and the client reads it back
    (`list_tools`), so the write edge is `mcp_server →
    untrusted_source` and the writer is a `mcp_server` node (1.3a.1).
  - **A call** — `rag_corpus` and `email_inbox` (B.2). A corpus and a
    mailbox are declared by nobody: they are written by a component
    that calls the store (`vectorstore.add_documents`,
    `smtp.sendmail`). The call is credited by a `CapabilityRule`
    (`patterns.WRITER_RULES`) exactly the way a read is credited by
    `READS_UNTRUSTED_*` (ADR-0009), and the writer is the node the
    delta already holds for the symbol that credited it.

The second mechanism is why `WRITER_RULES` exists and why it has no
`mcp_tool_description` entry: a rule for that family would be a call
pattern for something no call site spells. `user_upload` (ADR-0010's
fourth family) stays out of both — it is a structural signal rather
than a call shape, and B.3 owns it.

**The writer of a call-written family is a component, not the store.**
`_component_writers_for` reads the crediting symbols from
`ctx.capabilities`, resolves each to the most specific node the delta
holds for it (the symbol itself, else the class that encloses it), and
keeps the ones the source is visible from. A write site whose symbol
was never classified has no node, and therefore no edge: a component
that is not in the graph cannot be the first hop of a write chain
either, and inventing a node for it here would put a component in the
graph that no other stage knows about.

**Anchoring** is 1.3a's: the source node is one *read site*, so a writer
cannot name it by what it writes. It can only be matched to a source
that is visible from it — `_writers_for` states the four ways that
happens for a server-declared family, and `_component_writers_for`
states the two ways for a call-written one:

  - **the same file** — the write and the read are in one module, which
    is the shape an ingest route and its retriever have;
  - **a shared import path** — the file that writes imports the file
    that reads, or the other way round, matched on trailing path
    segments exactly as `cross_file._module_matches_file` matches a
    module name to a file (B.1's rule, reused rather than
    re-implemented). The two halves of ADR-0010's channel need not
    share a file: a service module writes the corpus and the route
    module reads it, and both name the same store module.

Both are evidence about *visibility*, not about identity: the two
anchors say "this write and this read are in the same module or in
modules that import each other", which is why they over-match by
construction (ADR-0009).

The server-declared family's fourth anchor is the literal module path: a
file that spells out
`...mcp.servers.<name>.server.create_<name>_server` is naming a server
it talks to, so that server is a writer of a catalogue the file reads
even when the server's own module is elsewhere. That is FinBot's
`_SERVER_INTROSPECTORS` shape, it is a rule about a server declaration,
and it does not apply to a call-written family.

1.3a.1.2 read that literal in the source's own file. 1.3a.1.3 reads it
**project-wide**, because the two halves of the channel need not share a
file: FinBot's two route files carry the literal and read the catalogue
through a helper that has no node, while `mcp/provider.py` carries the
reader (`MCPToolProvider`) and names no server at all. The evidence is
the same literal; only its scope changes, and the scope is the project.
That is deliberately wide — a server named in one file writes a
catalogue read in another — so it over-matches by construction.
`max_writer_edges` bounds the `servers × sources` product, and ADR-0012's
prioritization (1.3c) is what prunes a wide group set.

Side effects: none. The stage reads the delta only: it emits edges
between nodes other stages created, and creates none.
"""

from __future__ import annotations

import ast
import logging
import re
from typing import TYPE_CHECKING

from ...graph.models import Edge, Node
from ...patterns import WRITER_RULES
from ..framework_detectors.cross_file import _module_matches_file
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import edge_id, server_name_for_file

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


log = logging.getLogger(__name__)


# Bound on the edges this stage emits per scan. The literal anchor is
# project-wide (1.3a.1.3), so the worst case is a Cartesian product of
# the servers a literal names and the catalogue sources the scan found.
# The bound is on *emission*, not on the graph: past it the stage stops
# and says so at DEBUG, which is the visible signal that a target's
# project scope is wider than the rule expects.
MAX_WRITER_EDGES: int = 1000


# The one family whose writer is a declaration (1.3a.1): the server
# declares the catalogue, so the writer is a `mcp_server` node and the
# anchors are `_writers_for`'s. It is named here rather than derived,
# because the family is the thing the server anchors are about.
SERVER_DECLARED_SOURCE_TYPE: str = "mcp_tool_description"


# The source families this stage writes edges for. The
# declaration-written family plus every family `patterns.WRITER_RULES`
# has a call rule for (B.2), derived rather than listed: a family with a
# writer rule is a family this stage must match, and one without a rule
# is a family nothing has evidence for (`user_upload` — B.3).
WRITER_SOURCE_TYPES: frozenset[str] = frozenset(
    {SERVER_DECLARED_SOURCE_TYPE}
    | {source_type for source_type, _ in WRITER_RULES}
)


# A literal that names a server's factory: the module path of the
# `<name>` server and the `create_<name>_server` factory inside it. The
# spelling is the dotted module path (`finbot.mcp.servers.finmail.server.
# create_finmail_server`); the name is read from the path, never from a
# dict key, so a plain string literal is the same evidence.
_SERVER_FACTORY_PATH = re.compile(
    r"^(?:[\w.]+\.)?mcp\.servers\.(?P<name>[a-z_][a-z0-9_]*)\."
    r"server\.create_(?P<factory>[a-z_][a-z0-9_]*)_server$"
)


class UntrustedSourceWriterStage:
    """`writer → writes_to → untrusted_source`, one edge per pair.

    Two ways a writer is found, one per family mechanism: an
    `mcp_server` for the declaration-written catalogue (1.3a.1), and the
    node of the component that calls the store for the call-written
    corpus and mailbox (B.2).
    """

    name = "untrusted_source_writers"

    def __init__(self, max_writer_edges: int = MAX_WRITER_EDGES) -> None:
        """`max_writer_edges` bounds the edges this stage emits per scan.

        The constant is the default; the argument is the test seam.
        """
        self.max_writer_edges = max_writer_edges

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        sources = [
            node for node in delta.nodes
            if node.kind == "untrusted_source"
            and node.attributes.get("source_type") in WRITER_SOURCE_TYPES
        ]
        if not sources:
            return delta

        # --- The server-declared family's anchors (1.3a.1) ---
        # Built only when the delta holds a catalogue source: the
        # project-wide literal pass below is O(files), and a scan of a
        # target with no MCP client has nothing for it to answer.
        servers = [n for n in delta.nodes if n.kind == "mcp_server"]
        by_file, by_name = _server_indexes(servers)
        declared: list[Node] = []
        if servers and any(
            _source_type(node) == SERVER_DECLARED_SOURCE_TYPE
            for node in sources
        ):
            # One project pass, not one pass per source (1.3a.1.3): the
            # literal is evidence about the project, and a source's own
            # file need not carry it. The pass is O(files), and
            # `ctx.module_for` keeps each file parsed at most once.
            manifest_paths = [entry.path for entry in ctx.manifest.files]
            declared = [
                by_name[name]
                for name in _project_declared_servers(
                    ctx, by_name, manifest_paths
                )
            ]

        # --- The call-written families' anchors (B.2) ---
        # `source_type → rule`, from the one table that pairs them; the
        # rule's capability is what the extractor credited the writing
        # symbol with. The node index is built once and only when a
        # call-written source is in the delta.
        component_rules = dict(WRITER_RULES)
        writer_index: dict[tuple[str, str], Node] = {}
        if any(_source_type(node) in component_rules for node in sources):
            writer_index = _writer_index(delta)

        emitted: set[str] = set()
        for source in sources:
            rule = component_rules.get(_source_type(source))
            if rule is None:
                writers = _writers_for(source, by_file, by_name, declared)
            else:
                writers = _component_writers_for(
                    source, rule.capability, ctx, writer_index
                )
            for writer in writers:
                edge = _write_edge(writer, source, ctx)
                if edge.id in emitted:
                    continue
                if len(emitted) >= self.max_writer_edges:
                    log.debug(
                        "untrusted_source_writers: max_writer_edges (%d) "
                        "reached; the remaining pairs are not emitted",
                        self.max_writer_edges,
                    )
                    return delta
                emitted.add(edge.id)
                delta.edges.append(edge)
        return delta


def _server_indexes(
    servers: list[Node],
) -> tuple[dict[str, list[Node]], dict[str, Node]]:
    """Two views of the `mcp_server` nodes: by file, and by name.

    `by_file` holds only the servers that declare a file — the
    detector-created ones. A path-heuristic server has none: the server
    *is* the `mcp/servers/<name>/` directory (stage 1), which is why the
    name view exists as well.

    The name is the node's `name`, which is what `mcp_entry_synthesis`
    matches a tool's `parent_server` against; a path-heuristic node also
    carries `server_name`, and both spellings are indexed so a
    `parent_server` written against either resolves. First write wins,
    which makes the resolution a function of `delta.nodes` order.
    """
    by_file: dict[str, list[Node]] = {}
    by_name: dict[str, Node] = {}
    for server in servers:
        file_path = server.attributes.get("source_file")
        if isinstance(file_path, str):
            by_file.setdefault(file_path, []).append(server)
        by_name.setdefault(server.name, server)
        declared = server.attributes.get("server_name")
        if isinstance(declared, str):
            by_name.setdefault(declared, server)
    return by_file, by_name


def _writers_for(
    source: Node,
    by_file: dict[str, list[Node]],
    by_name: dict[str, Node],
    declared: list[Node],
) -> list[Node]:
    """The `mcp_server` nodes that declare `source`, in id order.

    Four ways a server is visible from a source, in the order they are
    tried — each one is a different answer to "which node does a writer
    write into?":

    1. **`parent_server`** — the precise anchor. A detector that reads a
       tool declaration knows the server it belongs to, and names it.
       Nothing sets this attribute yet: 1.3a anchors a source at its
       *read* site, and the reader is a client, not the server. It is
       the hook a declaration-side detector would use. It is also the
       one anchor that *replaces* the rest: a `parent_server` that
       names no server means the precise evidence was checked and it
       failed, so no other anchor is consulted.
    2. **Same file** — the source's `source_file` equals the server's.
       This is the shape a framework-detected server has: `FastMCP(...)`
       and the catalogue read in one module.
    3. **Same server directory** — the source's file is under
       `mcp/servers/<name>/`, so the path-heuristic server named `<name>`
       (stage 1) is the one the file belongs to. The path heuristic
       makes the server a *directory*-level node, so any file under it
       is the server's file; `server_name_for_file` is that rule, reused
       rather than re-implemented.
    4. **A literal module path anywhere in the project** (1.3a.1.2 for
       the same file, project-wide in 1.3a.1.3) — some file spells out
       `...mcp.servers.<name>.server.create_<name>_server`, naming a
       server it talks to, so that server is a writer of this catalogue.
       This is the anchor that reaches a writer in *another* file:
       FinBot's `_SERVER_INTROSPECTORS` maps five server types to their
       factory modules inside the route files that read a catalogue, and
       the client that reads the catalogue it describes
       (`mcp/provider.py`) names no server at all. Deliberately wide —
       the same literal read at project scope — so it over-matches by
       construction; `max_writer_edges` bounds the product.

    2, 3 and 4 are unioned: they are three independent pieces of
    visibility evidence, and a file that both belongs to a server
    directory and names another server's module talks to both. 4 is
    project-wide, so it contains the same-file literals it used to be
    limited to — one edge per (server, source) pair either way, because
    an anchor contributes a node, and a node already in `found` is not
    added twice. A source visible from no server yields nothing — the
    honest result when the writer is not in the project either. No
    fallback matches a source to an arbitrary server: that would
    fabricate the edge the enumerator is supposed to find.
    """
    parent = source.attributes.get("parent_server")
    if isinstance(parent, str):
        server = by_name.get(parent)
        return [server] if server is not None else []

    file_path = source.attributes.get("source_file")
    if not isinstance(file_path, str):
        return []

    found = list(by_file.get(file_path, ()))
    name = server_name_for_file(file_path)
    if name is not None:
        server = by_name.get(name)
        if server is not None and server not in found:
            found.append(server)
    for server in declared:
        if server not in found:
            found.append(server)
    return sorted(found, key=lambda node: node.id)


def _source_type(source: Node) -> str | None:
    """The source node's family, or None.

    `untrusted_source` nodes always carry one (`_source_node` writes it),
    but the attribute is read as an unknown: a node built by a detector
    that is not this pipeline's would otherwise be matched against a
    table by a value that is not a string.
    """
    source_type = source.attributes.get("source_type")
    return source_type if isinstance(source_type, str) else None


def _component_writers_for(
    source: Node,
    capability: str,
    ctx: NormalizeContext,
    writer_index: dict[tuple[str, str], Node],
) -> list[Node]:
    """The nodes that call the write site of `capability`, in id order.

    The call-written families' anchor (B.2). Three gates, in this order:

      1. the source names a file — a source with no `source_file` is not
         visible from anything, exactly as in `_writers_for`;
      2. a symbol that credited `capability` resolves to a node — the
         symbol's own node, else the class that encloses it (see
         `_writing_symbols`). No node, no writer;
      3. the node is visible from the source: the same file, or a file
         that shares an import path with it (`_shares_an_import`).

    Nothing here looks at the store's name, the collection name or the
    mailbox address — none of them is in either call site. What is
    matched is a family (`capability`) and a file, which is the same
    evidence 1.3a uses for the read half.
    """
    file_path = source.attributes.get("source_file")
    if not isinstance(file_path, str):
        return []

    found: list[Node] = []
    for writer_file, names in _writing_symbols(ctx, capability):
        node = _resolve_writer_node(writer_file, names, writer_index)
        if node is None:
            continue
        if not _shares_an_import(writer_file, file_path, ctx):
            continue
        if node not in found:
            found.append(node)
    return sorted(found, key=lambda node: node.id)


def _writing_symbols(
    ctx: NormalizeContext,
    capability: str,
) -> list[tuple[str, tuple[str, ...]]]:
    """`(file, (candidate node names, most specific first))` per writer.

    One entry per symbol whose extracted capabilities include
    `capability` — the write is a property of the symbol's body, so the
    symbol is the evidence (ADR-0009). A method's capabilities are
    folded into its class by the extractor, and a method that is not
    classified has no node while its class does, so the enclosing class
    is the fallback name — the same "most specific node" rule the read
    side applies to `reader` and `container` (1.3a).

    The order is `ctx.capabilities`' own (manifest order), so the result
    is a function of the scan, not of a set's iteration order.
    """
    containers: dict[tuple[str, str], str] = {}
    for symbol in ctx.tsa_output.symbols:
        if symbol.kind == "method" and symbol.class_name:
            containers[(symbol.file_path, symbol.name)] = symbol.class_name

    out: list[tuple[str, tuple[str, ...]]] = []
    for file_path, file_caps in ctx.capabilities.items():
        for symbol in file_caps.symbols:
            if capability not in symbol.capabilities:
                continue
            container = containers.get((file_path, symbol.symbol_name))
            names = (
                (symbol.symbol_name, container)
                if container is not None
                else (symbol.symbol_name,)
            )
            out.append((file_path, names))
    return out


def _resolve_writer_node(
    file_path: str,
    names: tuple[str, ...],
    writer_index: dict[tuple[str, str], Node],
) -> Node | None:
    """The first of `names` that has a node in that file, or None."""
    for name in names:
        node = writer_index.get((file_path, name))
        if node is not None:
            return node
    return None


def _writer_index(delta: "GraphDelta") -> dict[tuple[str, str], Node]:
    """`(source_file, name)` → node, plus each node's `handler` alias.

    The nodes a writing symbol can be, indexed the way the read side
    indexes its readers: a node's own `name`, and — for entry points,
    whose name is `"GET /path"` rather than the handler's — its
    `handler` attribute. That is what lets a write inside a route
    handler resolve to the entry point, which is the shape an ingest
    route has (`POST /documents` calls `vectorstore.add_documents`).

    First write wins (`setdefault`), and `delta.nodes` is in stage order,
    so a method node takes precedence over an entry point of the same
    name: the more specific node is the writer. A node with no
    `source_file` — a detector node that has not been merged — is not a
    candidate at all, and neither is an `untrusted_source`: a source is a
    place, and a place does not write.

    `untrusted_sources._reader_index` is the twin of this function; the
    two are not shared because the read stage's index maps to ids (it
    runs before the merge that invalidates them) while this one holds the
    final nodes.
    """
    index: dict[tuple[str, str], Node] = {}
    for node in delta.nodes:
        if node.kind == "untrusted_source":
            continue
        file_path = node.attributes.get("source_file")
        if not isinstance(file_path, str):
            continue
        index.setdefault((file_path, node.name), node)
        handler = node.attributes.get("handler")
        if isinstance(handler, str):
            index.setdefault((file_path, handler), node)
    return index


def _shares_an_import(
    writer_file: str,
    source_file: str,
    ctx: NormalizeContext,
) -> bool:
    """True if the two files are the same, or import each other.

    The call-written families' visibility rule (B.2). `a == b` is the
    same-file write; the two `_module_matches_file` calls are the
    directional ones — the writer's module imports the reader's file, or
    the reader's module imports the writer's — and both directions are
    checked because the channel's two halves can be on either side of
    the import: an ingest service that imports the store module, or a
    route module that imports the ingest service.

    The match is `cross_file._module_matches_file`'s (B.1): trailing path
    segments, so a target root's prefix does not matter. Relative
    imports are already dropped by the entry-point parser, so they
    resolve to nothing here, as everywhere else.
    """
    if writer_file == source_file:
        return True
    imports = ctx.entry_points.imports
    return any(
        _module_matches_file(module, source_file)
        for module in imports.get(writer_file, ())
    ) or any(
        _module_matches_file(module, writer_file)
        for module in imports.get(source_file, ())
    )


def _project_declared_servers(
    ctx: NormalizeContext,
    by_name: dict[str, Node],
    manifest_paths: list[str],
) -> dict[str, list[str]]:
    """`server name → [files that name it]`, one pass over the project.

    The literal anchor at project scope (1.3a.1.3). The stage calls this
    once, and `ctx.module_for` keeps each manifest file parsed at most
    once per scan, so the pass is O(files) rather than
    O(sources × files). It is skipped entirely when the delta holds no
    catalogue source, which is every scan of a target with no MCP client.

    Three gates per file, the ones the per-file form applied to the
    source's own file, now applied to every file — all of them evidence
    the detector reads rather than guesses:

      - the file must parse;
      - the literal must name `<name>` and `create_<name>_server`
        consistently, and the module it spells must be a file this scan
        discovered — a typo or a missing package names no server the
        code can reach;
      - the named server must be an `mcp_server` node.

    A literal that fails a gate yields nothing. The value is the list of
    files that named the server, in manifest order: it is the provenance
    the edge rests on, and it is what makes the widening auditable
    ("which file said `finmail`?"). The name is what resolves to the
    node. Nothing here parses the `importlib.import_module` call the
    path is handed to: the literal is the evidence, and the call is not
    read.
    """
    found: dict[str, list[str]] = {}
    for entry in ctx.manifest.files:
        module = ctx.module_for(entry.path)
        if module is None:
            continue
        for name in declared_server_names(module):
            if name not in by_name:
                continue
            if not _server_module_exists(name, manifest_paths):
                continue
            files = found.setdefault(name, [])
            if entry.path not in files:
                files.append(entry.path)
    return found


def declared_server_names(module: ast.Module) -> list[str]:
    """`mcp_server` names the module spells out as factory paths.

    The FinBot shape, in each of the two route files that read a
    catalogue:

        _SERVER_INTROSPECTORS = {
            "finmail": "finbot.mcp.servers.finmail.server."
                       "create_finmail_server",
        }

    Both the dict key and the value name the server; the value is what
    is read, because the path is the claim about *which module* the code
    talks to — the key is a label. A path quoted in a docstring is not
    evidence, which is the one exclusion `_literal_values` makes.
    """
    names: list[str] = []
    for value in _literal_values(module):
        match = _SERVER_FACTORY_PATH.match(value)
        if match is None:
            continue
        name = match.group("name")
        # `servers/<name>/server.py` must hold `create_<name>_server`,
        # or the literal points at a factory this rule cannot name.
        if match.group("factory") != name:
            continue
        if name not in names:
            names.append(name)
    return names


def _literal_values(module: ast.Module) -> list[str]:
    """Every string literal the module uses as a value.

    `ast.walk` reaches every constant, including the ones that are bare
    expression statements — docstrings, and prose quoted at a reader.
    Those are excluded: the rule reads a *value* the code passes around,
    and a sentence in a docstring that happens to contain a module path
    is not one.
    """
    standalone = {
        id(node.value)
        for node in ast.walk(module)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node.value
        for node in ast.walk(module)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in standalone
    ]


def _server_module_exists(name: str, manifest_paths: list[str]) -> bool:
    """Is `mcp/servers/<name>/server.py` a file this scan discovered?

    The literal is a dotted module path and the manifest holds
    target-relative paths, so the check compares the *tail*: whatever
    prefix the target root carries, the server module is the same file.
    """
    suffix = f"mcp/servers/{name}/server.py"
    return any(
        path == suffix or path.endswith("/" + suffix)
        for path in manifest_paths
    )


def _write_edge(
    writer: Node,
    source: Node,
    ctx: NormalizeContext,
) -> Edge:
    """`writes_to` from the writer that produces the source.

    One edge shape for both mechanisms (B.2): a `mcp_server` that
    declares a catalogue and a component that calls a store write the
    same way — into a source that a later read pulls from — and the
    graph does not need to tell them apart to enumerate a chain.

    Direction is the data's, like the read edge and for the same reason:
    the content is produced by the writer and flows into the reader, so
    the write chain ends at the source and the read chain leaves it.

    The rule is structural — a writer and a source visible from the same
    file, or from files that import each other — and the producer never
    traced a value into the source, so the edge makes no data-flow claim:
    `data_flow_confirmed` stays at its schema default (`NULL`) and the
    edge is control, like `provides_tool` — the parallel
    server → declared-artifact edge (ADR-0015, decision 5; ADR-0016,
    decision 3).
    """
    return Edge(
        id=edge_id(writer.id, source.id, "writes_to"),
        source_id=writer.id,
        target_id=source.id,
        kind="writes_to",
        host_id=ctx.host_id,
    )


__all__ = [
    "MAX_WRITER_EDGES",
    "SERVER_DECLARED_SOURCE_TYPE",
    "UntrustedSourceWriterStage",
    "WRITER_SOURCE_TYPES",
    "declared_server_names",
]
