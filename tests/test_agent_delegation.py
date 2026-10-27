"""The `agent_delegation` fixture: delegations resolved from provenance.

The positive path of BACKLOG A.3b, end to end and on real files:

    POST /tasks → run_orchestrator_agent → OrchestratorAgent
                → run_worker_agent → WorkerAgent

Nothing in the chain reads a name as a claim about a kind. The route
calls a runner; the runner is followed through its import because the
import names a file that defines it; the file's body names an agent
node the classification layer already produced from an LLM call. The
call site itself never says "agent".
"""

from __future__ import annotations

from pathlib import Path

from aicorellator.ingestion import discover, ingest
from aicorellator.parsing import make_context, normalize, run_tsa
from aicorellator.parsing.capability_extractor import (
    extract as extract_capabilities,
)
from aicorellator.parsing.entry_points import run as run_entry_points


FIXTURE = Path(__file__).parent / "fixtures" / "agent_delegation"


def _normalized(target_root: Path):
    """Run the deterministic pipeline over a real directory."""
    target = ingest(target_root, kind="folder")
    manifest = discover(target)
    tsa = run_tsa(manifest, target.root)
    capabilities = {
        entry.path: extract_capabilities(entry.abs_path, entry.path)
        for entry in manifest.files
        if entry.language == "python"
    }
    ep = run_entry_points(manifest)
    delta = normalize(
        make_context(manifest, tsa, ep, capabilities=capabilities)
    )
    return tsa, ep, delta


def test_fixture_resolves_entry_to_agent_and_agent_to_agent() -> None:
    """Both delegations are emitted, with the names that resolved them."""
    _, _, delta = _normalized(FIXTURE)

    nodes = {n.id: n for n in delta.nodes}
    assert {
        (nodes[e.source_id].name, nodes[e.target_id].name)
        for e in delta.edges if e.kind == "delegates_to"
    } == {
        ("POST /tasks", "OrchestratorAgent"),
        ("OrchestratorAgent", "WorkerAgent"),
    }

    # ADR-0015/ADR-0016: control flow, no data-flow verdict, names only.
    for edge in (e for e in delta.edges if e.kind == "delegates_to"):
        assert edge.flow_kind == "control"
        assert edge.data_flow_confirmed is None
        assert edge.attributes["resolved_via"] == "call_graph"
    assert {
        e.attributes["evidence"]
        for e in delta.edges if e.kind == "delegates_to"
    } == {
        "create_task → run_orchestrator_agent → OrchestratorAgent",
        "delegate_to_worker → run_worker_agent → WorkerAgent",
    }


def test_fixture_agents_come_from_capability_not_from_names() -> None:
    """The two agents are agents because their bodies call an LLM.

    The fixture's classes are named `*Agent`, which is exactly what must
    not decide anything: `WorkerAgent` is an agent node because
    `self.llm.invoke` is `calls_llm`, and the graph would be the same if
    the class were called `PlanExecutor`.
    """
    _, _, delta = _normalized(FIXTURE)

    agents = {n.name: n for n in delta.nodes if n.kind == "agent"}
    assert set(agents) == {"OrchestratorAgent", "WorkerAgent"}
    assert all("calls_llm" in n.capabilities for n in agents.values())


def test_fixture_second_hop_is_not_followed() -> None:
    """`run_agent_with_retry` names the agent two hops out.

    It is the runner the orchestrator's runner calls, so a resolver that
    followed two hops would still find `WorkerAgent` — and would be
    guessing. Nothing links to it: it is not an agent, and no call site
    in the fixture names it as one.
    """
    _, _, delta = _normalized(FIXTURE)

    assert not [n for n in delta.nodes if n.name == "run_agent_with_retry"]
    assert not [
        e for e in delta.edges
        if e.kind == "delegates_to"
        and "run_agent_with_retry" in e.attributes.get("evidence", "")
    ]
