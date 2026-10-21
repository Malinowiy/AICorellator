"""UntrustedSourceWriterStage — `writes_to` into a source (1.3a.1).

Run order: 11 of 17 (see `normalizer_stages.STAGES`).

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
that is visible from the same file — `_writers_for` states the four ways
that happens. The fourth (1.3a.1.2) is the literal module path: a file
that spells out `...mcp.servers.<name>.server.create_<name>_server` is
naming the server it talks to, so the server is a writer of the
catalogue that file reads even when the server's own module is
elsewhere. That is FinBot's `_SERVER_INTROSPECTORS` shape.

Side effects: none. The stage reads the delta only: it emits edges
between nodes other stages created, and creates none.
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

from ...graph.models import Edge, Node
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import edge_id, server_name_for_file

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


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

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        servers = [n for n in delta.nodes if n.kind == "mcp_server"]
        if not servers:
            return delta
        by_file, by_name = _server_indexes(servers)
        manifest_paths = [entry.path for entry in ctx.manifest.files]

        emitted: set[str] = set()
        for source in delta.nodes:
            if source.kind != "untrusted_source":
                continue
            if source.attributes.get("source_type") not in WRITER_SOURCE_TYPES:
                continue
            declared = _declared_servers(
                source, ctx, by_name, manifest_paths
            )
            for server in _writers_for(
                source, by_file, by_name, declared
            ):
                edge = _write_edge(server, source, ctx)
                if edge.id in emitted:
                    continue
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
    4. **A literal module path in the same file** (1.3a.1.2) — the file
       spells out `...mcp.servers.<name>.server.create_<name>_server`,
       naming the server whose catalogue it reads. This is the anchor
       that reaches a writer in *another* file: FinBot's
       `_SERVER_INTROSPECTORS` maps five server types to their factory
       modules inside the route file that reads a catalogue, and no
       server is otherwise visible from that file.

    2, 3 and 4 are unioned: they are three independent pieces of
    visibility evidence, and a file that both belongs to a server
    directory and names another server's module talks to both. A source
    visible from no server yields nothing — the honest result when the
    writer is not in the file. No fallback matches a source to an
    arbitrary server: that would fabricate the edge the enumerator is
    supposed to find.
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


def _declared_servers(
    source: Node,
    ctx: NormalizeContext,
    by_name: dict[str, Node],
    manifest_paths: list[str],
) -> list[Node]:
    """The servers the source's own file names by factory module path.

    Three gates, all of them evidence the detector reads rather than
    guesses:

      - the file must parse (`ctx.module_for` reads and parses each
        manifest file at most once per scan);
      - the literal must name `<name>` and `create_<name>_server`
        consistently, and the module it spells must be a file this scan
        discovered — a typo or a missing package names no server the
        code can reach;
      - the named server must be an `mcp_server` node.

    A literal that fails a gate yields nothing. Nothing here parses the
    `importlib.import_module` call the path is handed to: the literal is
    the evidence, and the call is not read.
    """
    file_path = source.attributes.get("source_file")
    if not isinstance(file_path, str):
        return []
    module = ctx.module_for(file_path)
    if module is None:
        return []

    found: list[Node] = []
    for name in declared_server_names(module):
        if not _server_module_exists(name, manifest_paths):
            continue
        server = by_name.get(name)
        if server is not None and server not in found:
            found.append(server)
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
    "UntrustedSourceWriterStage",
    "WRITER_SOURCE_TYPES",
    "declared_server_names",
]
