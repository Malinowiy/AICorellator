"""UntrustedSourceWriterStage: `writes_to` into a source (1.3a.1).

The stage is the write half of ADR-0010's channel: 1.3a's
`untrusted_sources` emits `source → reader`, and this one emits
`mcp_server → writes_to → source` so `find_chain_groups` has an
incoming edge to walk. These tests cover the four ways a server is
visible from a source — `parent_server`, the same file, the
`mcp/servers/<name>/` directory, and a literal factory module path
(1.3a.1.2), which is the anchor that reaches a server in another file —
plus the source-family gate, the flow attributes, and the absences.
"""

from __future__ import annotations

import ast

from aicorellator.graph.models import Node
from aicorellator.parsing.normalizer_stages import (
    FlowKindStage,
    UntrustedSourceWriterStage,
)
from aicorellator.parsing.normalizer_stages.untrusted_source_writers import (
    declared_server_names,
)


def _server(
    name: str,
    file_path: str | None = None,
    *,
    node_id: str | None = None,
    server_name: str | None = None,
) -> Node:
    attributes: dict[str, object] = {}
    if file_path is not None:
        attributes["source_file"] = file_path
    if server_name is not None:
        attributes["server_name"] = server_name
    return Node(
        id=node_id or f"mcp_server:{name}",
        kind="mcp_server",
        name=name,
        host_id="localhost",
        attributes=attributes,
    )


def _source(
    source_type: str = "mcp_tool_description",
    file_path: str = "app.py",
    *,
    node_id: str = "untrusted_source:1",
    **attrs,
) -> Node:
    return Node(
        id=node_id,
        kind="untrusted_source",
        name=f"{source_type}:client.list_tools",
        host_id="localhost",
        attributes={
            "source_type": source_type,
            "call_target": "client.list_tools",
            "source_file": file_path,
            "line_start": 1,
            **attrs,
        },
    )


def test_a_server_in_the_same_file_writes_the_catalogue(project) -> None:
    """The detector-server shape: server and read in one module."""
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("knowledge", "app.py", node_id="mcp_server:k"))
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [("writes_to", "mcp_server:k", "untrusted_source:1")]
    assert delta.edges[0].data_flow_confirmed == "unknown"


def test_flow_kind_is_control_once_the_deriving_stage_runs(project) -> None:
    """The stage sets the evidence; `FlowKindStage` derives the kind.

    `control`, not `data`: the edge is a declaration, and the producer
    traced no value into the source (ADR-0015 decision 3).
    """
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("knowledge", "app.py"))
    p.delta.nodes.append(_source(file_path="app.py"))

    UntrustedSourceWriterStage().apply(p.delta, p.ctx)
    FlowKindStage().apply(p.delta, p.ctx)

    assert [e.flow_kind for e in p.delta.edges] == ["control"]


def test_a_server_in_another_file_writes_nothing(project) -> None:
    """No server is visible from the source's file, so there is no edge.

    The writer cannot be named by what it writes (the source is a read
    site), so a server the file does not contain is not a candidate.
    """
    p = project(files={"app.py": "", "other.py": ""})
    p.delta.nodes.append(_server("knowledge", "other.py"))
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_a_path_heuristic_server_covers_its_whole_directory(project) -> None:
    """The server *is* the `mcp/servers/<name>/` directory (stage 1).

    A path-heuristic node carries no file of its own, so the source's
    file is matched against the directory the path names — the same rule
    `McpServerNodeStage` used to create the node.
    """
    p = project(files={"mcp/servers/knowledge/tools.py": ""})
    p.delta.nodes.append(_server("knowledge", server_name="knowledge"))
    p.delta.nodes.append(
        _source(file_path="mcp/servers/knowledge/tools.py")
    )

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [e.kind for e in delta.edges] == ["writes_to"]
    assert delta.edges[0].source_id == "mcp_server:knowledge"


