"""The FastMCP middleware detector through the normalizer (B.4-gap-5).

The detector's own tests are
`tests/test_framework_fastmcp_middleware.py`. Two things need a
`NormalizeContext` and the file-backed `project` fixture, and both are
about what happens to a detector node *after* the detector:

  - **M1 — the merge.** `DetectorMergeStage` is keyed by
    `(name, source_file)` and makes the detector node authoritative for
    kind (ADR-0011, ADR-0024 §5). A middleware class is the strongest
    case for that rule: the capability layer reads the class's *body*
    (this one writes an audit log, so its capabilities say `sink`), the
    detector reads the SDK's *base* (so the kind is `guardrail`), and the
    two are the same symbol. The TSA node is replaced, its capabilities
    move to the detector node, and the detector node's `observed_by`
    survives.

  - **X1 — B.4a.** `GuardrailEdgeStage` reads `delta.nodes` after the
    merge, so a detector `guardrail` node is a `guardrail` node to it.

**The X1 fixtures pin what B.4a does for a middleware.** B.4a's evidence
is a call site inside a component's own body, a literal `@guardrail`
decorator, or — since ADR-0019 rev 1 §1 — a *registration* on the
`mcp_server` the middleware runs on: `mcp.add_middleware(...)` in the
server's own module, or a `@mcp.middleware(...)` decorator on a factory
that returns the class. So X1a — the realistic FastMCP shape, a
middleware subclass registered on a server in its own file — is now a
**node and an edge**, pointing at the `mcp_server` (`resolved_via`
`"registration"`); X1a-unregistered, the same file with the registration
removed, is the node-and-no-edge shape B.4-gap-5 pinned, because
co-location is still not evidence. X1a′ is unchanged: a component whose
own body constructs the middleware, through the call graph. The
detector is node-only in all of them — the edges are B.4a's.


The pipeline order is the real one: the detector runs, the merge places
its nodes in the delta, and B.4a runs last on the merged node set.
"""

from __future__ import annotations

import ast

from aicorellator.graph.models import Node
from aicorellator.parsing.capability_extractor import (
    FileCapabilities,
    SymbolCapabilities,
)
from aicorellator.parsing.entry_points import run as run_entry_point_detector
from aicorellator.parsing.framework_detectors import DetectionResult
from aicorellator.parsing.framework_detectors.fastmcp_middleware import (
    FastMCPMiddlewareDetector,
)
from aicorellator.parsing.models import TSASymbol
from aicorellator.parsing.normalizer_stages import (
    ClassNodeStage,
    DetectorMergeStage,
    GuardrailEdgeStage,
    McpServerNodeStage,
)

# An MCP server file: the convention-based path is what
# `McpServerNodeStage` reads, so a `mcp_server` node appears for it.
SERVER_FILE = "mcp/servers/audit/server.py"

# A file that is not a server path: no `mcp_server` node, so X1b has
# nothing for B.4a to link to.
CORE_FILE = "core/middleware/audit.py"

# M1: a class the capability layer calls a sink (its hook writes an audit
# log) and the detector calls a guardrail (its base is the SDK's). One
# symbol, two producers, and the merge decides between them.
MERGE_SOURCE = """\
from fastmcp.server.middleware import Middleware


class AuditMiddleware(Middleware):
    \"\"\"Logs every tool call to disk.\"\"\"

    async def on_call_tool(self, context, call_next):
        write_audit_log(context)
        return await call_next(context)
"""

# X1a: the shape the SDK documents — the middleware is registered on the
# server, in the server's own file, and no component body names it. Since
# ADR-0019 rev 1 that registration *is* B.4a's evidence for the
# `mcp_server` target, so the node has an edge; X1a-unregistered (below)
# is the same file without the registration, which still has none.
REGISTERED_SOURCE = """\
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

mcp = FastMCP("audit")


class AuditMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        return await call_next(context)


mcp.add_middleware(AuditMiddleware())


@mcp.tool
def search(query):
    return query
"""

# X1a-unregistered: the same server file with the `add_middleware(...)`
# line gone. The middleware class and the server are still co-located,
# and co-location is still not evidence (ADR-0019 rev 1 §1) — a node and
# no edge, which is the negative B.4-gap-5 pinned before the revision.
UNREGISTERED_SOURCE = """\
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

mcp = FastMCP("audit")


class AuditMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        return await call_next(context)


@mcp.tool
def search(query):
    return query
"""

# X1a': the shape B.4a's call-site rule does fire on — a component whose
# own body constructs the middleware. `AuditMiddleware()` is an
# `ast.Name` call with the same name the guardrail node carries.
BUILDING_SOURCE = """\
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

mcp = FastMCP("audit")


class AuditMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        return await call_next(context)


@mcp.tool
def audited_search(query):
    mw = AuditMiddleware()
    return mw.run(query)
"""

