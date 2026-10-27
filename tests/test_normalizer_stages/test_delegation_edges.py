"""DelegationEdgeStage: entry → agent, agent → agent, agent → server.

Every test here is about *provenance*, not names (ADR-0016, decision 7,
BACKLOG A.3b). The stage links a call site to an agent node that already
exists; a call site whose callee matches no agent node produces no edge,
however agent-shaped the name is.

The files are written to disk because the resolver reads a body one hop
away (`ctx.functions_for`) when the callee is a function defined in
another file.
"""

from __future__ import annotations

from aicorellator.graph.models import Node
from aicorellator.parsing.entry_points import (
    EntryPoint,
    EntryPointOutput,
    MCPUsage,
    SymbolCall,
)
from aicorellator.parsing.models import TSASymbol
from aicorellator.parsing.normalizer_stages import DelegationEdgeStage


def _agent(node_id: str, name: str, file_path: str) -> Node:
    return Node(
        id=node_id, kind="agent", name=name, host_id="localhost",
        attributes={"source_file": file_path},
    )


def _function(name: str, file_path: str, line: int = 1) -> TSASymbol:
    return TSASymbol(
        name=name, kind="function", file_path=file_path,
        line_start=line, line_end=line + 1,
    )


def _method(name: str, file_path: str, class_name: str) -> TSASymbol:
    return TSASymbol(
        name=name, kind="method", file_path=file_path,
        line_start=1, line_end=2, class_name=class_name,
    )


# The route. `run_orchestrator_agent` is imported, so the resolver has to
# follow the import into `agents/runner.py` to find the agent.
API_SOURCE = '''\
from agents.runner import run_orchestrator_agent


@router.post("/chat")
async def chat(request):
    return await run_orchestrator_agent(request)
'''

# The runner. `run_orchestrator_agent` names `OrchestratorAgent` in its
# body — one hop from the route. `run_worker_agent` names `WorkerAgent`,
# one hop from the orchestrator's method.
RUNNER_SOURCE = '''\
from agents.orchestrator import OrchestratorAgent
from agents.worker import WorkerAgent


async def run_orchestrator_agent(request):
    return await OrchestratorAgent().handle(request)


async def run_worker_agent(task):
    return await run_agent_with_retry(WorkerAgent, task)


async def run_agent_with_retry(agent_class, task):
    return await agent_class().process(task)
'''

ORCHESTRATOR_SOURCE = '''\
from agents.runner import run_worker_agent


class OrchestratorAgent:
    async def delegate_to_worker(self, task):
        return await run_worker_agent(task)
'''


def test_all_three_delegation_shapes(project) -> None:
    """A route hands to an agent, an orchestrator to a worker, and an
    agent holds an MCP server — all three resolved from provenance."""
    p = project(
        files={
            "apps/api.py": API_SOURCE,
            "agents/runner.py": RUNNER_SOURCE,
            "agents/orchestrator.py": ORCHESTRATOR_SOURCE,
        },
        symbols=[
            _function("run_orchestrator_agent", "agents/runner.py"),
            _function("run_worker_agent", "agents/runner.py", line=10),
            _function("run_agent_with_retry", "agents/runner.py", line=14),
            _method(
                "delegate_to_worker", "agents/orchestrator.py",
                "OrchestratorAgent",
            ),
        ],
        entry_points=EntryPointOutput(
            entry_points=[EntryPoint(
                method="POST", path="/chat", handler="chat",
                file_path="apps/api.py", line=4,
            )],
            symbol_calls=[
                SymbolCall(
                    handler="chat", callee="run_orchestrator_agent",
                    file_path="apps/api.py", line=6,
                ),
                SymbolCall(
                    handler="delegate_to_worker", callee="run_worker_agent",
                    file_path="agents/orchestrator.py", line=6,
                ),
            ],
            imports={
                "apps/api.py": ["agents.runner"],
                "agents/orchestrator.py": ["agents.runner"],
                # `agents/runner.py` imports both agents it names. The
                # import table is what the project-wide step reads, so a
                # fixture that leaves it out declares a file that does
                # not import what its own source text imports.
                "agents/runner.py": ["agents.orchestrator", "agents.worker"],
            },
            mcp_usages=[MCPUsage(
                handler="delegate_to_worker", mcp_server_name="findrive",
                file_path="agents/orchestrator.py", line=6,
            )],
        ),
    )
    p.delta.nodes.extend([
        _agent("agent:orch", "OrchestratorAgent", "agents/orchestrator.py"),
        _agent("agent:worker", "WorkerAgent", "agents/worker.py"),
    ])
    p.ctx.entry_node_by_handler = {"chat": "entry:1"}
    p.ctx.mcp_servers = {"findrive": "mcp_server:1"}

    delta = DelegationEdgeStage().apply(p.delta, p.ctx)

    assert {(e.kind, e.source_id, e.target_id) for e in delta.edges} == {
        ("delegates_to", "entry:1", "agent:orch"),
        ("delegates_to", "agent:orch", "agent:worker"),
        ("has_access_to", "agent:orch", "mcp_server:1"),
    }
    # The indexes later stages read.
    assert p.ctx.agent_node_by_name["WorkerAgent"] == "agent:worker"
    assert p.ctx.agent_node_by_file["agents/orchestrator.py"] == "agent:orch"