def test_a_source_outside_the_server_directory_gets_no_edge(project) -> None:
    """`mcp/provider.py` is not under `mcp/servers/` — no server there.

    The FinBot shape: the client that reads the catalogue lives in
    `mcp/provider.py`, and the servers it talks to live in
    `mcp/servers/<name>/`. The file names no server, so no edge.
    """
    p = project(files={"mcp/provider.py": ""})
    p.delta.nodes.append(_server("knowledge", server_name="knowledge"))
    p.delta.nodes.append(_source(file_path="mcp/provider.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_a_parent_server_attribute_is_the_precise_anchor(project) -> None:
    """`parent_server` names the server, whatever file the source is in.

    The convention is `mcp_entry_synthesis`'s: a detector that reads a
    declaration records the server it belongs to. No producer sets it on
    an `untrusted_source` yet — 1.3a anchors at the read site — so this
    pins the hook, not a current corpus shape.
    """
    p = project(files={"app.py": "", "other.py": ""})
    p.delta.nodes.append(_server("knowledge", "other.py"))
    p.delta.nodes.append(
        _source(file_path="app.py", parent_server="knowledge")
    )

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [e.source_id for e in delta.edges] == ["mcp_server:knowledge"]


def test_a_parent_server_that_names_no_server_writes_nothing(project) -> None:
    """An unresolvable parent is not a licence to match any server."""
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("knowledge", "app.py"))
    p.delta.nodes.append(
        _source(file_path="app.py", parent_server="other")
    )

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_a_rag_corpus_has_no_writer_rule(project) -> None:
    """The scope is `mcp_tool_description` — one family, with evidence.

    A corpus read inside a server file is the server *reading* its
    store, not writing it: `vectorstore.similarity_search` in
    `mcp/servers/knowledge/server.py` (the pipeline fixture) is exactly
    that, and it must not produce a writer edge.
    """
    p = project(files={"mcp/servers/knowledge/server.py": ""})
    p.delta.nodes.append(_server("knowledge", server_name="knowledge"))
    p.delta.nodes.append(_source(
        source_type="rag_corpus",
        file_path="mcp/servers/knowledge/server.py",
    ))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_only_the_matching_server_writes(project) -> None:
    """Two servers, one file each; only the visible one writes."""
    p = project(files={"app.py": "", "other.py": ""})
    p.delta.nodes.append(_server("one", "app.py", node_id="mcp_server:1"))
    p.delta.nodes.append(_server("two", "other.py", node_id="mcp_server:2"))
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [e.source_id for e in delta.edges] == ["mcp_server:1"]


def test_two_servers_in_one_file_both_write(project) -> None:
    """Two declarations in one module are two writers, in id order."""
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("one", "app.py", node_id="mcp_server:1"))
    p.delta.nodes.append(_server("two", "app.py", node_id="mcp_server:2"))
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [e.source_id for e in delta.edges] == [
        "mcp_server:1", "mcp_server:2",
    ]


def test_one_edge_per_pair_even_for_repeated_sources(project) -> None:
    """The edge id is content-derived, so a repeat is not a second edge."""
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("knowledge", "app.py"))
    p.delta.nodes.append(_source(file_path="app.py"))
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert len(delta.edges) == 1


def test_no_server_nodes_means_no_work(project) -> None:
    """A scan with no `mcp_server` node emits nothing."""
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []


# ============================================================
# The literal factory path (1.3a.1.2)
# ============================================================

INTROSPECTORS = '''\
"""Route helpers -- server introspection."""
import importlib

_SERVER_INTROSPECTORS = {
    "finmail": "finbot.mcp.servers.finmail.server.create_finmail_server",
    "findrive": "finbot.mcp.servers.findrive.server.create_findrive_server",
}


async def _get_default_tool_definitions(server_type):
    """Introspect a server's FastMCP instance."""
    factory_path = _SERVER_INTROSPECTORS.get(server_type)
    module = importlib.import_module(factory_path.rsplit(".", 1)[0])
    return await module.list_tools()
'''


def _server_modules(*names: str) -> dict[str, str]:
    """Manifest entries for the server modules a literal names."""
    return {f"mcp/servers/{name}/server.py": "" for name in names}


