"""DelegationEdgeStage: entry → agent, agent → agent, agent → server."""

from __future__ import annotations

from aicorellator.graph.models import Node
from aicorellator.parsing.entry_points import (
    AgentCall,
    EntryPoint,
    EntryPointOutput,
    MCPUsage,
)
from aicorellator.parsing.normalizer_stages import DelegationEdgeStage


def _agent(node_id: str, name: str, file_path: str) -> Node:
    return Node(
        id=node_id, kind="agent", name=name, host_id="localhost",
        attributes={"source_file": file_path},
    )


def test_all_three_delegation_shapes(project) -> None:
    """A route hands to an agent, an orchestrator to a worker, and an
    agent module holds an MCP server."""
    p = project(
        files={"app.py": "", "orchestrator.py": "", "server.py": ""},
        entry_points=EntryPointOutput(
            entry_points=[EntryPoint(
                method="POST", path="/invoices", handler="create_invoice",
                file_path="app.py", line=10,
            )],
            agent_calls=[
                AgentCall(
                    handler="create_invoice", agent_name="OrchestratorAgent",
                    file_path="app.py", line=12,
                ),
                AgentCall(
                    handler="run", agent_name="InvoiceAgent",
                    file_path="orchestrator.py", line=30,
                    is_agent_to_agent=True,
                ),
            ],
            mcp_usages=[MCPUsage(
                handler="run", mcp_server_name="findrive",
                file_path="orchestrator.py", line=31, is_agent_module=True,
            )],
        ),
    )
    p.delta.nodes.extend([
        _agent("agent:orch", "OrchestratorAgent", "orchestrator.py"),
        _agent("agent:invoice", "InvoiceAgent", "agents/invoice.py"),
    ])
    p.ctx.entry_node_by_handler = {"create_invoice": "entry:1"}
    p.ctx.mcp_servers = {"findrive": "mcp_server:1"}

    delta = DelegationEdgeStage().apply(p.delta, p.ctx)

    assert {(e.kind, e.source_id, e.target_id) for e in delta.edges} == {
        ("delegates_to", "entry:1", "agent:orch"),
        ("delegates_to", "agent:orch", "agent:invoice"),
        ("has_access_to", "agent:orch", "mcp_server:1"),
    }
    # The indexes later stages read.
    assert p.ctx.agent_node_by_name["InvoiceAgent"] == "agent:invoice"
    assert p.ctx.agent_node_by_file["orchestrator.py"] == "agent:orch"