def test_delegates_to_edges_are_control_flow_with_evidence(project) -> None:
    """ADR-0015/ADR-0016: control flow, no data-flow verdict, names only."""
    p = project(
        files={
            "apps/api.py": API_SOURCE,
            "agents/runner.py": RUNNER_SOURCE,
        },
        symbols=[_function("run_orchestrator_agent", "agents/runner.py")],
        entry_points=EntryPointOutput(
            entry_points=[EntryPoint(
                method="POST", path="/chat", handler="chat",
                file_path="apps/api.py", line=4,
            )],
            symbol_calls=[SymbolCall(
                handler="chat", callee="run_orchestrator_agent",
                file_path="apps/api.py", line=6,
            )],
            imports={
                "apps/api.py": ["agents.runner"],
                # RUNNER_SOURCE imports `agents.orchestrator`; the
                # project-wide step reads the import table, so it has to
                # be declared here.
                "agents/runner.py": ["agents.orchestrator"],
            },
        ),
    )
    p.delta.nodes.append(
        _agent("agent:orch", "OrchestratorAgent", "agents/orchestrator.py")
    )
    p.ctx.entry_node_by_handler = {"chat": "entry:1"}

    delta = DelegationEdgeStage().apply(p.delta, p.ctx)

    edge = delta.edges[0]
    assert edge.kind == "delegates_to"
    assert edge.flow_kind == "control"
    assert edge.data_flow_confirmed is None
    assert edge.attributes["resolved_via"] == "call_graph"
    assert edge.attributes["evidence"] == (
        "chat → run_orchestrator_agent → OrchestratorAgent"
    )



# ============================================================
# What does not become an edge
# ============================================================

