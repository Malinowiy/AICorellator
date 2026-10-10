"""Query Layer — read-only cross-repository queries.

The Query Layer is the ONLY component allowed to read across
multiple repositories. Analysis and Reporting go through it;
they do not issue raw SQL.

Key methods:
  find_chains      — Layer 1 deterministic enumeration (ADR-0006)
  validate_edges   — Layer 3 deterministic validation (ADR-0006)
  get_neighbors    — graph traversal for a single node
  get_subgraph     — slice by kind
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .models import Edge, Node


# ============================================================
# Result types
# ============================================================


@dataclass
class CandidateChain:
    """A structural path from entry to sink, before LLM evaluation.

    Produced by find_chains. Not stored — transient during one
    analysis run (ADR-0006).
    """

    chain_id: str
    nodes: list[str]                       # ordered node IDs
    edges: list[str]                       # ordered edge IDs
    length: int                            # number of hops

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "nodes": self.nodes,
            "edges": self.edges,
            "length": self.length,
        }


@dataclass
class GraphSlice:
    """A subgraph: nodes + edges + findings, ready for serialization."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)


# ============================================================
# Query Layer
# ============================================================


class QueryLayer:
    """Read-only queries spanning multiple repositories.

    Takes a raw sqlite3.Connection. Does not write. Does not
    manage transactions — the caller (GraphStore) does.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --------------------------------------------------------
    # get_neighbors
    # --------------------------------------------------------

    def get_neighbors(self, node_id: str, depth: int = 1) -> GraphSlice:
        """Nodes and edges within `depth` hops of node_id.

        Direction-agnostic: follows edges in both directions.
        depth = 1 → immediate neighbors only.
        """
        if depth < 1:
            raise ValueError("depth must be >= 1")

        seen_nodes: set[str] = {node_id}
        frontier: set[str] = {node_id}
        seen_edges: set[str] = set()

        for _ in range(depth):
            if not frontier:
                break

            placeholders = ",".join("?" for _ in frontier)
            params = list(frontier) * 2  # for source_id and target_id

            rows = self._conn.execute(
                f"""
                SELECT * FROM edges
                WHERE (source_id IN ({placeholders})
                       OR target_id IN ({placeholders}))
                  AND status = 'active'
                """,
                params,
            ).fetchall()

            next_frontier: set[str] = set()
            for row in rows:
                edge = Edge.from_row(row)
                if edge.id in seen_edges:
                    continue
                seen_edges.add(edge.id)

                for endpoint in (edge.source_id, edge.target_id):
                    if endpoint not in seen_nodes:
                        seen_nodes.add(endpoint)
                        next_frontier.add(endpoint)

            frontier = next_frontier

        return self._load_slice(seen_nodes, seen_edges)

    # --------------------------------------------------------
    # get_subgraph
    # --------------------------------------------------------

    def get_subgraph(
        self,
        node_kinds: list[str] | None = None,
        edge_kinds: list[str] | None = None,
        status: str = "active",
    ) -> GraphSlice:
        """All nodes and edges matching the given kinds.

        Used by Analysis to prepare a slice for LLM serialization.
        """
        node_query = "SELECT * FROM nodes WHERE status = ?"
        node_params: list[Any] = [status]
        if node_kinds:
            placeholders = ",".join("?" for _ in node_kinds)
            node_query += f" AND kind IN ({placeholders})"
            node_params.extend(node_kinds)

        node_rows = self._conn.execute(node_query, node_params).fetchall()
        node_ids = {r["id"] for r in node_rows}

        edge_query = "SELECT * FROM edges WHERE status = ?"
        edge_params: list[Any] = [status]
        if edge_kinds:
            placeholders = ",".join("?" for _ in edge_kinds)
            edge_query += f" AND kind IN ({placeholders})"
            edge_params.extend(edge_kinds)

        edge_rows = self._conn.execute(edge_query, edge_params).fetchall()
        edges = [
            Edge.from_row(r)
            for r in edge_rows
            if r["source_id"] in node_ids and r["target_id"] in node_ids
        ]
        edge_ids = {e.id for e in edges}

        return self._load_slice(node_ids, edge_ids)

    # --------------------------------------------------------
    # find_chains — Layer 1 deterministic enumeration (ADR-0006)
    # --------------------------------------------------------

    def find_chains(
        self,
        entry_kinds: list[str] | None = None,
        sink_kinds: list[str] | None = None,
        max_hops: int = 6,
        max_candidates: int = 1000,
    ) -> list[CandidateChain]:
        """Enumerate all structural paths from entry nodes to sink nodes.

        Deterministic. No LLM. Bounded by max_hops and max_candidates.
        Returns candidate chains — NOT evaluated. Evaluation happens
        in Layer 2 (ADR-0006).

        A "path" here ends at a sink node. The path may pass through
        any node kinds in between.
        """
        if entry_kinds is None:
            entry_kinds = ["entry_point"]
        if sink_kinds is None:
            sink_kinds = ["sink"]
        if max_hops < 1:
            raise ValueError("max_hops must be >= 1")

        entry_ph = ",".join("?" for _ in entry_kinds)
        sink_ph = ",".join("?" for _ in sink_kinds)

        entry_rows = self._conn.execute(
            f"SELECT id FROM nodes WHERE kind IN ({entry_ph}) AND status = 'active'",
            entry_kinds,
        ).fetchall()
        entry_ids = [r["id"] for r in entry_rows]

        if not entry_ids:
            return []

        sink_rows = self._conn.execute(
            f"SELECT id FROM nodes WHERE kind IN ({sink_ph}) AND status = 'active'",
            sink_kinds,
        ).fetchall()
        sink_id_set = {r["id"] for r in sink_rows}

        if not sink_id_set:
            return []

        candidates: list[CandidateChain] = []
        seen_chain_ids: set[str] = set()

        for entry_id in entry_ids:
            if len(candidates) >= max_candidates:
                break
            found = self._dfs_paths(
                start=entry_id,
                sink_ids=sink_id_set,
                max_hops=max_hops,
                max_candidates=max_candidates - len(candidates),
            )
            for nodes, edges in found:
                chain_id = self._chain_id(nodes, edges)
                if chain_id in seen_chain_ids:
                    continue
                seen_chain_ids.add(chain_id)
                candidates.append(
                    CandidateChain(
                        chain_id=chain_id,
                        nodes=nodes,
                        edges=edges,
                        length=len(edges),
                    )
                )

        return candidates

    def _dfs_paths(
        self,
        start: str,
        sink_ids: set[str],
        max_hops: int,
        max_candidates: int,
    ) -> list[tuple[list[str], list[str]]]:
        """DFS from `start` to any sink, up to max_hops.

        Returns list of (node_ids, edge_ids) tuples. Uses an
        iterative stack to avoid Python recursion limits on deep
        graphs.
        """
        results: list[tuple[list[str], list[str]]] = []

        # stack entries: (current_node, path_nodes, path_edges)
        stack: list[tuple[str, list[str], list[str]]] = [
            (start, [start], [])
        ]

        while stack:
            if len(results) >= max_candidates:
                break

            current, path_nodes, path_edges = stack.pop()

            if current in sink_ids and len(path_edges) > 0:
                results.append((path_nodes, path_edges))
                continue

            if len(path_edges) >= max_hops:
                continue

            rows = self._conn.execute(
                """
                SELECT * FROM edges
                WHERE source_id = ? AND status = 'active'
                ORDER BY id
                """,
                (current,),
            ).fetchall()

            # Push in reverse order so that the first edge is
            # explored first (depth-first, deterministic).
            for row in reversed(rows):
                edge = Edge.from_row(row)
                next_node = edge.target_id

                # Cycle guard: do not revisit a node in the same path.
                if next_node in path_nodes:
                    continue

                stack.append(
                    (
                        next_node,
                        path_nodes + [next_node],
                        path_edges + [edge.id],
                    )
                )

        return results

    @staticmethod
    def _chain_id(nodes: list[str], edges: list[str]) -> str:
        """Deterministic chain ID from node and edge sequences.

        Format: 'chain:' + first 16 hex chars of sha256.
        Full hash is unnecessary for an ID; 16 chars give 64 bits.
        """
        payload = json.dumps(
            {"nodes": nodes, "edges": edges},
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"chain:{digest[:16]}"

    # --------------------------------------------------------
    # validate_edges — Layer 3 deterministic validation (ADR-0006)
    # --------------------------------------------------------

    def validate_edges(self, edge_ids: list[str]) -> tuple[list[str], list[str]]:
        """Split edge_ids into (existing, missing).

        Preserves input order. Used to reject hallucinated chains:
        if any reported edge is missing, the chain is invalid.
        """
        if not edge_ids:
            return [], []

        found: set[str] = set()
        chunk_size = 900
        for i in range(0, len(edge_ids), chunk_size):
            chunk = edge_ids[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT id FROM edges WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            found.update(r["id"] for r in rows)

        existing = [eid for eid in edge_ids if eid in found]
        missing = [eid for eid in edge_ids if eid not in found]
        return existing, missing

    def is_chain_valid(self, chain_nodes: list[str], chain_edges: list[str]) -> bool:
        """True if every node and every edge exists in the graph.

        Stricter than validate_edges alone: also checks nodes.
        """
        if not chain_nodes or not chain_edges:
            return False

        # Nodes
        chunk_size = 900
        found_nodes: set[str] = set()
        for i in range(0, len(chain_nodes), chunk_size):
            chunk = chain_nodes[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT id FROM nodes WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            found_nodes.update(r["id"] for r in rows)

        if len(found_nodes) != len(set(chain_nodes)):
            return False

        # Edges
        _, missing_edges = self.validate_edges(chain_edges)
        return not missing_edges

    # --------------------------------------------------------
    # Internal helpers
    # --------------------------------------------------------

    def _load_slice(
        self,
        node_ids: set[str],
        edge_ids: set[str],
    ) -> GraphSlice:
        """Fetch nodes, edges, and findings for the given ID sets."""
        if not node_ids:
            return GraphSlice()

        chunk_size = 900
        node_list = list(node_ids)
        edge_list = list(edge_ids)

        nodes: list[Node] = []
        for i in range(0, len(node_list), chunk_size):
            chunk = node_list[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            nodes.extend(Node.from_row(r) for r in rows)

        edges: list[Edge] = []
        for i in range(0, len(edge_list), chunk_size):
            chunk = edge_list[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT * FROM edges WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            edges.extend(Edge.from_row(r) for r in rows)

        findings: list[dict[str, Any]] = []
        if node_list or edge_list:
            all_ids = node_list + edge_list
            for i in range(0, len(all_ids), chunk_size):
                chunk = all_ids[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                rows = self._conn.execute(
                    f"""
                    SELECT * FROM findings
                    WHERE status = 'active'
                      AND (node_id IN ({placeholders})
                           OR edge_id IN ({placeholders}))
                    """,
                    chunk + chunk,
                ).fetchall()
                findings.extend(dict(r) for r in rows)

        return GraphSlice(nodes=nodes, edges=edges, findings=findings)