"""UntrustedSourceWriterStage: `writes_to` into a store.

The stage is the write half of ADR-0010's channel: 1.3a's
`untrusted_sources` emits `source → reader`, and this one emits
`writer → writes_to → store` plus `store → reads_from → source`, so
`find_chain_groups` has an incoming edge to walk and a place to join on.
These tests cover the four ways a server is visible from a source —
`parent_server`, the same file, the `mcp/servers/<name>/` directory, and
a literal factory module path (1.3a.1.2 in the source's own file,
project-wide in 1.3a.1.3) — plus the source-family gate, the flow
attributes, the emission cap, and the absences.

B.3c (ADR-0021 §2–§3, §5, §8): a **call-written** family's `writes_to`
targets a `store` the stage creates, named by the *read site* — the
source type and the call target — with one `reads_from` from that store
back to the read site, and both edges carry §8's `identity_confidence`.

B.3b-mcp-catalogue: the **declaration-written** family is migrated the
same way, named by its *writer* instead — `mcp_catalogue` and the
server's name — so every write-side family now ends at a store.
"""

from __future__ import annotations

import ast

from aicorellator.graph.models import Node
from aicorellator.parsing.capability_extractor import (
    FileCapabilities,
    SymbolCapabilities,
)
from aicorellator.parsing.entry_points import EntryPointOutput
from aicorellator.parsing.models import TSASymbol
from aicorellator.parsing.normalizer_stages import (
    FlowKindStage,
    UntrustedSourceWriterStage,
)
from aicorellator.parsing.normalizer_stages.untrusted_source_writers import (
    CATALOGUE_FAMILY,
    CATALOGUE_WRITER_BASE,
    WRITER_SOURCE_TYPES,
    declared_server_names,
)
from aicorellator.store.confidence import (
    BASE,
    CODE_CONTEXT_SHARED_IMPORT,
    CODE_CONTEXT_SAME_MODULE,
    LITERAL_EQUAL,
    combine_signals,
)
from aicorellator.store.resolver import store_node_id


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
    call_target: str = "client.list_tools",
    **attrs,
) -> Node:
    return Node(
        id=node_id,
        kind="untrusted_source",
        name=f"{source_type}:{call_target}",
        host_id="localhost",
        attributes={
            "source_type": source_type,
            "call_target": call_target,
            "source_file": file_path,
            "line_start": 1,
            **attrs,
        },
    )


def _component(
    name: str,
    file_path: str,
    *,
    node_id: str | None = None,
    kind: str = "tool",
    **attrs,
) -> Node:
    """A node a write site can belong to (B.2).

    `tool` by default, because that is what a component that writes a
    store usually is once it is classified; `kind` is a parameter so a
    test can use an `entry_point` (the ingest-route shape) or a `sink`.
    """
    return Node(
        id=node_id or f"{kind}:{name}",
        kind=kind,
        name=name,
        host_id="localhost",
        attributes={"source_file": file_path, **attrs},
    )


def _symbols(
    file_path: str,
    *entries: tuple[str, str | None],
) -> list[TSASymbol]:
    """TSA symbols for one file: `(name, class_name)` pairs.

    A `class_name` makes the symbol a method, which is what
    `_writing_symbols` reads to find the enclosing class.
    """
    return [
        TSASymbol(
            name=name,
            kind="method" if class_name else "function",
            file_path=file_path,
            line_start=index + 1,
            line_end=index + 2,
            class_name=class_name,
        )
        for index, (name, class_name) in enumerate(entries)
    ]


def _caps(file_path: str, **symbol_caps: set[str]) -> FileCapabilities:
    """`FileCapabilities` for one file, from `symbol=capabilities`."""
    return FileCapabilities(
        file_path=file_path,
        symbols=[
            SymbolCapabilities(
                symbol_name=name, file_path=file_path, capabilities=caps,
            )
            for name, caps in symbol_caps.items()
        ],
    )


def _imports(**by_file: list[str]) -> EntryPointOutput:
    """An `EntryPointOutput` carrying only an import table (B.2)."""
    return EntryPointOutput(imports=by_file)


def test_a_server_in_the_same_file_writes_the_catalogue(project) -> None:
    """A2 — the detector-server shape: server and read in one module.

    B.3b-mcp-catalogue: the server writes the catalogue *store* its own
    name fixes, and one `reads_from` points back at the read site.
    """
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server(
        "knowledge", "app.py", node_id="mcp_server:k",
    ))
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p, "mcp_catalogue", "knowledge")
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "mcp_server:k", store.id),
        ("reads_from", store.id, "untrusted_source:1"),
    ]
    assert {e.data_flow_confirmed for e in delta.edges} == {None}