def test_name_only_lookalike_is_not_an_agent(project) -> None:
    """`run_fake_agent` calling `FakeAgent` is not a delegation.

    The names are agent-shaped and the file is `agents/`; the pre-A.3b
    heuristics matched both. No agent node exists, so there is nothing
    to link to and no edge is emitted.
    """
    p = project(
        files={
            "apps/api.py": '''\
from agents.fake_runner import run_fake_agent


@router.post("/chat")
async def chat(request):
    return await run_fake_agent(request)
''',
            "agents/fake_runner.py": '''\
async def run_fake_agent(request):
    return await FakeAgent().handle(request)
''',
        },
        symbols=[_function("run_fake_agent", "agents/fake_runner.py")],
        entry_points=EntryPointOutput(
            entry_points=[EntryPoint(
                method="POST", path="/chat", handler="chat",
                file_path="apps/api.py", line=4,
            )],
            symbol_calls=[SymbolCall(
                handler="chat", callee="run_fake_agent",
                file_path="apps/api.py", line=6,
            )],
            imports={"apps/api.py": ["agents.fake_runner"]},
        ),
    )
    p.ctx.entry_node_by_handler = {"chat": "entry:1"}

    delta = DelegationEdgeStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_a_name_mentioned_but_not_invoked_is_not_a_delegation(project) -> None:
    """`logger.info("Using %s", InvoiceAgent)` names the agent; it does
    not call it, so hop-1 must not treat the mention as evidence.

    The pre-fix rule read every `ast.Name` in the body, so a name in a
    log message — or any other non-call position — fabricated an edge.

    `services/helpers.py` imports the agent, so the import gate is open
    and this test pins the hop-1 rule alone: nothing but the position of
    the name stands between the mention and the edge.
    """
    p = project(
        files={
            "apps/api.py": '''\
from services.helpers import log_agent


@router.post("/chat")
async def chat(request):
    return log_agent(request)
''',
            "services/helpers.py": '''\
import logging

from agents.invoice import InvoiceAgent

logger = logging.getLogger(__name__)


def log_agent(x):
    logger.info("Using %s", InvoiceAgent)
    return x
''',
        },
        symbols=[_function("log_agent", "services/helpers.py", line=8)],
        entry_points=EntryPointOutput(
            entry_points=[EntryPoint(
                method="POST", path="/chat", handler="chat",
                file_path="apps/api.py", line=4,
            )],
            symbol_calls=[SymbolCall(
                handler="chat", callee="log_agent",
                file_path="apps/api.py", line=6,
            )],
            imports={
                "apps/api.py": ["services.helpers"],
                "services/helpers.py": ["agents.invoice"],
            },
        ),
    )
    p.delta.nodes.append(
        _agent("agent:invoice", "InvoiceAgent", "agents/invoice.py")
    )
    p.ctx.entry_node_by_handler = {"chat": "entry:1"}

    delta = DelegationEdgeStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_agent_not_imported_is_not_a_delegation(project) -> None:
    """`def build(): return WorkerAgent()` in a file that never imports
    WorkerAgent must not delegate to it. The name is project-wide; the
    evidence is not.

    The project-wide fallback used to accept any agent node of that name,
    wherever it lived, so a file that neither defines nor imports the
    agent produced an edge to it.
    """
    p = project(
        files={
            "apps/api.py": '''\
from services.helpers import build


@router.post("/chat")
async def chat(request):
    return build()
''',
            "services/helpers.py": '''\
def build():
    return WorkerAgent()
''',
        },
        symbols=[_function("build", "services/helpers.py")],
        entry_points=EntryPointOutput(
            entry_points=[EntryPoint(
                method="POST", path="/chat", handler="chat",
                file_path="apps/api.py", line=4,
            )],
            symbol_calls=[SymbolCall(
                handler="chat", callee="build",
                file_path="apps/api.py", line=6,
            )],
            imports={"apps/api.py": ["services.helpers"]},
        ),
    )
    p.delta.nodes.append(
        _agent("agent:worker", "WorkerAgent", "agents/worker.py")
    )
    p.ctx.entry_node_by_handler = {"chat": "entry:1"}

    delta = DelegationEdgeStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_only_one_hop_is_followed(project) -> None:
    """`MAX_REACHABILITY_HOPS = 1`: a name two calls away is not evidence.

    Both routes call a runner that ends at `WorkerAgent`; in one the
    agent is named in the runner's own body (one hop), in the other it
    is named in a helper the runner calls (two hops). Only the first is
    a delegation.
    """
    p = project(
        files={
            "apps/api.py": '''\
from agents.runner import run_direct, run_via_middle


@router.post("/chat")
async def chat(request):
    await run_via_middle(request)
    return await run_direct(request)
''',
            "agents/runner.py": '''\
from agents.worker import WorkerAgent


def run_direct(task):
    return WorkerAgent()


def run_via_middle(task):
    return middle(task)


def middle(task):
    return WorkerAgent()
''',
        },
        symbols=[
            _function("run_direct", "agents/runner.py"),
            _function("run_via_middle", "agents/runner.py", line=8),
            _function("middle", "agents/runner.py", line=12),
        ],
        entry_points=EntryPointOutput(
            entry_points=[EntryPoint(
                method="POST", path="/chat", handler="chat",
                file_path="apps/api.py", line=4,
            )],
            symbol_calls=[
                SymbolCall(
                    handler="chat", callee="run_via_middle",
                    file_path="apps/api.py", line=6,
                ),
                SymbolCall(
                    handler="chat", callee="run_direct",
                    file_path="apps/api.py", line=7,
                ),
            ],
            imports={
                "apps/api.py": ["agents.runner"],
                # `agents/runner.py` imports `agents.worker`; the
                # project-wide step reads the import table.
                "agents/runner.py": ["agents.worker"],
            },
        ),
    )
    p.delta.nodes.append(
        _agent("agent:worker", "WorkerAgent", "agents/worker.py")
    )
    p.ctx.entry_node_by_handler = {"chat": "entry:1"}

    delta = DelegationEdgeStage().apply(p.delta, p.ctx)

    assert [
        (e.source_id, e.target_id, e.attributes["evidence"])
        for e in delta.edges
    ] == [("entry:1", "agent:worker", "chat → run_direct → WorkerAgent")]



