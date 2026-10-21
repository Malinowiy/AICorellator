"""McpServerNodeStage: one `mcp_server` node per server directory."""

from __future__ import annotations

from aicorellator.parsing.normalizer_stages import McpServerNodeStage


def test_mcp_server_node_from_path(project) -> None:
    """The directory name under `mcp/servers/` is the server name."""
    p = project(files={"mcp/servers/findrive/server.py": ""})

    delta = McpServerNodeStage().apply(p.delta, p.ctx)

    assert [(n.kind, n.name) for n in delta.nodes] == [
        ("mcp_server", "findrive"),
    ]
    # The index later stages read: server name → node id.
    assert p.ctx.mcp_servers == {"findrive": delta.nodes[0].id}
