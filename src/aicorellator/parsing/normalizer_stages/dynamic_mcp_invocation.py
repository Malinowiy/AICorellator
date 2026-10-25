"""DynamicMcpInvocationStage — `uses_tool` for a reader (1.3a.1.2).

Run order: 13 of 18 (see `normalizer_stages.STAGES`).

C4: no pre-1.3 row. ADR-0010's channel has two halves by now — 1.3a
emits `source → reader` for every catalogue read, 1.3a.1 emits
`mcp_server → writes_to → source` — and neither says *which tools the
reader can call*. The reader's call is `client.call_tool(name)`, and
`name` is a runtime value: no static edge exists from the agent to any
tool, so a chain that starts at the catalogue dies at the agent and
`find_chain_groups` stays empty on the one corpus that has the shape
(measured: FinBot, 1.3a.1's report).

This stage closes that hop with the only evidence available: **the agent
that reads a catalogue can invoke every tool the catalogue describes.**
The analysis cannot know which one it will pick, so it links all of
them. That is the same trade-off `provides_tool` already makes — "can
call" is the candidate attack path until runtime monitoring can
separate it from "does call" (ADR-0015 decision 5, ADR-0009) — applied
one hop further out.

**Why `uses_tool`, and no new edge kind.** `uses_tool` already means
"an agent calls a tool" (ADR-0013). The invocation is real; only the
resolution is coarse. A new kind would have to mean something different,
and it would need a `find_chains` rule of its own.

**Position.** After `untrusted_source_writers` (11), because it walks
the `writes_to` edges that stage emits, and after `flow_kind` (12), for
the reason the three resolvers after it run there: they construct
control edges and pass `CONTROL_FLOW`, so the kind is named in one place
and this stage names no `flow_kind` of its own (ADR-0016).

Side effects: none. It reads the delta only — the agent nodes and the
three edge kinds — and creates no nodes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Edge
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import CONTROL_FLOW, edge_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


# The capability that makes a node a catalogue reader. The gate is on
# the reader, not on the source: 1.3a credits `reads_untrusted` on the
# component whose body makes the call (ADR-0009), and the source itself
# carries no capabilities by design.
READS_UNTRUSTED: str = "reads_untrusted"


# The source families whose reader can invoke what the source describes.
# One entry, deliberately: a tool catalogue names callable tools, and
# nothing else in ADR-0010's table does. Widening this set needs a rule
# for what the reader can invoke, not just for what it reads.
INVOCABLE_SOURCE_TYPES: frozenset[str] = frozenset({"mcp_tool_description"})


class DynamicMcpInvocationStage:
    """`agent → uses_tool → tool` for a catalogue the agent reads."""

    name = "dynamic_mcp_invocation"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        agents = [
            node for node in delta.nodes
            if node.kind == "agent" and READS_UNTRUSTED in node.capabilities
        ]
        if not agents:
            return delta
        agent_ids = {node.id for node in agents}

        catalogues = {
            node.id for node in delta.nodes
            if node.kind == "untrusted_source"
            and node.attributes.get("source_type")
            in INVOCABLE_SOURCE_TYPES
        }
        if not catalogues:
            return delta

        read_by = _readers_of_catalogues(delta, catalogues, agent_ids)
        writers = _writers_of_catalogues(delta, catalogues)
        tools_by_server = _tools_by_server(delta)

        emitted = {edge.id for edge in delta.edges}
        for agent in sorted(agents, key=lambda node: node.id):
            targets = _invocable_tools(
                agent.id, read_by, writers, tools_by_server
            )
            for tool_id in sorted(targets):
                edge = _invocation_edge(agent.id, tool_id, ctx)
                if edge.id in emitted:
                    continue
                emitted.add(edge.id)
                delta.edges.append(edge)
        return delta


def _readers_of_catalogues(
    delta: "GraphDelta",
    catalogues: set[str],
    agent_ids: set[str],
) -> dict[str, list[str]]:
    """`catalogue id → [agent id]`, from 1.3a's read edges.

    The read edge is `has_access_to` with the source at the tail and the
    reader at the head, and it is the only statement the graph makes
    about *this agent reads this catalogue*. An agent whose capability
    says it reads untrusted data but whose read resolves to no node
    (a plain helper function has none) is not in this index, and gets no
    invocation edge: the catalogue would be a different agent's.
    """
    index: dict[str, list[str]] = {}
    for edge in delta.edges:
        if edge.kind != "has_access_to":
            continue
        if edge.source_id not in catalogues:
            continue
        if edge.target_id not in agent_ids:
            continue
        index.setdefault(edge.source_id, []).append(edge.target_id)
    return index


def _writers_of_catalogues(
    delta: "GraphDelta",
    catalogues: set[str],
) -> dict[str, list[str]]:
    """`catalogue id → [server id]`, from 1.3a.1's `writes_to` edges."""
    index: dict[str, list[str]] = {}
    for edge in delta.edges:
        if edge.kind != "writes_to":
            continue
        if edge.target_id not in catalogues:
            continue
        index.setdefault(edge.target_id, []).append(edge.source_id)
    return index


def _tools_by_server(delta: "GraphDelta") -> dict[str, list[str]]:
    """`server id → [tool id]`, from the `provides_tool` edges."""
    index: dict[str, list[str]] = {}
    for edge in delta.edges:
        if edge.kind != "provides_tool":
            continue
        index.setdefault(edge.source_id, []).append(edge.target_id)
    return index


def _invocable_tools(
    agent_id: str,
    read_by: dict[str, list[str]],
    writers: dict[str, list[str]],
    tools_by_server: dict[str, list[str]],
) -> set[str]:
    """Every tool of every server that writes a catalogue this agent reads.

    Two hops and a set union, in the direction the graph stores: the
    agent's own read edge names the catalogue, the writer's `writes_to`
    edge names the server, and the server's `provides_tool` edges name
    the tools. A catalogue no server writes contributes nothing (no
    writer evidence exists for it), and a server that exposes no tool
    contributes nothing — both are absences, not failures.
    """
    tools: set[str] = set()
    for source_id, readers in read_by.items():
        if agent_id not in readers:
            continue
        for server_id in writers.get(source_id, ()):
            tools.update(tools_by_server.get(server_id, ()))
    return tools


def _invocation_edge(
    agent_id: str,
    tool_id: str,
    ctx: NormalizeContext,
) -> Edge:
    """`uses_tool` from the agent to a tool its catalogue declares.

    `control` via `CONTROL_FLOW`, the same attributes `provides_tool`
    carries and for the same reason: the edge is an access path, and no
    value was traced (ADR-0016, decision 3). No
    `data_flow_confirmed` is written — the column is Layer 2's.
    """
    return Edge(
        id=edge_id(agent_id, tool_id, "uses_tool"),
        source_id=agent_id,
        target_id=tool_id,
        kind="uses_tool",
        host_id=ctx.host_id,
        **CONTROL_FLOW,
    )


__all__ = [
    "DynamicMcpInvocationStage",
    "INVOCABLE_SOURCE_TYPES",
    "READS_UNTRUSTED",
]