def test_a_literal_factory_path_names_its_server(project) -> None:
    """FinBot's `_SERVER_INTROSPECTORS`: the route file names its servers.

    No server is visible from the route file — none is in it, and its
    path is not under `mcp/servers/` — so the literal is the anchor. The
    named server's own module lives elsewhere, which is the point.
    """
    p = project(files={
        "apps/routes/api.py": INTROSPECTORS,
        **_server_modules("finmail", "findrive"),
    })
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_server("findrive", server_name="findrive"))
    p.delta.nodes.append(_source(file_path="apps/routes/api.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "mcp_server:findrive", "untrusted_source:1"),
        ("writes_to", "mcp_server:finmail", "untrusted_source:1"),
    ]
    assert {e.flow_kind for e in delta.edges} == {"control"}
    assert {e.data_flow_confirmed for e in delta.edges} == {"unknown"}


def test_a_literal_whose_module_is_not_in_the_manifest_writes_nothing(
    project,
) -> None:
    """A typo or a missing package names no server this scan can reach."""
    p = project(files={
        "apps/routes/api.py": INTROSPECTORS,
        **_server_modules("finmail"),
    })
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_server("findrive", server_name="findrive"))
    p.delta.nodes.append(_source(file_path="apps/routes/api.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [e.source_id for e in delta.edges] == ["mcp_server:finmail"]


def test_a_literal_that_names_no_server_node_writes_nothing(project) -> None:
    """The module exists, but the graph has no node for the server."""
    p = project(files={
        "apps/routes/api.py": INTROSPECTORS,
        **_server_modules("finmail", "findrive"),
    })
    p.delta.nodes.append(_server("taxcalc", server_name="taxcalc"))
    p.delta.nodes.append(_source(file_path="apps/routes/api.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []



def test_a_literal_in_another_file_is_not_an_anchor(project) -> None:
    """The literal is read from the source's own file, not the project."""
    p = project(files={
        "apps/routes/api.py": "def read():\n    return server.list_tools()\n",
        "other.py": INTROSPECTORS,
        **_server_modules("finmail", "findrive"),
    })
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_server("findrive", server_name="findrive"))
    p.delta.nodes.append(_source(file_path="apps/routes/api.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_a_literal_path_naming_a_different_factory_is_ignored(project) -> None:
    """`servers/finmail/server.py` must hold `create_finmail_server`.

    A path whose two halves disagree names two servers, and the rule
    picks the module — the same name twice, or nothing.
    """
    mismatched = INTROSPECTORS.replace(
        "finbot.mcp.servers.finmail.server.create_finmail_server",
        "finbot.mcp.servers.finmail.server.create_taxcalc_server",
    )
    p = project(files={
        "apps/routes/api.py": mismatched,
        **_server_modules("finmail", "findrive"),
    })
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_server("findrive", server_name="findrive"))
    p.delta.nodes.append(_source(file_path="apps/routes/api.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [e.source_id for e in delta.edges] == ["mcp_server:findrive"]


def test_a_path_quoted_in_a_docstring_is_not_evidence(project) -> None:
    """A sentence is not a value: the rule reads literals the code uses."""
    p = project(files={
        "apps/routes/api.py": (
            '"""See finbot.mcp.servers.finmail.server.'
            'create_finmail_server."""\n'
        ),
        **_server_modules("finmail"),
    })
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_source(file_path="apps/routes/api.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_a_literal_does_not_anchor_a_non_catalogue_source(project) -> None:
    """The family gate is first: a corpus in that file is still no match."""
    p = project(files={
        "apps/routes/api.py": INTROSPECTORS,
        **_server_modules("finmail", "findrive"),
    })
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_source(
        source_type="rag_corpus", file_path="apps/routes/api.py",
    ))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_the_literal_writers_join_the_structurally_visible_one(
    project,
) -> None:
    """A file inside a server directory that also names another server.

    The anchors are independent pieces of evidence, so the edges are a
    union: `knowledge` owns the file, and the literal names `finmail`.
    """
    p = project(files={
        "mcp/servers/knowledge/server.py": INTROSPECTORS,
        **_server_modules("finmail"),
    })
    p.delta.nodes.append(_server("knowledge", server_name="knowledge"))
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(
        _source(file_path="mcp/servers/knowledge/server.py")
    )

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [e.source_id for e in delta.edges] == [
        "mcp_server:finmail", "mcp_server:knowledge",
    ]


def test_declared_server_names_reads_the_path_not_the_key() -> None:
    """One name per server, in source order, from the module path."""
    module = ast.parse(
        '_SERVER_INTROSPECTORS = {\n'
        '    "label-a": "pkg.mcp.servers.finmail.server.'
        'create_finmail_server",\n'
        '    "label-b": "pkg.mcp.servers.findrive.server.'
        'create_findrive_server",\n'
        '    "again": "pkg.mcp.servers.finmail.server.'
        'create_finmail_server",\n'
        '}\n'
    )

    assert declared_server_names(module) == ["finmail", "findrive"]


def test_declared_server_names_ignores_a_non_factory_literal() -> None:
    """A module path that is not a server factory names no server."""
    module = ast.parse(
        'PATH = "pkg.mcp.servers.finmail.server.serve"\n'
        'URL = "http://example.test/tools"\n'
        'BARE = "finmail"\n'
        'ONE = "mcp.servers.finmail.server.create_finmail_server"\n'
    )

    assert declared_server_names(module) == ["finmail"]

