"""McpServerNodeStage — `mcp_server` nodes from file paths.

Run order: 1 of 20 (see `normalizer_stages.STAGES`).

C4: "**MCP server nodes** — from file paths under `mcp/servers/`." The
detector-discovered servers (`FastMCP(...)`, `Server(...)`) arrive with
the detector merge instead; this stage covers the convention-based
layout, where the directory name is the server name.

Side effects: fills `ctx.mcp_servers` (server name → node id), which the
`provides_tool` edges and the agent → server edges read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Node
from ..classification import classify_file
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import HOST_ID, mcp_server_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class McpServerNodeStage:
    """One `mcp_server` node per `mcp/servers/<name>/server.py` path."""

    name = "mcp_server_nodes"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        for file_entry in ctx.manifest.files:
            c = classify_file(file_entry.path)
            if c is None:
                continue
            server_name = c.attributes["server_name"]
            if server_name in ctx.mcp_servers:
                continue
            node_id = mcp_server_id(server_name)
            ctx.mcp_servers[server_name] = node_id
            delta.nodes.append(Node(
                id=node_id,
                kind="mcp_server",
                name=server_name,
                host_id=HOST_ID,
                attributes={"server_name": server_name},
                content_hash=None,
            ))
        return delta
