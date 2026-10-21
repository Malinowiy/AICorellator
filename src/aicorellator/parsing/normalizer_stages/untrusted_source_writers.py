"""UntrustedSourceWriterStage — `writes_to` into a source (1.3a.1).

Run order: 11 of 18 (see `normalizer_stages.STAGES`).

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
  - It must run before `flow_kind` (stage 12), so the one derivation
    (ADR-0015 decision 3) classifies the new edge instead of this stage
    naming a `flow_kind` of its own.

**The writer is the server, and for one source family only.** Of the
five families in ADR-0010's table, exactly one is evidenced on the four
corpora: `mcp_tool_description`. Its writer is not a call — it is a
declaration. The MCP server declares its tool catalogue
(`@mcp.tool()`), and the client reads it back (`list_tools`). So the
write edge is `mcp_server → untrusted_source`, and the source is the
one the server declares. RAG corpora, mailboxes and user uploads have
no writer evidence at all (1.3a.1's report records the scan), so no
rule is guessed for them: a pattern that fires nowhere is a claim the
data does not support (ADR-0009, and 1.3a's "deferred rather than
guessed at").

**Anchoring** is 1.3a's: the source node is one *read site*, so a writer
cannot name it by what it writes. It can only be matched to a source
that is visible from it — `_writers_for` states the four ways that
happens. The fourth is the literal module path: a file that spells out
`...mcp.servers.<name>.server.create_<name>_server` is naming a server
it talks to, so that server is a writer of a catalogue the file reads
even when the server's own module is elsewhere. That is FinBot's
`_SERVER_INTROSPECTORS` shape.

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


# The source families a writer rule exists for. One entry, deliberately:
# see the module docstring. Widening this set is widening the write side,
# and each addition needs corpus evidence before it lands.
WRITER_SOURCE_TYPES: frozenset[str] = frozenset({"mcp_tool_description"})


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
    """`mcp_server → writes_to → untrusted_source`, one edge per pair."""

    name = "untrusted_source_writers"

    def __init__(self, max_writer_edges: int = MAX_WRITER_EDGES) -> None:
        """`max_writer_edges` bounds the edges this stage emits per scan.

        The constant is the default; the argument is the test seam.
        """
        self.max_writer_edges = max_writer_edges

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        servers = [n for n in delta.nodes if n.kind == "mcp_server"]
        if not servers:
            return delta
        sources = [
            node for node in delta.nodes
            if node.kind == "untrusted_source"
            and node.attributes.get("source_type") in WRITER_SOURCE_TYPES
        ]
        if not sources:
            return delta

        by_file, by_name = _server_indexes(servers)
        manifest_paths = [entry.path for entry in ctx.manifest.files]
        # One project pass, not one pass per source (1.3a.1.3): the
        # literal is evidence about the project, and a source's own file
        # need not carry it. The pass is O(files), and `ctx.module_for`
        # keeps each file parsed at most once per scan.
        declared = _project_declared_servers(ctx, by_name, manifest_paths)
        writers = [by_name[name] for name in declared]

        emitted: set[str] = set()
        for source in sources:
            for server in _writers_for(source, by_file, by_name, writers):
                edge = _write_edge(server, source, ctx)
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
    server: Node,
    source: Node,
    ctx: NormalizeContext,
) -> Edge:
    """`writes_to` from the server that declares the catalogue.

    Direction is the data's, like the read edge and for the same reason:
    the catalogue is produced by the server and flows into the client, so
    the write chain ends at the source and the read chain leaves it.

    `data_flow_confirmed = "unknown"` is the honest value. The rule is
    structural — a server and a source visible from the same file — and
    the producer never traced a value into the source. `literal_only`
    would claim the catalogue's text is a literal and `call_result_match`
    would claim it is the result of a call; neither was checked, and
    ADR-0015 decision 3 maps the latter to `flow_kind = "data"`, which
    would assert a dataflow the evidence does not contain. `unknown` is
    what `provides_tool` — the parallel server → declared-artifact edge —
    carries (ADR-0015, decision 5).
    """
    return Edge(
        id=edge_id(server.id, source.id, "writes_to"),
        source_id=server.id,
        target_id=source.id,
        kind="writes_to",
        host_id=ctx.host_id,
        data_flow_confirmed="unknown",
    )


__all__ = [
    "MAX_WRITER_EDGES",
    "UntrustedSourceWriterStage",
    "WRITER_SOURCE_TYPES",
    "declared_server_names",
]