def test_the_stage_states_a_structural_kind_for_both_edges(project) -> None:
    """`FlowKindStage` leaves them alone; the writer stage states the kind.

    `structural`, not `control`: the edges are declarations about a place,
    and the producer traced no value into the source (ADR-0021 §4;
    ADR-0016, decision 3). The stage that emits them states the kind —
    `find_chains` never traverses them — so stage 12 skips them rather
    than pinning a kind of its own on them.
    """
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("knowledge", "app.py"))
    p.delta.nodes.append(_source(file_path="app.py"))

    UntrustedSourceWriterStage().apply(p.delta, p.ctx)
    FlowKindStage().apply(p.delta, p.ctx)

    assert [e.flow_kind for e in p.delta.edges] == [
        "structural", "structural",
    ]


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
    """A3 — the server *is* the `mcp/servers/<name>/` directory (stage 1).

    A path-heuristic node carries no file of its own, so the source's
    file is matched against the directory the path names — the same rule
    `McpServerNodeStage` used to create the node — and the store's hint is
    the directory name that created it.

    The pair carries no code-context signal, because the server names no
    file: `_link_confidence` combines only the base and the literal, so
    the link is `1 − 0.5 × 0.4 = 0.8` rather than the same-module 0.84.
    """
    p = project(files={"mcp/servers/knowledge/tools.py": ""})
    p.delta.nodes.append(_server("knowledge", server_name="knowledge"))
    p.delta.nodes.append(
        _source(file_path="mcp/servers/knowledge/tools.py")
    )

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p, "mcp_catalogue", "knowledge")
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "mcp_server:knowledge", store.id),
        ("reads_from", store.id, "untrusted_source:1"),
    ]
    assert [e.identity_confidence for e in delta.edges] == [
        combine_signals(CATALOGUE_WRITER_BASE, [LITERAL_EQUAL]),
    ] * 2


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
    """A1 — `parent_server` names the server, whatever file the source is in.

    The convention is `mcp_entry_synthesis`'s: a detector that reads a
    declaration records the server it belongs to. No producer sets it on
    an `untrusted_source` yet — 1.3a anchors at the read site — so this
    pins the hook, not a current corpus shape. The store the server
    writes is its own name, and the anchor *replaces* the file rules, so
    the server being in another file changes nothing.
    """
    p = project(files={"app.py": "", "other.py": ""})
    p.delta.nodes.append(_server("knowledge", "other.py"))
    p.delta.nodes.append(
        _source(file_path="app.py", parent_server="knowledge")
    )

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p, "mcp_catalogue", "knowledge")
    assert [e.source_id for e in delta.edges] == [
        "mcp_server:knowledge", store.id,
    ]


