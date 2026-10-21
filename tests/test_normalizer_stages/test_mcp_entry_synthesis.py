"""McpEntrySynthesisStage: a virtual entry point per detected server."""

from __future__ import annotations

from aicorellator.graph.models import Node
from aicorellator.parsing.normalizer_stages import McpEntrySynthesisStage


def test_virtual_mcp_entry_links_every_tool_of_the_server(project) -> None:
    """The client can invoke any tool the server exposes (Resolver 3)."""
    p = project(files={"server.py": ""})
    p.delta.nodes.extend([
        Node(
            id="mcp_server:1", kind="mcp_server", name="srv",
            host_id="localhost",
            attributes={"source_file": "server.py", "framework": "fastmcp"},
        ),
        Node(
            id="tool:1", kind="tool", name="search", host_id="localhost",
            attributes={
                "source_file": "server.py", "parent_server": "srv",
                "tool_data_flow": "parameter_match",
            },
        ),
        Node(
            id="tool:2", kind="tool", name="health", host_id="localhost",
            attributes={
                "source_file": "server.py", "parent_server": "srv",
                "tool_data_flow": "no_args",
            },
        ),
    ])

    delta = McpEntrySynthesisStage().apply(p.delta, p.ctx)

    entries = [n for n in delta.nodes if n.kind == "entry_point"]
    assert len(entries) == 1
    assert entries[0].name == "MCP:srv"
    assert entries[0].attributes["entry_kind"] == "mcp"

    by_target = {
        e.target_id: e for e in delta.edges if e.kind == "delegates_to"
    }
    # The 1.2a.2 enrichment travels with the tool (step 3's rule).
    assert by_target["tool:1"].flow_kind == "data"
    assert by_target["tool:1"].data_flow_confirmed == "parameter_match"
    assert by_target["tool:2"].flow_kind == "control"
    assert by_target["tool:2"].data_flow_confirmed == "no_args"