def test_imported_agent_class_is_linked_directly(project) -> None:
    """A route that constructs an imported agent class delegates to it.

    The import is spelled with a package prefix (`app.agents...`) that
    no manifest path carries — the manifest has no package root, so the
    module is matched on its trailing segments.
    """
    p = project(
        files={
            "apps/api.py": '''\
from app.agents.tool_provider import ToolProviderAgent


@router.get("/chat")
async def chat():
    agent = ToolProviderAgent()
    return {"agent": agent}
''',
            "agents/tool_provider.py": '''\
class ToolProviderAgent:
    pass
''',
        },
        entry_points=EntryPointOutput(
            entry_points=[EntryPoint(
                method="GET", path="/chat", handler="chat",
                file_path="apps/api.py", line=4,
            )],
            symbol_calls=[SymbolCall(
                handler="chat", callee="ToolProviderAgent",
                file_path="apps/api.py", line=6,
            )],
            imports={"apps/api.py": ["app.agents.tool_provider"]},
        ),
    )
    p.delta.nodes.append(
        _agent("agent:tp", "ToolProviderAgent", "agents/tool_provider.py")
    )
    p.ctx.entry_node_by_handler = {"chat": "entry:1"}

    delta = DelegationEdgeStage().apply(p.delta, p.ctx)

    assert [(e.source_id, e.target_id) for e in delta.edges] == [
        ("entry:1", "agent:tp"),
    ]


def test_agent_to_mcp_server_needs_an_agent_node(project) -> None:
    """The gate is a node, not a path: `mcp/servers/...` is not an agent.

    Before A.3b the gate was `usage.is_agent_module` — `"agents" in
    path`. A server module that creates a client is not an agent and gets
    no edge, and a file that holds an agent node does, wherever it lives.
    """
    p = project(
        files={"mcp/servers/findrive/server.py": "def build():\n    pass\n"},
        entry_points=EntryPointOutput(
            mcp_usages=[MCPUsage(
                handler="build", mcp_server_name="findrive",
                file_path="mcp/servers/findrive/server.py", line=1,
            )],
        ),
    )
    p.ctx.mcp_servers = {"findrive": "mcp_server:1"}

    delta = DelegationEdgeStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_call_outside_an_entry_or_agent_file_is_not_a_delegation(
    project,
) -> None:
    """A plain module calling an agent name is not a delegation.

    The source of an edge is a node — an entry handler or an agent — and
    a helper in a file that holds neither is neither.
    """
    p = project(
        files={
            "services/plumbing.py":
                "def helper():\n    return WorkerAgent()\n",
        },
        entry_points=EntryPointOutput(
            symbol_calls=[SymbolCall(
                handler="helper", callee="WorkerAgent",
                file_path="services/plumbing.py", line=2,
            )],
        ),
    )
    p.delta.nodes.append(
        _agent("agent:worker", "WorkerAgent", "agents/worker.py")
    )

    delta = DelegationEdgeStage().apply(p.delta, p.ctx)

    assert delta.edges == []


def test_self_delegation_is_skipped(project) -> None:
    """An agent that names itself is not delegating to itself."""
    p = project(
        files={"agents/orchestrator.py": '''\
class OrchestratorAgent:
    async def delegate_to_self(self, task):
        return await OrchestratorAgent().handle(task)
'''},
        entry_points=EntryPointOutput(
            symbol_calls=[SymbolCall(
                handler="delegate_to_self", callee="OrchestratorAgent",
                file_path="agents/orchestrator.py", line=3,
            )],
        ),
    )
    p.delta.nodes.append(
        _agent("agent:orch", "OrchestratorAgent", "agents/orchestrator.py")
    )

    delta = DelegationEdgeStage().apply(p.delta, p.ctx)

    assert delta.edges == []

