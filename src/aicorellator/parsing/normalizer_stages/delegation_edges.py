"""DelegationEdgeStage — `delegates_to` and agent → server.

Run order: 8 of 16 (see `normalizer_stages.STAGES`).

C4: "**Delegation edges** — entry point to agent, agent to agent, agent
to MCP server."

Three shapes, one question — who hands control to whom:

    entry_point → agent        a route handler calls an agent
    agent → agent              an orchestrator delegates to a worker
    agent → mcp_server         an agent module holds an MCP client

The agent indexes are built here (they need every agent node, which the
class- and method-node stages produced) and stored on the context: later
stages and the MCP entry synthesis read them.

Side effects: fills `ctx.agent_node_by_name` and `ctx.agent_node_by_file`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Edge
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import CONTROL_FLOW, edge_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class DelegationEdgeStage:
    """`delegates_to` (entry → agent, agent → agent) and agent → server."""

    name = "delegation_edges"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        ctx.agent_node_by_name = {
            n.name: n.id for n in delta.nodes if n.kind == "agent"
        }
        ctx.agent_node_by_file = {
            n.attributes.get("source_file", ""): n.id
            for n in delta.nodes
            if n.kind == "agent"
        }

        self._entry_to_agent(delta, ctx)
        self._agent_to_agent(delta, ctx)
        self._agent_to_mcp_server(delta, ctx)
        return delta

    @staticmethod
    def _entry_to_agent(
        delta: "GraphDelta", ctx: NormalizeContext
    ) -> None:
        for call in ctx.entry_points.agent_calls:
            if call.is_agent_to_agent:
                continue
            entry_id = ctx.entry_node_by_handler.get(call.handler)
            agent_id = ctx.agent_node_by_name.get(call.agent_name)
            if entry_id is None or agent_id is None:
                continue
            delta.edges.append(Edge(
                id=edge_id(entry_id, agent_id, "delegates_to"),
                source_id=entry_id,
                target_id=agent_id,
                kind="delegates_to",
                host_id=ctx.host_id,
                **CONTROL_FLOW,
            ))

    @staticmethod
    def _agent_to_agent(
        delta: "GraphDelta", ctx: NormalizeContext
    ) -> None:
        for call in ctx.entry_points.agent_calls:
            if not call.is_agent_to_agent:
                continue
            source_id = ctx.agent_node_by_file.get(call.file_path)
            target_id = ctx.agent_node_by_name.get(call.agent_name)
            if source_id is None or target_id is None:
                continue
            if source_id == target_id:
                continue
            delta.edges.append(Edge(
                id=edge_id(source_id, target_id, "delegates_to"),
                source_id=source_id,
                target_id=target_id,
                kind="delegates_to",
                host_id=ctx.host_id,
                **CONTROL_FLOW,
            ))

    @staticmethod
    def _agent_to_mcp_server(
        delta: "GraphDelta", ctx: NormalizeContext
    ) -> None:
        for usage in ctx.entry_points.mcp_usages:
            if not usage.is_agent_module:
                continue
            agent_id = ctx.agent_node_by_file.get(usage.file_path)
            server_id = ctx.mcp_servers.get(usage.mcp_server_name)
            if agent_id is None or server_id is None:
                continue
            delta.edges.append(Edge(
                id=edge_id(agent_id, server_id, "has_access_to"),
                source_id=agent_id,
                target_id=server_id,
                kind="has_access_to",
                host_id=ctx.host_id,
                # Agent → MCP server is reachability, not a call into a
                # sink (ADR-0015): there is no tool body and no sink
                # call to analyze, so data flow stays unconfirmed.
                **CONTROL_FLOW,
            ))