def test_a_parent_server_that_names_no_server_writes_nothing(project) -> None:
    """An unresolvable parent is not a licence to match any server."""
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("knowledge", "app.py"))
    p.delta.nodes.append(
        _source(file_path="app.py", parent_server="other")
    )

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_a_server_is_not_a_writer_of_a_corpus(project) -> None:
    """The server anchors are `mcp_tool_description`'s alone (B.2).

    A corpus read inside a server file is the server *reading* its
    store, not writing it: `vectorstore.similarity_search` in
    `mcp/servers/knowledge/server.py` (the pipeline fixture) is exactly
    that. A corpus is written by a *call*, so the only writer a corpus
    source can have is a component that credits `writes_rag_corpus` —
    and a server is not one, whatever file the read is in.
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
    """Two servers, one file each; only the visible one writes.

    One store, because one server is the writer: the invisible server
    names no catalogue this source reads, so §5 gives it no store either.
    """
    p = project(files={"app.py": "", "other.py": ""})
    p.delta.nodes.append(_server("one", "app.py", node_id="mcp_server:1"))
    p.delta.nodes.append(_server("two", "other.py", node_id="mcp_server:2"))
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    writes = [e for e in delta.edges if e.kind == "writes_to"]
    assert [e.source_id for e in writes] == ["mcp_server:1"]
    assert [n.name for n in p.nodes("store")] == [
        "mcp_catalogue:one",
    ]


def test_two_servers_in_one_file_both_write(project) -> None:
    """M1 — two declarations in one module are two writers, two stores.

    The writers come in id order, each one writes the store its own name
    fixes, and *both* stores get a `reads_from` back to the one source —
    the multi-anchor union B.2 already had, multiplied by the store model.
    """
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("one", "app.py", node_id="mcp_server:1"))
    p.delta.nodes.append(_server("two", "app.py", node_id="mcp_server:2"))
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    one = _named_store(p, "mcp_catalogue", "one")
    two = _named_store(p, "mcp_catalogue", "two")
    assert one.id != two.id
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "mcp_server:1", one.id),
        ("reads_from", one.id, "untrusted_source:1"),
        ("writes_to", "mcp_server:2", two.id),
        ("reads_from", two.id, "untrusted_source:1"),
    ]


def test_one_edge_per_pair_even_for_repeated_sources(project) -> None:
    """The edge ids are content-derived, so a repeat is not a second edge.

    Two source nodes with one id are one read site twice over: one
    `writes_to` and one `reads_from`, not two of either.
    """
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("knowledge", "app.py"))
    p.delta.nodes.append(_source(file_path="app.py"))
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [e.kind for e in delta.edges] == ["writes_to", "reads_from"]


def test_no_server_nodes_means_no_work(project) -> None:
    """A scan with no `mcp_server` node emits nothing."""
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []


# ============================================================
# The literal factory path (1.3a.1.2, project-wide in 1.3a.1.3)
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
    """A4 — FinBot's `_SERVER_INTROSPECTORS`: the route file names its servers.

    No server is visible from the route file — none is in it, and its
    path is not under `mcp/servers/` — so the literal is the anchor. The
    named server's own module lives elsewhere, which is the point, and
    each named server gets the store its name fixes.

    Neither server node names a file, so neither pair carries a
    code-context signal: `1 − 0.5 × 0.4 = 0.8` on all four edges.
    """
    p = project(files={
        "apps/routes/api.py": INTROSPECTORS,
        **_server_modules("finmail", "findrive"),
    })
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_server("findrive", server_name="findrive"))
    p.delta.nodes.append(_source(file_path="apps/routes/api.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    finmail = _named_store(p, "mcp_catalogue", "finmail")
    findrive = _named_store(p, "mcp_catalogue", "findrive")
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "mcp_server:findrive", findrive.id),
        ("reads_from", findrive.id, "untrusted_source:1"),
        ("writes_to", "mcp_server:finmail", finmail.id),
        ("reads_from", finmail.id, "untrusted_source:1"),
    ]
    assert {e.flow_kind for e in delta.edges} == {"structural"}
    assert {e.data_flow_confirmed for e in delta.edges} == {None}
    assert {e.identity_confidence for e in delta.edges} == {
        combine_signals(CATALOGUE_WRITER_BASE, [LITERAL_EQUAL]),
    }


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

    assert [
        e.source_id for e in delta.edges if e.kind == "writes_to"
    ] == ["mcp_server:finmail"]
    assert [n.name for n in p.nodes("store")] == ["mcp_catalogue:finmail"]


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
    assert p.nodes("store") == []



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

    assert [
        e.source_id for e in delta.edges if e.kind == "writes_to"
    ] == ["mcp_server:findrive"]


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
    """A literal names a server, so it anchors a catalogue, not a corpus.

    The family decides which anchors apply: a factory path is evidence
    about a *server declaration*, and a corpus is written by a call
    (B.2) — the literal is not a write site, in this file or any other.
    """
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

    The anchors are independent pieces of evidence, so the writers are a
    union: `knowledge` owns the file, and the literal names `finmail`.
    Two writers, two stores, one source.
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

    assert [
        e.source_id for e in delta.edges if e.kind == "writes_to"
    ] == ["mcp_server:finmail", "mcp_server:knowledge"]
    assert [n.name for n in p.nodes("store")] == [
        "mcp_catalogue:finmail", "mcp_catalogue:knowledge",
    ]


def test_a_literal_in_another_file_names_the_writer(project) -> None:
    """The widening (1.3a.1.3): file A names the server, file B reads it.

    The two halves of ADR-0010's channel need not share a file. FinBot's
    route files carry `_SERVER_INTROSPECTORS` and no reader; its
    `mcp/provider.py` carries the reader and no literal. Read per file,
    neither half sees the other, and the group never forms — the
    1.3a.1.2 measurement. At project scope the literal names the server
    for any catalogue the project reads.
    """
    p = project(files={
        "apps/routes/api.py": "def read():\n    return client.list_tools()\n",
        "config/registry.py": INTROSPECTORS,
        **_server_modules("finmail", "findrive"),
    })
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_server("findrive", server_name="findrive"))
    p.delta.nodes.append(_source(file_path="apps/routes/api.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [
        e.source_id for e in delta.edges if e.kind == "writes_to"
    ] == ["mcp_server:findrive", "mcp_server:finmail"]
    assert {e.flow_kind for e in delta.edges} == {"structural"}
    assert {e.data_flow_confirmed for e in delta.edges} == {None}


def test_a_literal_in_the_same_file_is_one_edge_not_two(project) -> None:
    """The project-wide anchor contains the same-file case (1.3a.1.2).

    One literal is one piece of evidence however many anchors see it, and
    the edge ids are content-derived, so the same-file case emits one pair
    — not one per anchor.
    """
    p = project(files={
        "apps/routes/api.py": INTROSPECTORS,
        **_server_modules("finmail", "findrive"),
    })
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_server("findrive", server_name="findrive"))
    p.delta.nodes.append(_source(file_path="apps/routes/api.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert len(delta.edges) == 4
    assert len({e.id for e in delta.edges}) == 4


def test_two_files_naming_one_server_are_still_one_edge(project) -> None:
    """Provenance is a list, the edge is one: the id dedupes the pair."""
    p = project(files={
        "apps/routes/api.py": "def read():\n    return client.list_tools()\n",
        "config/one.py": INTROSPECTORS,
        "config/two.py": INTROSPECTORS,
        **_server_modules("finmail", "findrive"),
    })
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_server("findrive", server_name="findrive"))
    p.delta.nodes.append(_source(file_path="apps/routes/api.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert len(delta.edges) == 4
    assert [
        e.source_id for e in delta.edges if e.kind == "writes_to"
    ] == ["mcp_server:findrive", "mcp_server:finmail"]


def test_a_project_literal_and_a_structural_anchor_are_unioned(
    project,
) -> None:
    """Two independent anchors on one source: the union, in id order."""
    p = project(files={
        "apps/routes/api.py": "def read():\n    return client.list_tools()\n",
        "config/registry.py": INTROSPECTORS,
        **_server_modules("finmail", "findrive"),
    })
    p.delta.nodes.append(
        _server("knowledge", "apps/routes/api.py", node_id="mcp_server:k")
    )
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_source(file_path="apps/routes/api.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [
        e.source_id for e in delta.edges if e.kind == "writes_to"
    ] == ["mcp_server:finmail", "mcp_server:k"]


def test_max_writer_edges_caps_the_emission(project, caplog) -> None:
    """The bound is on emission, and hitting it is visible at DEBUG.

    Two servers × three sources is six pairs and twelve edges, and the cap
    is two: the stage stops in the middle of the first source rather than
    walking the rest of the product. The pair's two edges are emitted
    together, so a cap of two always leaves a `writes_to` and its
    `reads_from`.
    """
    p = project(files={
        "apps/a.py": "def read():\n    return client.list_tools()\n",
        "apps/b.py": "def read():\n    return client.list_tools()\n",
        "apps/c.py": "def read():\n    return client.list_tools()\n",
        "config/registry.py": INTROSPECTORS,
        **_server_modules("finmail", "findrive"),
    })
    p.delta.nodes.append(_server("finmail", server_name="finmail"))
    p.delta.nodes.append(_server("findrive", server_name="findrive"))
    for index, path in enumerate(("apps/a.py", "apps/b.py", "apps/c.py")):
        p.delta.nodes.append(_source(
            file_path=path, node_id=f"untrusted_source:{index}",
        ))

    with caplog.at_level("DEBUG"):
        delta = UntrustedSourceWriterStage(max_writer_edges=2).apply(
            p.delta, p.ctx
        )

    assert [e.kind for e in delta.edges] == ["writes_to", "reads_from"]
    assert delta.edges[0].source_id == "mcp_server:findrive"
    assert "max_writer_edges" in caplog.text


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

    assert declared_server_names(module) == ["finmail"]


# ------------------------------------------------------------
# B.2 — the call-written families: rag_corpus and email_inbox
# ------------------------------------------------------------
# The writer of these families is a component that calls the store, so
# the tests build the two inputs the stage reads: the capability the
# extractor credited (`ctx.capabilities`) and the node the delta holds
# for the symbol that credited it. The rules themselves are the
# extractor's tests' subject (`tests/test_capability_extractor.py`):
# here the capability is the given, because that is what the stage sees.

CORPUS_READ = "vectorstore.similarity_search"
MAIL_READ = "imap.search"


def _corpus_source(file_path: str, **kwargs) -> Node:
    return _source(
        source_type="rag_corpus",
        file_path=file_path,
        call_target=CORPUS_READ,
        **kwargs,
    )


def _mail_source(file_path: str, **kwargs) -> Node:
    return _source(
        source_type="email_inbox",
        file_path=file_path,
        call_target=MAIL_READ,
        **kwargs,
    )


def _named_store(
    p,
    family: str = "rag_corpus",
    hint: str = CORPUS_READ,
) -> Node:
    """The `store` node the stage built for one identity.

    Looked up by the name a store carries (`<family>:<identity_hint>`,
    ADR-0021 §1) rather than by position, so the assertions below are
    about the identity the graph holds. The kind is part of the lookup
    because the two names collide for a call-written family: a source is
    named `<source_type>:<call_target>` (1.3a) and the store's two halves
    *are* the source type and the call target, so the two nodes render the
    same string while their ids differ. `p.named` alone would find the
    source. For the declaration-written family the name is
    `mcp_catalogue:<server name>`, which no source renders.
    """
    name = f"{family}:{hint}"
    return next(
        node for node in p.delta.nodes
        if node.kind == "store" and node.name == name
    )


def test_a_corpus_write_in_the_reads_file_writes_the_store(project) -> None:
    """R1 — `vectorstore.add_documents` in the file the read is in.

    The component that writes and the read are in one module, which is
    the shape an ingest route and its retriever have: the writer is the
    node the delta holds for the symbol that credited the capability,
    and B.3c makes it write into the store the read site names rather
    than into the read site itself.
    """
    p = project(
        files={"app.py": ""},
        capabilities={
            "app.py": _caps("app.py", **{"index_documents": {
                "writes_rag_corpus",
            }}),
        },
    )
    p.delta.nodes.append(_component("index_documents", "app.py"))
    p.delta.nodes.append(_corpus_source("app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p)
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "tool:index_documents", store.id),
        ("reads_from", store.id, "untrusted_source:1"),
    ]


def test_a_corpus_write_in_an_imported_file_writes_the_store(
    project,
) -> None:
    """R2 — the writer imports the reader's module.

    The two halves of the channel need not share a file: the service
    that writes the store imports the module the read is in, which is
    the trailing-segment match `cross_file._module_matches_file` makes
    (B.1's rule).
    """
    p = project(
        files={"apps/routes.py": "", "services/indexer.py": ""},
        capabilities={
            "services/indexer.py": _caps(
                "services/indexer.py", **{"index_documents": {
                    "writes_rag_corpus",
                }},
            ),
        },
        entry_points=_imports(**{"services/indexer.py": ["apps.routes"]}),
    )
    p.delta.nodes.append(_component("index_documents", "services/indexer.py"))
    p.delta.nodes.append(_corpus_source("apps/routes.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p)
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "tool:index_documents", store.id),
        ("reads_from", store.id, "untrusted_source:1"),
    ]


def test_a_corpus_write_the_reader_imports_writes_the_store(
    project,
) -> None:
    """R2, the other direction: the reader imports the writer's module.

    A route module that imports the ingest service is the same channel
    as an ingest service that imports the store module, so both
    directions of the import are checked (`_shares_an_import`).
    """
    p = project(
        files={"apps/routes.py": "", "services/indexer.py": ""},
        capabilities={
            "services/indexer.py": _caps(
                "services/indexer.py", **{"index_documents": {
                    "writes_rag_corpus",
                }},
            ),
        },
        entry_points=_imports(**{"apps/routes.py": ["services.indexer"]}),
    )
    p.delta.nodes.append(_component("index_documents", "services/indexer.py"))
    p.delta.nodes.append(_corpus_source("apps/routes.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p)
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "tool:index_documents", store.id),
        ("reads_from", store.id, "untrusted_source:1"),
    ]


def test_a_corpus_write_in_an_unrelated_file_writes_nothing(project) -> None:
    """R3 — a writer, but no read of the same source it can see.

    The write is in one file and the corpus read in another, and neither
    imports the other: the writer cannot name the read site, so there is
    no edge — the same honest absence `_writers_for`'s third anchor
    produces for a server. No store either: the store is created after
    the writers are found (ADR-0021 §5), and a store nothing writes into
    is not evidence of a channel.
    """
    p = project(
        files={"app.py": "", "other.py": ""},
        capabilities={
            "other.py": _caps("other.py", **{"index_documents": {
                "writes_rag_corpus",
            }}),
        },
    )
    p.delta.nodes.append(_component("index_documents", "other.py"))
    p.delta.nodes.append(_corpus_source("app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []
    assert p.nodes("store") == []


def test_a_corpus_reader_with_no_write_site_writes_nothing(project) -> None:
    """R4 — the read half alone is not a write.

    The unchanged 1.3a result: a corpus read with no component that
    credits a write capability produces no `writes_to` edge. Nothing in
    the file is a writer, so nothing is matched.
    """
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_component("read_documents", "app.py"))
    p.delta.nodes.append(_corpus_source("app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []
    assert p.nodes("store") == []


def test_a_write_of_the_other_family_does_not_write_this_source(
    project,
) -> None:
    """The capability names the family, so only the same family pairs.

    A mail send and a corpus read in one file are two channels: the
    writer credits `writes_email_inbox`, the source is a `rag_corpus`,
    and the family gate keeps them apart. This is what two capability
    names buy over one (`writes_untrusted`).
    """
    p = project(
        files={"app.py": ""},
        capabilities={
            "app.py": _caps("app.py", **{"notify": {"writes_email_inbox"}}),
        },
    )
    p.delta.nodes.append(_component("notify", "app.py"))
    p.delta.nodes.append(_corpus_source("app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_a_mailbox_write_in_the_reads_file_writes_the_store(project) -> None:
    """E1 — `smtp.sendmail` in the file the mailbox read is in."""
    p = project(
        files={"mail.py": ""},
        capabilities={
            "mail.py": _caps("mail.py", **{"send_reply": {
                "writes_email_inbox",
            }}),
        },
    )
    p.delta.nodes.append(_component("send_reply", "mail.py"))
    p.delta.nodes.append(_mail_source("mail.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p, "email_inbox", MAIL_READ)
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "tool:send_reply", store.id),
        ("reads_from", store.id, "untrusted_source:1"),
    ]


def test_a_mailbox_write_in_an_imported_file_writes_the_store(
    project,
) -> None:
    """E2 — the send is in a module the mailbox read's file imports.

    `smtplib.SMTP.send_message` credits `writes_email_inbox` in the
    notification module (the spelling is the extractor's test); the
    stage's question is only whether the two files can see each other.
    """
    p = project(
        files={"apps/inbox.py": "", "notify/mailer.py": ""},
        capabilities={
            "notify/mailer.py": _caps("notify/mailer.py", **{"send": {
                "writes_email_inbox",
            }}),
        },
        entry_points=_imports(**{"apps/inbox.py": ["notify.mailer"]}),
    )
    p.delta.nodes.append(_component("send", "notify/mailer.py"))
    p.delta.nodes.append(_mail_source("apps/inbox.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p, "email_inbox", MAIL_READ)
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "tool:send", store.id),
        ("reads_from", store.id, "untrusted_source:1"),
    ]


def test_a_mailbox_write_in_an_unrelated_file_writes_nothing(
    project,
) -> None:
    """E3 — a writer, but no read of the same source it can see."""
    p = project(
        files={"app.py": "", "other.py": ""},
        capabilities={
            "other.py": _caps("other.py", **{"send": {
                "writes_email_inbox",
            }}),
        },
    )
    p.delta.nodes.append(_component("send", "other.py"))
    p.delta.nodes.append(_mail_source("app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []
    assert p.nodes("store") == []


def test_a_write_in_a_route_handler_is_the_entry_point(project) -> None:
    """The ingest-route shape: the handler's node is the entry point.

    A write inside a route handler has no node of its own — a plain
    handler is not a tool or a sink — so the entry point that carries
    its `handler` name is the writer, exactly as the read stage resolves
    a read inside a handler (1.3a). This is the shape ADR-0010's write
    chain starts from: `POST /documents → … → rag_corpus`.
    """
    p = project(
        files={"apps/routes.py": ""},
        capabilities={
            "apps/routes.py": _caps("apps/routes.py", **{"upload": {
                "writes_rag_corpus",
            }}),
        },
    )
    p.delta.nodes.append(_component(
        "POST /documents", "apps/routes.py",
        node_id="entry_point:upload", kind="entry_point", handler="upload",
    ))
    p.delta.nodes.append(_corpus_source("apps/routes.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p)
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "entry_point:upload", store.id),
        ("reads_from", store.id, "untrusted_source:1"),
    ]


def test_a_write_in_an_unclassified_method_is_its_class(project) -> None:
    """The class is the fallback writer, as it is the read fallback.

    A method that credits the capability but is not classified has no
    node; the class that encloses it does, so the class node is the
    writer — the same "most specific node" rule the read stage applies
    to `reader` and `container`.
    """
    p = project(
        files={"app.py": ""},
        symbols=_symbols("app.py", ("index_documents", "Corpus")),
        capabilities={
            "app.py": _caps("app.py", **{"index_documents": {
                "writes_rag_corpus",
            }}),
        },
    )
    p.delta.nodes.append(_component("Corpus", "app.py", node_id="tool:Corpus"))
    p.delta.nodes.append(_corpus_source("app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p)
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "tool:Corpus", store.id),
        ("reads_from", store.id, "untrusted_source:1"),
    ]


def test_a_write_site_with_no_node_writes_nothing(project) -> None:
    """A component that is not in the graph cannot write into it.

    The symbol credits the capability, but no stage gave it a node: a
    write chain cannot start at a node that does not exist, and this
    stage creates none (its contract is to emit edges between nodes
    other stages created).
    """
    p = project(
        files={"app.py": ""},
        capabilities={
            "app.py": _caps("app.py", **{"index_documents": {
                "writes_rag_corpus",
            }}),
        },
    )
    p.delta.nodes.append(_corpus_source("app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []
    assert p.nodes("store") == []


def test_the_migrated_edges_carry_the_flow_discipline(project) -> None:
    """F1 — every B.3c edge: structural, `["ast"]`, no confirmed data flow.

    The edge is a declaration about a place, not a traced value
    (ADR-0015, decision 5; ADR-0016, decision 3), so `FlowKindStage`
    must leave it alone: `writes_to` and `reads_from` are in
    `STRUCTURAL_EDGE_KINDS`, the stage that emits them states the kind,
    and stage 12 skips a structural edge rather than pinning `control`
    on it. `observed_by` is the `Edge` default and `data_flow_confirmed`
    stays `NULL`. The `control` half of the discipline is the
    declaration-written family's, one test above.
    """
    p = project(
        files={"app.py": ""},
        capabilities={
            "app.py": _caps("app.py", **{"index_documents": {
                "writes_rag_corpus",
            }, "send_reply": {"writes_email_inbox"}}),
        },
    )
    p.delta.nodes.append(_component("index_documents", "app.py"))
    p.delta.nodes.append(_component("send_reply", "app.py"))
    p.delta.nodes.append(_corpus_source("app.py"))
    p.delta.nodes.append(_mail_source("app.py", node_id="untrusted_source:2"))

    UntrustedSourceWriterStage().apply(p.delta, p.ctx)
    FlowKindStage().apply(p.delta, p.ctx)

    assert [e.kind for e in p.delta.edges] == [
        "writes_to", "reads_from", "writes_to", "reads_from",
    ]
    assert {e.flow_kind for e in p.delta.edges} == {"structural"}
    assert {tuple(e.observed_by) for e in p.delta.edges} == {("ast",)}
    assert {e.data_flow_confirmed for e in p.delta.edges} == {None}
    # §7: the two store links carry the field, and only they do.
    assert None not in {e.identity_confidence for e in p.delta.edges}


def test_the_source_family_gate_is_the_writer_table(project) -> None:
    """The gate is derived: two call-written families and one declared.

    `WRITER_SOURCE_TYPES` is `patterns.WRITER_RULES`' families plus the
    declaration-written one, so a family with a writer rule is a family
    this stage matches and `user_upload` (B.3, no rule yet) is not.
    """
    assert WRITER_SOURCE_TYPES == {
        "mcp_tool_description", "rag_corpus", "email_inbox",
    }


def test_a_mailbox_reader_with_no_write_site_writes_nothing(project) -> None:
    """E4 — the read half alone is not a write."""
    p = project(files={"mail.py": ""})
    p.delta.nodes.append(_component("read_inbox", "mail.py"))
    p.delta.nodes.append(_mail_source("mail.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []
    assert p.nodes("store") == []


# ============================================================
# B.3c — the store a call-written source reads from (ADR-0021)
# ============================================================

def test_the_store_identity_is_the_source_type_and_the_call_target(
    project,
) -> None:
    """S1 — `family` is the source type, `identity_hint` the call target.

    ADR-0021 §1: a family is the technology, and for these two the read
    site is what names the place — the corpus is a Chroma or a FAISS
    collection and neither call site says which — so the source type is
    the family here. The hint is the dotted call the read spells, the
    only literal either half of the channel has, and the id is
    `store.resolver.store_node_id`'s, so a store this stage builds and a
    store the resolver would build for one identity are one node.
    """
    p = project(
        files={"app.py": ""},
        capabilities={
            "app.py": _caps("app.py", **{"index_documents": {
                "writes_rag_corpus",
            }}),
        },
    )
    p.delta.nodes.append(_component("index_documents", "app.py"))
    p.delta.nodes.append(_corpus_source("app.py"))

    UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert len(p.nodes("store")) == 1
    store = p.nodes("store")[0]
    assert store.attributes == {
        "family": "rag_corpus", "identity_hint": CORPUS_READ,
    }
    assert store.name == f"rag_corpus:{CORPUS_READ}"
    assert store.id == store_node_id("rag_corpus", CORPUS_READ)


def test_two_reads_of_one_target_share_one_store(project) -> None:
    """S2 — one node per `(family, identity_hint)` (§1).

    Two read sites of one family that spell one target are one place, so
    they share the store the first one created. The sources stay two
    nodes — a source is a read site (1.3a) — which is what makes
    `reads_from` N:1: one store, two read sites, one edge each.
    """
    p = project(
        files={"app.py": "", "other.py": ""},
        capabilities={
            "app.py": _caps("app.py", **{"index_documents": {
                "writes_rag_corpus",
            }}),
            "other.py": _caps("other.py", **{"index_more": {
                "writes_rag_corpus",
            }}),
        },
    )
    p.delta.nodes.append(_component("index_documents", "app.py"))
    p.delta.nodes.append(_component("index_more", "other.py"))
    p.delta.nodes.append(
        _corpus_source("app.py", node_id="untrusted_source:1")
    )
    p.delta.nodes.append(
        _corpus_source("other.py", node_id="untrusted_source:2")
    )

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p)
    assert len(p.nodes("store")) == 1
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "tool:index_documents", store.id),
        ("reads_from", store.id, "untrusted_source:1"),
        ("writes_to", "tool:index_more", store.id),
        ("reads_from", store.id, "untrusted_source:2"),
    ]


def test_two_targets_are_two_stores(project) -> None:
    """S3 — a different call target is a different place, not a wider one.

    `vectorstore.similarity_search` and `retriever.invoke` are two ways
    of reading a corpus, and the AST cannot say they are one collection:
    the identity is the literal, and the literals differ. So two stores,
    one read site each, and nothing pairs one store's write with the
    other's read.
    """
    p = project(
        files={"app.py": "", "other.py": ""},
        capabilities={
            "app.py": _caps("app.py", **{"index_documents": {
                "writes_rag_corpus",
            }}),
            "other.py": _caps("other.py", **{"index_more": {
                "writes_rag_corpus",
            }}),
        },
    )
    p.delta.nodes.append(_component("index_documents", "app.py"))
    p.delta.nodes.append(_component("index_more", "other.py"))
    p.delta.nodes.append(_corpus_source(
        "app.py",
        node_id="untrusted_source:1",
    ))
    p.delta.nodes.append(_source(
        source_type="rag_corpus",
        file_path="other.py",
        node_id="untrusted_source:2",
        call_target="retriever.invoke",
    ))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert len(p.nodes("store")) == 2
    search = _named_store(p)
    invoke = _named_store(p, "rag_corpus", "retriever.invoke")
    assert search.id != invoke.id
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "tool:index_documents", search.id),
        ("reads_from", search.id, "untrusted_source:1"),
        ("writes_to", "tool:index_more", invoke.id),
        ("reads_from", invoke.id, "untrusted_source:2"),
    ]


def test_the_same_module_confidence_is_on_both_edges(project) -> None:
    """C1/C3 — §8's same-module number, and it is the *pair's* number.

    `combine_signals(BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE])` is
    `1 − 0.7 × 0.4 × 0.8 = 0.776`: the family matched, the two ends name
    one literal (the store's hint *is* the call target), and the write
    and the read are in one file. Both edges carry it — the link's
    confidence is the pair's, not one edge's (ADR-0021 §7) — and the
    expected value is computed from `confidence`'s constants rather than
    restated.
    """
    p = project(
        files={"app.py": ""},
        capabilities={
            "app.py": _caps("app.py", **{"index_documents": {
                "writes_rag_corpus",
            }}),
        },
    )
    p.delta.nodes.append(_component("index_documents", "app.py"))
    p.delta.nodes.append(_corpus_source("app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    expected = combine_signals(
        BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
    )
    assert round(expected, 4) == 0.776
    assert [e.identity_confidence for e in delta.edges] == [
        expected, expected,
    ]


def test_a_shared_import_confidence_is_the_shorter_pair(project) -> None:
    """C2 — §8's shared-import number, on both edges.

    The writer is in a module the read's file imports, which is §8's
    weakest code-context row: `1 − 0.7 × 0.4 × 0.9 = 0.748`. The literal
    is still exact, so the pair is weaker than the same-file one and
    stronger than a pair with no code context at all (`0.72`) — the
    arithmetic says the anchor is worth something and not everything.
    """
    p = project(
        files={"apps/routes.py": "", "services/indexer.py": ""},
        capabilities={
            "services/indexer.py": _caps(
                "services/indexer.py", **{"index_documents": {
                    "writes_rag_corpus",
                }},
            ),
        },
        entry_points=_imports(**{"services/indexer.py": ["apps.routes"]}),
    )
    p.delta.nodes.append(_component("index_documents", "services/indexer.py"))
    p.delta.nodes.append(_corpus_source("apps/routes.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    expected = combine_signals(
        BASE, [LITERAL_EQUAL, CODE_CONTEXT_SHARED_IMPORT]
    )
    assert round(expected, 4) == 0.748
    assert [e.identity_confidence for e in delta.edges] == [
        expected, expected,
    ]


def test_the_reads_from_link_keeps_the_strongest_pairing(project) -> None:
    """S4 — several writers, one `reads_from`, one number on it.

    A (store, source) pair gets one `reads_from` (ADR-0021 §3) while
    every visible writer gets its own `writes_to`, so the read side has
    to choose: it carries the strongest pairing, which is
    `StoreResolver._keep_edge`'s rule for its own links. Here the
    same-file writer scores 0.776 and the shared-import one 0.748, so
    each `writes_to` carries its own value and the `reads_from` carries
    0.776.
    """
    p = project(
        files={"apps/routes.py": "", "services/indexer.py": ""},
        capabilities={
            "apps/routes.py": _caps("apps/routes.py", **{"index_local": {
                "writes_rag_corpus",
            }}),
            "services/indexer.py": _caps(
                "services/indexer.py", **{"index_remote": {
                    "writes_rag_corpus",
                }},
            ),
        },
        entry_points=_imports(**{"services/indexer.py": ["apps.routes"]}),
    )
    p.delta.nodes.append(_component("index_local", "apps/routes.py"))
    p.delta.nodes.append(_component("index_remote", "services/indexer.py"))
    p.delta.nodes.append(_corpus_source("apps/routes.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p)
    same_file = combine_signals(
        BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
    )
    shared_import = combine_signals(
        BASE, [LITERAL_EQUAL, CODE_CONTEXT_SHARED_IMPORT]
    )
    assert [
        (e.kind, e.source_id, e.target_id, e.identity_confidence)
        for e in delta.edges
    ] == [
        ("writes_to", "tool:index_local", store.id, same_file),
        ("writes_to", "tool:index_remote", store.id, shared_import),
        ("reads_from", store.id, "untrusted_source:1", same_file),
    ]



def test_a_source_that_names_no_target_gets_no_store(project) -> None:
    """S5 — §5: an unresolved identity produces no store.

    `call_target` is the identity hint, so a source without one names no
    place, and the stage creates nothing rather than a store with an
    empty or invented hint. `untrusted_sources` always writes the
    attribute, so this is the defensive half of the rule — pinned
    because the failure it prevents (a store per unresolved source,
    joining every call site of the family) is the one §5 exists for.
    """
    p = project(
        files={"app.py": ""},
        capabilities={
            "app.py": _caps("app.py", **{"index_documents": {
                "writes_rag_corpus",
            }}),
        },
    )
    p.delta.nodes.append(_component("index_documents", "app.py"))
    source = _corpus_source("app.py")
    del source.attributes["call_target"]
    p.delta.nodes.append(source)

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []
    assert p.nodes("store") == []


def test_a_store_the_delta_already_holds_is_reused(project) -> None:
    """S6 — the delta's stores win over a second node for the same id.

    `_store_index` reads the delta first, the way the upload writer's
    index does: a store is one node per identity (§1) and the id is the
    graph's primary key, so a delta that already holds the node gets it
    reused rather than a duplicate row. A hand-built delta is what
    reaches this today — the resolver builds its own stores at stage 19,
    after this one — and it is pinned because a duplicate id is a write
    the store repository rejects.
    """
    p = project(
        files={"app.py": ""},
        capabilities={
            "app.py": _caps("app.py", **{"index_documents": {
                "writes_rag_corpus",
            }}),
        },
    )
    existing = Node(
        id=store_node_id("rag_corpus", CORPUS_READ),
        kind="store",
        name=f"rag_corpus:{CORPUS_READ}",
        host_id="localhost",
        attributes={"family": "rag_corpus", "identity_hint": CORPUS_READ},
    )
    p.delta.nodes.append(_component("index_documents", "app.py"))
    p.delta.nodes.append(existing)
    p.delta.nodes.append(_corpus_source("app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert p.nodes("store") == [existing]
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "tool:index_documents", existing.id),
        ("reads_from", existing.id, "untrusted_source:1"),
    ]


# ============================================================
# B.3b-mcp-catalogue — the catalogue store (ADR-0021)
# ============================================================

def test_two_reads_of_one_catalogue_share_one_store(project) -> None:
    """M2's N:1: one store, two read sites, one `writes_to`.

    Two catalogue sources written by one server are two read sites (1.3a)
    and one place: the store is created once (§1), the `writes_to` edge
    onto it is one — the id is `(server, store)`, so the second source
    does not emit a second one — and each source keeps its own
    `reads_from`.
    """
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("knowledge", "app.py"))
    p.delta.nodes.append(_source(
        file_path="app.py", node_id="untrusted_source:1",
    ))
    p.delta.nodes.append(_source(
        file_path="app.py", node_id="untrusted_source:2",
        call_target="client.list_tools_async",
    ))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    store = _named_store(p, "mcp_catalogue", "knowledge")
    assert len(p.nodes("store")) == 1
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "mcp_server:knowledge", store.id),
        ("reads_from", store.id, "untrusted_source:1"),
        ("reads_from", store.id, "untrusted_source:2"),
    ]


def test_the_catalogue_store_identity_is_the_family_and_the_server(
    project,
) -> None:
    """M2 — `family` is `mcp_catalogue`, `identity_hint` the server's name.

    ADR-0021 §1: a family is the technology, and an MCP tool catalogue is
    one — the source type stays the *semantic role* on the read site. The
    hint is the writer's name, because the read site (`client.list_tools`)
    names no place at all, and the id is `store.resolver.store_node_id`'s,
    so a store this stage builds and a store the resolver would build for
    one identity are one node.
    """
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server(
        "finmail", "app.py", node_id="mcp_server:f",
    ))
    p.delta.nodes.append(_source(file_path="app.py"))

    UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert len(p.nodes("store")) == 1
    store = p.nodes("store")[0]
    assert store.attributes == {
        "family": CATALOGUE_FAMILY, "identity_hint": "finmail",
    }
    assert store.name == f"{CATALOGUE_FAMILY}:finmail"
    assert store.id == store_node_id(CATALOGUE_FAMILY, "finmail")


def test_the_server_name_attribute_wins_over_the_node_name(project) -> None:
    """`server_name` first, `name` second (`_server_identity`).

    A path-heuristic server carries the directory name in both spellings
    (stage 1), so the order is unobservable on a real scan; a
    detector-created one carries the constructor's argument as its `name`
    and no `server_name` at all. This pins the order for the hand-built
    delta where the two disagree.
    """
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server(
        "knowledge", "app.py", node_id="mcp_server:d",
        server_name="finmail",
    ))
    p.delta.nodes.append(_source(file_path="app.py"))

    UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert [n.name for n in p.nodes("store")] == [
        f"{CATALOGUE_FAMILY}:finmail",
    ]


def test_a_server_with_no_name_writes_no_store_and_no_edge(project) -> None:
    """§5 — an identity nothing can name produces no store.

    A real scan cannot reach this: the anchor returned a server node, so
    something named it. It is pinned because the failure it prevents — a
    store per unnamed server, joining every catalogue read of the family —
    is the one §5 exists for.
    """
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("", "app.py", node_id="mcp_server:x"))
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert delta.edges == []
    assert p.nodes("store") == []


def test_the_catalogue_confidence_is_the_declared_anchor_number(
    project,
) -> None:
    """C1/C3 — §8's same-module number, on both edges of the pair.

    `combine_signals(CATALOGUE_WRITER_BASE, [LITERAL_EQUAL,
    CODE_CONTEXT_SAME_MODULE])` is `1 − 0.5 × 0.4 × 0.8 = 0.84`: the anchor
    names the server explicitly (the 0.5 base, not a call-written family's
    0.3), the store's hint *is* the server's name (the equal literal), and
    the declaration is in the source's file. The expected value is
    computed from `confidence`'s constants rather than restated.
    """
    p = project(files={"app.py": ""})
    p.delta.nodes.append(_server("knowledge", "app.py"))
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    expected = combine_signals(
        CATALOGUE_WRITER_BASE,
        [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE],
    )
    assert round(expected, 4) == 0.84
    assert [e.identity_confidence for e in delta.edges] == [
        expected, expected,
    ]


def test_a_shared_import_confidence_is_the_catalogue_pair(project) -> None:
    """C2 — §8's shared-import number, on both edges.

    The server's module is one the read's file imports, which is §8's
    weakest code-context row: `1 − 0.5 × 0.4 × 0.9 = 0.82`. Above the
    call-written families' 0.776 / 0.748, which is the point of the 0.5
    base — the declaration names the server, and the anchor is explicit.
    """
    p = project(
        files={"apps/routes.py": "", "services/server.py": ""},
        entry_points=_imports(**{"services/server.py": ["apps.routes"]}),
    )
    p.delta.nodes.append(_server(
        "finmail", "services/server.py", node_id="mcp_server:f",
    ))
    p.delta.nodes.append(_source(
        file_path="apps/routes.py", parent_server="finmail",
    ))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    expected = combine_signals(
        CATALOGUE_WRITER_BASE,
        [LITERAL_EQUAL, CODE_CONTEXT_SHARED_IMPORT],
    )
    assert round(expected, 4) == 0.82
    assert [e.identity_confidence for e in delta.edges] == [
        expected, expected,
    ]


def test_a_catalogue_store_the_delta_already_holds_is_reused(project) -> None:
    """S6's rule for this family: the delta's store wins over a new node.

    `_store_index` reads the delta first, the way the upload writer's index
    does: a store is one node per identity (§1) and the id is the graph's
    primary key, so a delta that already holds the node gets it reused
    rather than a duplicate row.
    """
    p = project(files={"app.py": ""})
    existing = Node(
        id=store_node_id(CATALOGUE_FAMILY, "finmail"),
        kind="store",
        name=f"{CATALOGUE_FAMILY}:finmail",
        host_id="localhost",
        attributes={
            "family": CATALOGUE_FAMILY, "identity_hint": "finmail",
        },
    )
    p.delta.nodes.append(_server("finmail", "app.py", node_id="mcp_server:f"))
    p.delta.nodes.append(existing)
    p.delta.nodes.append(_source(file_path="app.py"))

    delta = UntrustedSourceWriterStage().apply(p.delta, p.ctx)

    assert p.nodes("store") == [existing]
    assert [
        (e.kind, e.source_id, e.target_id) for e in delta.edges
    ] == [
        ("writes_to", "mcp_server:f", existing.id),
        ("reads_from", existing.id, "untrusted_source:1"),
    ]

