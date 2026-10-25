"""DynamicMcpInvocationStage: `uses_tool` for a catalogue reader (1.3a.1.2).

The stage closes the hop the read side cannot see: an agent calls
`call_tool(name)` with a runtime name, so no static edge exists from the
agent to any tool. The rule is the only evidence available — a reader of
a catalogue can invoke every tool the catalogue describes — and these
tests pin it, the gates around it, and the flow attributes:

    source → agent            the agent's own read (1.3a)
    server → source           the writer's declaration (1.3a.1)
    server → tool             what the catalogue contains

The two absences that matter are both checked: a catalogue no server
writes, and a server that exposes no tool.
"""

from __future__ import annotations

from aicorellator.graph.models import Edge, Node
from aicorellator.parsing.normalizer_stages import (
    DynamicMcpInvocationStage,
)


def _agent(
    name: str = "MCPToolProvider",
    *,
    capabilities: list[str] | None = None,
) -> Node:
    return Node(
        id=f"agent:{name}",
        kind="agent",
        name=name,
        host_id="localhost",
        capabilities=(
            ["reads_untrusted"] if capabilities is None else capabilities
        ),
    )


def _catalogue(
    source_type: str = "mcp_tool_description",
    *,
    node_id: str = "untrusted_source:1",
) -> Node:
    return Node(
        id=node_id,
        kind="untrusted_source",
        name=f"{source_type}:client.list_tools",
        host_id="localhost",
        attributes={"source_type": source_type},
    )


def _tool(name: str) -> Node:
    return Node(
        id=f"tool:{name}",
        kind="tool",
        name=name,
        host_id="localhost",
    )


def _server(name: str) -> Node:
    return Node(
        id=f"mcp_server:{name}",
        kind="mcp_server",
        name=name,
        host_id="localhost",
    )


def _edge(source: str, target: str, kind: str) -> Edge:
    return Edge(
        id=f"edge:{source}:{target}:{kind}",
        source_id=source,
        target_id=target,
        kind=kind,
        host_id="localhost",
        flow_kind="control",
    )


def _graph(project):
    return project(files={"app.py": ""})


def _uses_tool(delta) -> list[Edge]:
    return [e for e in delta.edges if e.kind == "uses_tool"]


def test_a_reader_invokes_every_tool_of_the_servers_that_write_it(
    project,
) -> None:
    """One catalogue, one writer, two tools: the agent gets both."""
    p = _graph(project)
    p.delta.nodes.extend([
        _agent(), _catalogue(), _server("knowledge"),
        _tool("read_file"), _tool("delete_file"),
    ])
    p.delta.edges.extend([
        _edge("untrusted_source:1", "agent:MCPToolProvider", "has_access_to"),
        _edge("mcp_server:knowledge", "untrusted_source:1", "writes_to"),
        _edge("mcp_server:knowledge", "tool:read_file", "provides_tool"),
        _edge("mcp_server:knowledge", "tool:delete_file", "provides_tool"),
    ])

    delta = DynamicMcpInvocationStage().apply(p.delta, p.ctx)

    assert [
        (e.source_id, e.target_id) for e in _uses_tool(delta)
    ] == [
        ("agent:MCPToolProvider", "tool:delete_file"),
        ("agent:MCPToolProvider", "tool:read_file"),
    ]


def test_every_emitted_edge_is_control_with_unconfirmed_data_flow(
    project,
) -> None:
    """Access path, not a traced flow (ADR-0015 decision 5)."""
    p = _graph(project)
    p.delta.nodes.extend([
        _agent(), _catalogue(), _server("knowledge"), _tool("read_file"),
    ])
    p.delta.edges.extend([
        _edge("untrusted_source:1", "agent:MCPToolProvider", "has_access_to"),
        _edge("mcp_server:knowledge", "untrusted_source:1", "writes_to"),
        _edge("mcp_server:knowledge", "tool:read_file", "provides_tool"),
    ])

    delta = DynamicMcpInvocationStage().apply(p.delta, p.ctx)

    assert [e.flow_kind for e in _uses_tool(delta)] == ["control"]
    assert [e.data_flow_confirmed for e in _uses_tool(delta)] == [None]


def test_an_agent_without_the_capability_gets_no_edges(project) -> None:
    """The gate is `reads_untrusted`, not the presence of a read edge."""
    p = _graph(project)
    p.delta.nodes.extend([
        _agent(capabilities=["invokes_mcp_tool"]),
        _catalogue(), _server("knowledge"), _tool("read_file"),
    ])
    p.delta.edges.extend([
        _edge("untrusted_source:1", "agent:MCPToolProvider", "has_access_to"),
        _edge("mcp_server:knowledge", "untrusted_source:1", "writes_to"),
        _edge("mcp_server:knowledge", "tool:read_file", "provides_tool"),
    ])

    delta = DynamicMcpInvocationStage().apply(p.delta, p.ctx)

    assert _uses_tool(delta) == []


def test_an_agent_that_reads_no_catalogue_gets_no_edges(project) -> None:
    """A `reads_untrusted` agent reading a corpus reads no tools.

    The capability says the agent reads untrusted data; it does not say
    which catalogue. A `rag_corpus` is a different source family and
    names no tools.
    """
    p = _graph(project)
    p.delta.nodes.extend([
        _agent(), _catalogue(source_type="rag_corpus"),
        _server("knowledge"), _tool("read_file"),
    ])
    p.delta.edges.extend([
        _edge("untrusted_source:1", "agent:MCPToolProvider", "has_access_to"),
        _edge("mcp_server:knowledge", "untrusted_source:1", "writes_to"),
        _edge("mcp_server:knowledge", "tool:read_file", "provides_tool"),
    ])

    delta = DynamicMcpInvocationStage().apply(p.delta, p.ctx)

    assert _uses_tool(delta) == []


