"""OrphanEntryStage: an entry point with no delegation (Resolver 2)."""

from __future__ import annotations

from aicorellator.graph.models import Node
from aicorellator.parsing.normalizer_stages import OrphanEntryStage


def test_orphan_entry_links_to_the_single_framework_agent(project) -> None:
    """LangChain 1.0 factories hide the agent behind a graph object."""
    p = project(files={"app.py": ""})
    p.delta.nodes.extend([
        Node(
            id="agent:1", kind="agent", name="create_agent",
            host_id="localhost",
            attributes={"source_file": "agent.py", "framework": "langchain"},
        ),
        Node(
            id="entry:1", kind="entry_point", name="POST /chat",
            host_id="localhost", attributes={"source_file": "app.py"},
        ),
    ])

    delta = OrphanEntryStage().apply(p.delta, p.ctx)

    assert [(e.kind, e.source_id, e.target_id) for e in delta.edges] == [
        ("delegates_to", "entry:1", "agent:1"),
    ]
    assert delta.edges[0].attributes["resolved_via"] == (
        "orphan_entry_to_single_agent"
    )


def test_no_edge_when_there_are_two_framework_agents(project) -> None:
    """Ambiguity is a deliberate no-op, not a guess."""
    p = project(files={"app.py": ""})
    for index in (1, 2):
        p.delta.nodes.append(Node(
            id=f"agent:{index}", kind="agent", name=f"Agent{index}",
            host_id="localhost",
            attributes={"source_file": f"a{index}.py", "framework": "x"},
        ))
    p.delta.nodes.append(Node(
        id="entry:1", kind="entry_point", name="GET /",
        host_id="localhost", attributes={"source_file": "app.py"},
    ))

    delta = OrphanEntryStage().apply(p.delta, p.ctx)

    assert delta.edges == []
