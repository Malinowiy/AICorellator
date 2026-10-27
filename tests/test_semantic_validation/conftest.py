"""Shared builder for the A.4 Layer 3 semantic-validation tests.

Every check A.4 adds is a question about one graph and one candidate
chain, so each test builds the smallest graph that isolates the rule it
is about. `graph` keeps the ids: a test names a node once and uses the
id wherever the check needs it, which is what makes the assertions read
like the rule they pin.

Ids are derived from the kind and the name (`entry_point:entry`,
`edge:e1`) so a failure message names the thing that failed.
"""

from __future__ import annotations

import pytest

from aicorellator import GraphStore
from aicorellator.graph import Edge, Node


class Graph:
    """Named nodes and edges, written to the store as they are added."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def node(
        self,
        name: str,
        kind: str,
        capabilities: list[str] | None = None,
        attributes: dict | None = None,
    ) -> str:
        node_id = f"{kind}:{name}"
        self.store.nodes.upsert(Node(
            id=node_id,
            kind=kind,
            name=name,
            host_id="localhost",
            capabilities=list(capabilities or []),
            attributes=dict(attributes or {}),
        ))
        return node_id

    def edge(
        self,
        name: str,
        source: str,
        target: str,
        kind: str = "has_access_to",
    ) -> str:
        edge_id = f"edge:{name}"
        self.store.edges.upsert(Edge(
            id=edge_id,
            source_id=source,
            target_id=target,
            kind=kind,
            host_id="localhost",
        ))
        return edge_id

    def source(
        self,
        name: str = "inbox",
        source_type: str | None = "email_inbox",
        reader_capabilities: list[str] | None = None,
    ) -> str:
        """An `untrusted_source` and one reader, wired the normal way.

        Returns the source id. `source_type=None` writes no attribute at
        all, which is the "declares nothing" case; a caller that wants
        its own `source_type` passes it, including one outside the
        implemented families.
        """
        attributes = {} if source_type is None else {
            "source_type": source_type,
        }
        source_id = self.node(name, "untrusted_source", attributes=attributes)
        reader = self.node(
            f"{name}-reader",
            "tool",
            capabilities=reader_capabilities or ["reads_untrusted"],
        )
        self.edge(f"{name}-access", source_id, reader)
        return source_id


@pytest.fixture
def graph(store: GraphStore) -> Graph:
    return Graph(store)
