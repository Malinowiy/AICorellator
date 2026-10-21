"""Query Layer — read-only cross-repository queries.

The Query Layer is the ONLY component allowed to read across
multiple repositories. Analysis and Reporting go through it;
they do not issue raw SQL.

Key methods:
  find_chains       — Layer 1 deterministic enumeration (ADR-0006)
  find_chain_groups — Layer 1 enumeration of groups through an
                      untrusted source (ADR-0010, 1.3b)
  validate_edges    — Layer 3 deterministic validation (ADR-0006)
  get_neighbors     — graph traversal for a single node
  get_subgraph      — slice by kind

find_chains traverses control and data flow only. Structural edges
(`guards`) are declarations, not paths, and are skipped (ADR-0015,
decision 5). `provides_tool` is control: the access path is the
candidate attack path. `find_chain_groups` traverses the same kinds,
for the same reason.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .models import Edge, Node
from ..patterns import SINK_CAPABILITIES, TRAVERSABLE_FLOW_KINDS


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
class ChainGroup:
    """Chains that meet through one untrusted source (ADR-0010, 1.3b).

    A group is an *anchor* — the `untrusted_source` node — plus the
    paths that write into it and the paths that read from it. Neither
    side is dangerous alone; together they are indirect prompt
    injection (ADR-0010). Minimum viable group: one write chain and one
    read chain (ADR-0012), which is what `find_chain_groups` emits
    today — pairs only, `max_chain_group_size = 2` hard-coded until
    1.3c makes it configurable.

    `write_chain_ids[i]` is the id of `write_chains[i]`, and the same
    for reads: the ids are recomputable from the node sequence with the
    same function that names `find_chains` results
    (`QueryLayer._chain_id`), so a group is auditable without the
    matching `CandidateChain`.

    Produced by `find_chain_groups`. Not stored: the `chain_groups`
    table carries evaluation results (`verdict`, `score`,
    `lethal_trifecta`) that belong to 1.3c-1.3e, and half-filled rows
    would need a migration to finish (1.3b deliberately writes nothing).
    """

    group_id: str
    untrusted_source_id: str
    untrusted_source_name: str
    write_chain_ids: list[str]
    read_chain_ids: list[str]
    write_chains: list[list[str]] = field(default_factory=list)
    read_chains: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "untrusted_source_id": self.untrusted_source_id,
            "untrusted_source_name": self.untrusted_source_name,
            "write_chain_ids": self.write_chain_ids,
            "read_chain_ids": self.read_chain_ids,
            "write_chains": self.write_chains,
            "read_chains": self.read_chains,
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
        data_edges_only: bool = False,
    ) -> list[CandidateChain]:
        """Enumerate structural paths from entry nodes to sink nodes.

        A sink is either:
          - a node whose kind is in `sink_kinds` (default: ["sink"]), or
          - a node with an explicit dangerous capability
            (executes_code / executes_shell / writes_filesystem /
            writes_db / http_egress), per ADR-0009.

        Capabilities take precedence: a node of kind "tool" with
        executes_code is a valid sink for chain enumeration, because
        the chain reaches a dangerous action through it.

        Only control and data edges are traversed. Structural edges
        (`guards`) are declarations, not paths, and never appear in a
        chain (ADR-0015, decision 5). `provides_tool` is control: the
        access path to a tool is the candidate attack path.

        `data_edges_only` narrows the result to chains carrying at
        least one data-flow edge — a high-precision shortlist. The
        default is off: control-only chains stay enumerable because
        chain groups and manual review need them (ADR-0015,
        decision 6).

        Deterministic. No LLM. Bounded by max_hops and max_candidates.
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

        # Sinks by kind.
        sink_rows = self._conn.execute(
            f"SELECT id FROM nodes WHERE kind IN ({sink_ph}) AND status = 'active'",
            sink_kinds,
        ).fetchall()
        sink_id_set: set[str] = {r["id"] for r in sink_rows}

        # Sinks by capability (ADR-0009). This is what makes tools with
        # executes_code / writes_filesystem / http_egress valid endpoints.
        sink_id_set.update(self._nodes_with_sink_capabilities())

        if not sink_id_set:
            return []
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
                data_edges_only=data_edges_only,
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
        data_edges_only: bool = False,
    ) -> list[tuple[list[str], list[str]]]:
        """DFS from `start` to any sink, up to max_hops.

        Returns list of (node_ids, edge_ids) tuples. Uses an
        iterative stack to avoid Python recursion limits on deep
        graphs.

        Only control and data edges are traversed; structural edges
        are skipped (ADR-0015, decision 5). With `data_edges_only`,
        a path is reported only if at least one traversed edge is a
        data-flow edge.
        """
        results: list[tuple[list[str], list[str]]] = []

        # stack entries: (current_node, path_nodes, path_edges, has_data)
        stack: list[tuple[str, list[str], list[str], bool]] = [
            (start, [start], [], False)
        ]

        while stack:
            if len(results) >= max_candidates:
                break

            current, path_nodes, path_edges, has_data = stack.pop()

            if current in sink_ids and len(path_edges) > 0:
                # A sink terminates the path either way: a chain that
                # reaches a sink is not extended past it.
                if not data_edges_only or has_data:
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

                # Structural edges are declarations, not paths.
                if edge.flow_kind not in TRAVERSABLE_FLOW_KINDS:
                    continue

                next_node = edge.target_id

                # Cycle guard: do not revisit a node in the same path.
                if next_node in path_nodes:
                    continue

                stack.append(
                    (
                        next_node,
                        path_nodes + [next_node],
                        path_edges + [edge.id],
                        has_data or edge.flow_kind == "data",
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
    # find_chain_groups — Layer 1 enumeration of groups (ADR-0010)
    # --------------------------------------------------------

    def find_chain_groups(
        self,
        max_hops: int = 6,
        max_write_chains_per_source: int = 100,
        max_read_chains_per_source: int = 100,
        max_groups: int = 1000,
    ) -> list[ChainGroup]:
        """Enumerate write/read chain pairs around each untrusted source.

        Direct attacks are linear (`find_chains`). Indirect prompt
        injection is not: one path **writes** attacker content into an
        untrusted channel and another **reads** it on its way to a sink.
        Neither path is dangerous alone (ADR-0010). So the enumeration
        is anchored on the channel — every active `untrusted_source`
        node — and pairs

            write chains   entry_point → ... → S      (paths into S)
            read chains    S → reader → ... → sink    (paths out of S)

        into one group per (source, write, read) triple. Pairs only:
        `max_chain_group_size` is 2, hard-coded until 1.3c makes it
        configurable, because a pair is the canonical shape (ADR-0012)
        and larger groups are where the combinatorics bite.

        S is an **anchor, not a hop**. A write chain ends at S and a
        read chain starts at S; the visited-set rule that keeps a path
        acyclic also keeps either side from passing *through* S, which
        would double-count the channel. The first hop of every read
        chain is the `source → reader` edge 1.3a emits.

        **The write side has no producer yet.** Write chains are
        whatever the graph contains: this method walks the edges that
        point at S, in reverse, and reports the paths that start at an
        entry point. Nothing in 1.3a emits `writer → source` edges — it
        emits `source → reader` only — so on today's graphs the write
        side is empty and this returns no groups. The enumerator is
        complete regardless: it reads the graph, it does not produce
        edges, exactly like `find_chains`. Landing the write side is
        1.3a.1, and it is a *detection* question — which source node
        does a writer write into? — not an enumeration one.

        Deterministic and bounded: no LLM, ids derived from content,
        groups ordered by source id, then write chain id, then read
        chain id; each side bounded by its cap, the total by
        `max_groups`, each path by `max_hops`. Cycle-safe: a path never
        revisits a node (the `_dfs_paths` discipline).
        """
        if max_hops < 1:
            raise ValueError("max_hops must be >= 1")
        for name, value in (
            ("max_write_chains_per_source", max_write_chains_per_source),
            ("max_read_chains_per_source", max_read_chains_per_source),
            ("max_groups", max_groups),
        ):
            if value < 0:
                raise ValueError(f"{name} must be >= 0")

        sources = self._conn.execute(
            """
            SELECT id, name FROM nodes
            WHERE kind = 'untrusted_source' AND status = 'active'
            ORDER BY id
            """
        ).fetchall()
        if not sources:
            return []

        # Entries and sinks, as in find_chains: a sink is a node of kind
        # "sink" or a node with a dangerous capability (ADR-0009).
        entry_ids: set[str] = {
            r["id"]
            for r in self._conn.execute(
                """
                SELECT id FROM nodes
                WHERE kind = 'entry_point' AND status = 'active'
                ORDER BY id
                """
            ).fetchall()
        }
        sink_ids: set[str] = {
            r["id"]
            for r in self._conn.execute(
                "SELECT id FROM nodes WHERE kind = 'sink' AND status = 'active'"
            ).fetchall()
        }
        sink_ids.update(self._nodes_with_sink_capabilities())

        groups: list[ChainGroup] = []
        for row in sources:
            if len(groups) >= max_groups:
                break

            source_id = row["id"]
            write_chains = self._write_chains_to(
                source_id, entry_ids, max_hops, max_write_chains_per_source
            )
            read_chains = self._read_chains_from(
                source_id, sink_ids, max_hops, max_read_chains_per_source
            )

            for write_id, write_nodes in write_chains:
                for read_id, read_nodes in read_chains:
                    if len(groups) >= max_groups:
                        break
                    groups.append(
                        ChainGroup(
                            group_id=self._chain_group_id(
                                source_id, [write_id], [read_id]
                            ),
                            untrusted_source_id=source_id,
                            untrusted_source_name=row["name"],
                            write_chain_ids=[write_id],
                            read_chain_ids=[read_id],
                            write_chains=[write_nodes],
                            read_chains=[read_nodes],
                        )
                    )

        return groups

    def _write_chains_to(
        self,
        source_id: str,
        entry_ids: set[str],
        max_hops: int,
        max_chains: int,
    ) -> list[tuple[str, list[str]]]:
        """Paths `entry_point → ... → source_id`, as (chain_id, nodes).

        Walked **in reverse** — from the anchor along the edges that
        point at it — because that is the direction the graph stores: a
        writer's edge ends at the source. Each path is then reversed to
        read entry-first, which is the orientation the chain is *about*
        and the one its `chain_id` is computed in, so a write chain that
        happens to share a node sequence with a `find_chains` chain
        shares its id.

        A path is reported when it starts at an entry point and has at
        least one hop. Traversal continues *past* an entry point rather
        than stopping there: the node upstream of it starts a longer
        path that also begins at an entry point, and `max_hops` bounds
        both. No `entry_kinds` parameter, unlike `find_chains`: a write
        chain starts where a request arrives, and the anchor is the
        source, so the two ends are not symmetric.

        Order is by chain id, so the caller's nested loop is
        deterministic whatever order the stack happened to produce.
        """
        if max_chains == 0 or not entry_ids:
            return []

        found: list[tuple[str, list[str]]] = []
        seen: set[str] = set()

        # stack entries: (current_node, path_nodes, path_edges)
        stack: list[tuple[str, list[str], list[str]]] = [
            (source_id, [source_id], [])
        ]

        while stack:
            if len(found) >= max_chains:
                break

            current, path_nodes, path_edges = stack.pop()

            if path_edges and current in entry_ids:
                nodes = list(reversed(path_nodes))
                chain_id = self._chain_id(nodes, list(reversed(path_edges)))
                if chain_id not in seen:
                    seen.add(chain_id)
                    found.append((chain_id, nodes))
                continue

            if len(path_edges) >= max_hops:
                continue

            rows = self._conn.execute(
                """
                SELECT * FROM edges
                WHERE target_id = ? AND status = 'active'
                ORDER BY id
                """,
                (current,),
            ).fetchall()

            # Push in reverse so the first edge is explored first.
            for row in reversed(rows):
                edge = Edge.from_row(row)
                # Structural edges are declarations, not paths.
                if edge.flow_kind not in TRAVERSABLE_FLOW_KINDS:
                    continue
                previous = edge.source_id
                # Cycle guard: do not revisit a node in the same path.
                if previous in path_nodes:
                    continue
                stack.append(
                    (
                        previous,
                        path_nodes + [previous],
                        path_edges + [edge.id],
                    )
                )

        found.sort(key=lambda item: item[0])
        return found

    def _read_chains_from(
        self,
        source_id: str,
        sink_ids: set[str],
        max_hops: int,
        max_chains: int,
    ) -> list[tuple[str, list[str]]]:
        """Paths `source_id → ... → sink`, as (chain_id, nodes).

        Forward from the anchor, which is the direction 1.3a built: the
        source's `has_access_to` edge to the reader is the first hop, so
        a read chain is a chain in which the attacker's content is in
        context all the way to a sink.

        A sink terminates a path — it is not extended past one, the same
        rule `_dfs_paths` applies — and a path needs at least one hop,
        so the anchor itself is never a chain. Cycle guard as in
        `_dfs_paths`: a path never revisits a node, which is also what
        stops a read chain from doubling back through its own anchor.

        Order is by chain id.
        """
        if max_chains == 0 or not sink_ids:
            return []

        found: list[tuple[str, list[str]]] = []
        seen: set[str] = set()

        stack: list[tuple[str, list[str], list[str]]] = [
            (source_id, [source_id], [])
        ]

        while stack:
            if len(found) >= max_chains:
                break

            current, path_nodes, path_edges = stack.pop()

            if path_edges and current in sink_ids:
                chain_id = self._chain_id(path_nodes, path_edges)
                if chain_id not in seen:
                    seen.add(chain_id)
                    found.append((chain_id, list(path_nodes)))
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

            for row in reversed(rows):
                edge = Edge.from_row(row)
                if edge.flow_kind not in TRAVERSABLE_FLOW_KINDS:
                    continue
                next_node = edge.target_id
                if next_node in path_nodes:
                    continue
                stack.append(
                    (
                        next_node,
                        path_nodes + [next_node],
                        path_edges + [edge.id],
                    )
                )

        found.sort(key=lambda item: item[0])
        return found

    @staticmethod
    def _chain_group_id(
        source_id: str,
        write_chain_ids: list[str],
        read_chain_ids: list[str],
    ) -> str:
        """Deterministic group ID: `chain_group:` + first 16 hex of sha256.

        Over the anchor and the two id sets, sorted, so an id depends on
        the group's membership and not on the order pairs were
        enumerated in: the same graph always yields the same ids. The
        payload is canonical JSON — the convention `_chain_id` uses —
        rather than the parts concatenated with a separator, because
        JSON delimits and types the fields, and no two distinct groups
        can then collide by running together.
        """
        payload = json.dumps(
            {
                "source": source_id,
                "write": sorted(write_chain_ids),
                "read": sorted(read_chain_ids),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"chain_group:{digest[:16]}"

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
    
    def _nodes_with_sink_capabilities(self) -> set[str]:
        """Active nodes whose capabilities include a sink capability.

        Loads all active nodes and filters in Python. The graph is
        small enough (thousands of nodes) that this is faster and
        simpler than SQL JSON extraction.
        """
        rows = self._conn.execute(
            "SELECT id, capabilities FROM nodes WHERE status = 'active'"
        ).fetchall()

        out: set[str] = set()
        for row in rows:
            caps_raw = row["capabilities"] or "[]"
            try:
                caps = json.loads(caps_raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(caps, list):
                continue
            if SINK_CAPABILITIES.intersection(caps):
                out.add(row["id"])
        return out