# X1b: a middleware with nothing to link to — no server, no component
# that names it. A node and no edge.
LONELY_SOURCE = """\
from fastmcp.server.middleware import Middleware


class AuditMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        return await call_next(context)
"""

# X1c/X1d: both producers in one file — a capability-derived guardrail
# (`pii_check`, called from the tool's body) and a detector-derived one.
MIXED_SOURCE = """\
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from core.rails.pii import pii_check

mcp = FastMCP("audit")


class AuditMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        return await call_next(context)


@mcp.tool
def guarded_search(query):
    pii_check(query)
    mw = AuditMiddleware()
    return mw.run(query)
"""


# ============================================================
# Fixture builders
# ============================================================

def _symbol(
    name: str,
    kind: str,
    file_path: str,
    source: str,
) -> TSASymbol:
    """A TSA symbol at the real line of `name` in `source`."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            if node.name == name:
                return TSASymbol(
                    name=name, kind=kind, file_path=file_path,
                    line_start=node.lineno, line_end=node.lineno + 4,
                )
    raise AssertionError(name)


def _caps(
    *entries: tuple[str, str, set[str]],
) -> dict[str, FileCapabilities]:
    """`(file, symbol, capabilities)` triples → the file capability map.

    Capabilities are the only positive signal about behaviour
    (ADR-0016, decision 7): a write makes a class a sink, and a
    capability-free helper called from a component's body is a
    `validates_input` candidate.
    """
    out: dict[str, FileCapabilities] = {}
    for file_path, name, capabilities in entries:
        fc = out.setdefault(
            file_path, FileCapabilities(file_path=file_path, symbols=[])
        )
        fc.symbols.append(SymbolCapabilities(
            symbol_name=name,
            file_path=file_path,
            capabilities=set(capabilities),
        ))
    return out


def _detector_nodes(source: str, file_path: str) -> list[Node]:
    """The nodes the real detector returns for `source`."""
    return FastMCPMiddlewareDetector().detect(
        file_path, ast.parse(source), source
    ).nodes


def _detection(nodes: list[Node]) -> DetectionResult:
    return DetectionResult(framework="fastmcp_middleware", nodes=list(nodes))


def _capability_guardrail(name: str, file_path: str) -> Node:
    """The `guardrail` node the capability producer would have left.

    Built by hand, the way `test_guardrail_edges` builds one: the stage
    under test reads `delta.nodes`, and what put the node there is not
    what these tests are about. Its `observed_by` stays the `Node`
    default `["ast"]` — ADR-0024 §1's provenance for a
    capability-derived guardrail, which is what makes it tellable apart
    from the detector's node in X1c.
    """
    return Node(
        id=f"guardrail:{name}:{file_path}",
        kind="guardrail",
        name=name,
        host_id="localhost",
        attributes={"source_file": file_path},
    )


def _tool(name: str, file_path: str) -> Node:
    """A `tool` node for a function the file defines.

    B.4a links `guardrail → tool`, so the component in X1a′/X1d is a
    tool node. Built by hand for the same reason
    `_capability_guardrail` is: which stage would have produced it is not
    what these tests are about, and the resolver reads the file for the
    body either way.
    """
    return Node(
        id=f"tool:{name}:{file_path}",
        kind="tool",
        name=name,
        host_id="localhost",
        attributes={"source_file": file_path},
    )


def _scan(
    project,
    *,
    source: str,
    file_path: str = SERVER_FILE,
    symbols: list[TSASymbol] | None = None,
    capabilities: dict[str, FileCapabilities] | None = None,
    extra_nodes: list[Node] | None = None,
):
    """The pipeline up to B.4a: detector → merge → guardrail_edges.

    `McpServerNodeStage` runs because the file path is a server path, so
    the `mcp_server` node X1a asks about is in the delta when B.4a runs.
    Returns the project and the detector's own node list, so a test can
    compare what the detector returned with what the graph kept.
    """
    nodes = _detector_nodes(source, file_path)
    p = project(
        files={file_path: source},
        symbols=symbols,
        capabilities=capabilities,
        detection=_detection(nodes),
    )
    p.ctx.entry_points = run_entry_point_detector(p.ctx.manifest)
    McpServerNodeStage().apply(p.delta, p.ctx)
    ClassNodeStage().apply(p.delta, p.ctx)
    DetectorMergeStage().apply(p.delta, p.ctx)
    p.delta.nodes.extend(extra_nodes or [])
    GuardrailEdgeStage().apply(p.delta, p.ctx)
    return p, nodes


# ============================================================
# M1 — the merge
# ============================================================

def test_a_detector_guardrail_replaces_the_tsa_node_of_the_same_name(
    project,
) -> None:
    """ADR-0011: same `(name, source_file)`, the detector is authoritative.

    `AuditMiddleware` is one symbol with two readings: the capability
    layer sees a body that writes an audit log and classifies the class a
    `sink`; the detector sees `Middleware` in its bases and emits a
    `guardrail`. The detector node replaces the class node, takes the
    class's capabilities (the detector found none of its own), and keeps
    its own `observed_by` — which is what makes the two producers
    tellable apart in the graph afterwards.
    """
    nodes = _detector_nodes(MERGE_SOURCE, SERVER_FILE)
    assert [n.name for n in nodes] == ["AuditMiddleware"]
    assert nodes[0].attributes["line_start"] == 4

    p = project(
        files={SERVER_FILE: MERGE_SOURCE},
        symbols=[_symbol("AuditMiddleware", "class", SERVER_FILE,
                         MERGE_SOURCE)],
        capabilities=_caps(
            (SERVER_FILE, "AuditMiddleware", {"writes_filesystem"})
        ),
        detection=_detection(nodes),
    )
    ClassNodeStage().apply(p.delta, p.ctx)
    tsa_node = p.named("AuditMiddleware")
    assert tsa_node.kind == "sink"
    assert tsa_node.capabilities != []

    DetectorMergeStage().apply(p.delta, p.ctx)

    guardrails = p.nodes("guardrail")
    assert len(guardrails) == 1
    # The detector's id, not a rebuilt one: the node is the replacement.
    assert guardrails[0].id == nodes[0].id
    # The class node is gone, not duplicated, and its kind with it.
    assert p.nodes("sink") == []
    # Capabilities survive the replacement (ADR-0011).
    assert guardrails[0].capabilities == tsa_node.capabilities
    assert guardrails[0].observed_by == [
        "ast", "framework:fastmcp_middleware",
    ]


# ============================================================
# X1 — B.4a on a detector node
# ============================================================

def test_a_middleware_registered_on_a_server_is_a_node_and_an_edge(
    project,
) -> None:
    """X1a: the SDK's own shape, and what B.4a makes of it since rev 1.

    The middleware is registered on the server (`mcp.add_middleware(...)`)
    in the server's own file, so a `mcp_server` node and the detector's
    `guardrail` node are in the same delta — and ADR-0019 rev 1 §1 makes
    that registration the `mcp_server` target's evidence: the middleware
    runs on the server, and the server is the container its tools live
    in. Registering the middleware puts its name in no component's body,
    which is why the call-site form cannot see it and this form exists:
    the evidence is the server's module AST, not the name-keyed call
    graph (ADR-0016).

    `resolved_via` names the form (`registration`); X1a-unregistered is
    the same shape with the registration removed, and it keeps the node
    and no edge.
    """
    p, nodes = _scan(
        project,
        source=REGISTERED_SOURCE,
        symbols=[
            _symbol("AuditMiddleware", "class", SERVER_FILE,
                    REGISTERED_SOURCE),
        ],
    )

    assert [n.name for n in nodes] == ["AuditMiddleware"]
    guardrail = p.named("AuditMiddleware")
    assert guardrail.kind == "guardrail"
    server = p.named("audit")
    assert server.kind == "mcp_server"
    guards = p.edges("guards")
    assert [(e.source_id, e.target_id) for e in guards] == [
        (guardrail.id, server.id),
    ]
    assert guards[0].attributes["resolved_via"] == "registration"
    assert guards[0].flow_kind == "structural"


def test_a_middleware_in_a_server_file_with_no_registration_has_no_edge(
    project,
) -> None:
    """X1a-unregistered: co-location is still not evidence (rev 1 §1).

    The middleware class and the server share the file, and nothing
    registers the middleware on the server. That is the negative
    B.4-gap-5 pinned: a node and no edge. Rev 1 widens what counts as
    evidence; it does not make a file layout one.
    """
    p, nodes = _scan(
        project,
        source=UNREGISTERED_SOURCE,
        symbols=[
            _symbol("AuditMiddleware", "class", SERVER_FILE,
                    UNREGISTERED_SOURCE),
        ],
    )

    assert [n.name for n in nodes] == ["AuditMiddleware"]
    assert p.named("AuditMiddleware").kind == "guardrail"
    assert [n.name for n in p.nodes("mcp_server")] == ["audit"]
    assert "add_middleware" not in UNREGISTERED_SOURCE
    assert p.edges("guards") == []


def test_a_middleware_a_component_body_constructs_is_linked(project) -> None:
    """X1a′: `mw = AuditMiddleware()` inside the tool.

    B.4a's call-site rule reads its name-keyed call graph, which records
    `ast.Name` call targets, and `AuditMiddleware()` is one — with the
    same name the guardrail node carries. So the edge is emitted, and it
    is not a claim this step makes: B.4a decides what a `guards` edge
    needs, and this fixture pins what it decides for a middleware a
    component constructs.
    """
    p, nodes = _scan(
        project,
        source=BUILDING_SOURCE,
        symbols=[
            _symbol("AuditMiddleware", "class", SERVER_FILE,
                    BUILDING_SOURCE),
        ],
        extra_nodes=[_tool("audited_search", SERVER_FILE)],
    )

    assert [n.name for n in nodes] == ["AuditMiddleware"]
    guardrail = p.named("AuditMiddleware")
    tool = p.named("audited_search")
    assert guardrail.kind == "guardrail"
    assert tool.kind == "tool"
    guards = p.edges("guards")
    assert [(e.source_id, e.target_id) for e in guards] == [
        (guardrail.id, tool.id),
    ]
    assert guards[0].attributes["resolved_via"] == "call_graph"
    assert guards[0].flow_kind == "structural"


def test_a_middleware_with_nothing_to_link_to_is_a_node_and_no_edge(
    project,
) -> None:
    """X1b: no server, no component that names it — a node and no edge.

    The file is not a server path, so there is no `mcp_server` node, and
    nothing constructs the middleware. The detector node is in the graph
    either way; this is the false negative B.4a's own docstring states
    (a guardrail wired in by the framework).
    """
    p, nodes = _scan(
        project,
        source=LONELY_SOURCE,
        file_path=CORE_FILE,
        symbols=[
            _symbol("AuditMiddleware", "class", CORE_FILE, LONELY_SOURCE),
        ],
    )

    assert [n.name for n in nodes] == ["AuditMiddleware"]
    assert p.named("AuditMiddleware").kind == "guardrail"
    assert p.nodes("mcp_server") == []
    assert p.edges("guards") == []


def test_a_capability_guardrail_and_a_detector_guardrail_coexist(
    project,
) -> None:
    """X1c: two `guardrail` nodes from two producers, one file.

    `pii_check` is the capability producer's node — a helper called from
    the tool's body, credited `validates_input` (ADR-0024 §2) — and
    `AuditMiddleware` is the detector's, credited from its base. Both are
    `guardrail` nodes and both are in the graph; `observed_by` is what
    tells them apart (ADR-0024 §1), and neither producer overwrites the
    other.
    """
    p, nodes = _scan(
        project,
        source=MIXED_SOURCE,
        symbols=[
            _symbol("AuditMiddleware", "class", SERVER_FILE, MIXED_SOURCE),
        ],
        extra_nodes=[_capability_guardrail("pii_check", SERVER_FILE)],
    )

    assert [n.name for n in nodes] == ["AuditMiddleware"]
    guardrails = {n.name: n for n in p.nodes("guardrail")}
    assert sorted(guardrails) == ["AuditMiddleware", "pii_check"]
    assert guardrails["AuditMiddleware"].observed_by == [
        "ast", "framework:fastmcp_middleware",
    ]
    assert guardrails["pii_check"].observed_by == ["ast"]


def test_both_guardrails_are_visible_to_the_component_that_calls_them(
    project,
) -> None:
    """X1d: B.4a links both, because the tool's body names both.

    The component calls `pii_check(query)` and constructs
    `AuditMiddleware()` in its own body, so the call graph holds both
    names under the tool's key and B.4a emits one edge per guardrail.
    Nothing about the pair is special to this step: B.4a links every
    `guardrail` node its evidence supports, and which producer made the
    node is not part of its test.
    """
    p, _ = _scan(
        project,
        source=MIXED_SOURCE,
        symbols=[
            _symbol("AuditMiddleware", "class", SERVER_FILE, MIXED_SOURCE),
        ],
        extra_nodes=[
            _capability_guardrail("pii_check", SERVER_FILE),
            _tool("guarded_search", SERVER_FILE),
        ],
    )

    guardrails = {n.name: n.id for n in p.nodes("guardrail")}
    tool = p.named("guarded_search")
    assert tool.kind == "tool"
    pairs = sorted(
        (e.source_id, e.target_id) for e in p.edges("guards")
    )
    assert pairs == sorted(
        (guardrails[name], tool.id)
        for name in ("AuditMiddleware", "pii_check")
    )
    assert {e.attributes["resolved_via"] for e in p.edges("guards")} == {
        "call_graph",
    }