def test_a_catalogue_no_server_writes_gives_no_edges(project) -> None:
    """No `writes_to` edge means no server, and no server means no tools."""
    p = _graph(project)
    p.delta.nodes.extend([
        _agent(), _catalogue(), _server("knowledge"), _tool("read_file"),
    ])
    p.delta.edges.extend([
        _edge("untrusted_source:1", "agent:MCPToolProvider", "has_access_to"),
        _edge("mcp_server:knowledge", "tool:read_file", "provides_tool"),
    ])

    delta = DynamicMcpInvocationStage().apply(p.delta, p.ctx)

    assert _uses_tool(delta) == []


def test_a_server_with_no_tools_gives_no_edges(project) -> None:
    """A server that exposes nothing invokes nothing."""
    p = _graph(project)
    p.delta.nodes.extend([
        _agent(), _catalogue(), _server("knowledge"),
    ])
    p.delta.edges.extend([
        _edge("untrusted_source:1", "agent:MCPToolProvider", "has_access_to"),
        _edge("mcp_server:knowledge", "untrusted_source:1", "writes_to"),
    ])

    delta = DynamicMcpInvocationStage().apply(p.delta, p.ctx)

    assert _uses_tool(delta) == []


def test_only_the_writers_of_this_agents_catalogue_count(project) -> None:
    """Two servers, two catalogues; the agent reads one of them.

    The rule is per-source, not per-family: an agent linked to one
    server's catalogue is not linked to another server's tools.
    """
    p = _graph(project)
    p.delta.nodes.extend([
        _agent(),
        _catalogue(node_id="untrusted_source:mine"),
        _catalogue(node_id="untrusted_source:other"),
        _server("mine"), _server("other"),
        _tool("mine_tool"), _tool("other_tool"),
    ])
    p.delta.edges.extend([
        _edge(
            "untrusted_source:mine", "agent:MCPToolProvider",
            "has_access_to",
        ),
        _edge("mcp_server:mine", "untrusted_source:mine", "writes_to"),
        _edge("mcp_server:other", "untrusted_source:other", "writes_to"),
        _edge("mcp_server:mine", "tool:mine_tool", "provides_tool"),
        _edge("mcp_server:other", "tool:other_tool", "provides_tool"),
    ])

    delta = DynamicMcpInvocationStage().apply(p.delta, p.ctx)

    assert [
        e.target_id for e in _uses_tool(delta)
    ] == ["tool:mine_tool"]


def test_two_catalogues_two_writers_union_their_tools(project) -> None:
    """Two read edges are two catalogues, and a set union, not a list."""
    p = _graph(project)
    p.delta.nodes.extend([
        _agent(),
        _catalogue(node_id="untrusted_source:a"),
        _catalogue(node_id="untrusted_source:b"),
        _server("a"), _server("b"),
        _tool("shared"), _tool("b_only"),
    ])
    p.delta.edges.extend([
        _edge("untrusted_source:a", "agent:MCPToolProvider", "has_access_to"),
        _edge("untrusted_source:b", "agent:MCPToolProvider", "has_access_to"),
        _edge("mcp_server:a", "untrusted_source:a", "writes_to"),
        _edge("mcp_server:b", "untrusted_source:b", "writes_to"),
        _edge("mcp_server:a", "tool:shared", "provides_tool"),
        _edge("mcp_server:b", "tool:shared", "provides_tool"),
        _edge("mcp_server:b", "tool:b_only", "provides_tool"),
    ])

    delta = DynamicMcpInvocationStage().apply(p.delta, p.ctx)

    assert [
        e.target_id for e in _uses_tool(delta)
    ] == ["tool:b_only", "tool:shared"]


def test_an_edge_the_delta_already_has_is_not_emitted_twice(project) -> None:
    """The edge id is content-derived, so a resolver's edge wins.

    `cross_file_resolution` emits the same kind from an import table;
    the delta is checked by id, and a second run appends nothing.
    """
    p = _graph(project)
    p.delta.nodes.extend([
        _agent(), _catalogue(), _server("knowledge"), _tool("read_file"),
    ])
    p.delta.edges.extend([
        _edge("untrusted_source:1", "agent:MCPToolProvider", "has_access_to"),
        _edge("mcp_server:knowledge", "untrusted_source:1", "writes_to"),
        _edge("mcp_server:knowledge", "tool:read_file", "provides_tool"),
    ])

    DynamicMcpInvocationStage().apply(p.delta, p.ctx)
    DynamicMcpInvocationStage().apply(p.delta, p.ctx)

    assert len(_uses_tool(p.delta)) == 1


def test_the_stage_creates_no_nodes(project) -> None:
    """It reads the graph; it does not build it."""
    p = _graph(project)
    p.delta.nodes.extend([
        _agent(), _catalogue(), _server("knowledge"), _tool("read_file"),
    ])
    p.delta.edges.extend([
        _edge("untrusted_source:1", "agent:MCPToolProvider", "has_access_to"),
        _edge("mcp_server:knowledge", "untrusted_source:1", "writes_to"),
        _edge("mcp_server:knowledge", "tool:read_file", "provides_tool"),
    ])
    before = len(p.delta.nodes)

    DynamicMcpInvocationStage().apply(p.delta, p.ctx)

    assert len(p.delta.nodes) == before
