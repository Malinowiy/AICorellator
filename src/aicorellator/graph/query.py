"""Query Layer — read-only cross-repository queries.

The Query Layer is the ONLY component allowed to read across
multiple repositories. Analysis and Reporting go through it;
they do not issue raw SQL.

Key methods:
  find_chains       — Layer 1 deterministic enumeration (ADR-0006)
  find_chain_groups — Layer 1 enumeration of groups through an
                      untrusted source (ADR-0010, 1.3b)
  prioritize_chain_groups — Layer 1 scoring and top-N selection of
                      those groups (ADR-0012, 1.3c)
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

from .models import ChainGroupRecord, Edge, Node
from ..patterns import (
    SINK_CAPABILITIES,
    TRAVERSABLE_FLOW_KINDS,
    lethal_trifecta_caps,
)


# ============================================================
# Chain group scoring (ADR-0012)
# ============================================================

# Weights, one per component of ADR-0012's "Cheap Scoring" table. They
# live here rather than in patterns.py because they are not patterns —
# nothing is matched with them; they are policy over signals that
# patterns.py already defines (the `LETHAL_TRIFECTA_*` capability sets
# and `SINK_CAPABILITIES`). Changing a weight changes ranking, never
# detection, which is why it is a constant with a name and not a
# literal in the scorer.
SCORE_LETHAL_TRIFECTA = 5
SCORE_DANGEROUS_SINK = 4
SCORE_EXFIL_OR_WRITE = 3
# ADR-0012's 2026-10-22 revision: a persistent data mutation is the
# same class of impact as a filesystem write, so it is weighted
# identically. It sits next to the other impact weights because it is
# one — the source-type weights below are a different axis.
SCORE_WRITES_DB = 3
SCORE_AUTHENTICATED_ENTRY = 2
SCORE_USER_WRITABLE_SOURCE = 2
SCORE_USER_READABLE_SOURCE = 1
SCORE_SHORT_CHAIN = 1
SCORE_STALE_NODE = -2
SCORE_SHARED_ENTRY = 2

# Capabilities that make a reading chain's sink a dangerous one.
# `executes_*` is the high-impact action (+4); `writes_filesystem` /
# `http_egress` is data manipulation or exfiltration (+3). A sink with
# both earns both — the two rows of the ADR table are two components,
# not two levels of one.
DANGEROUS_SINK_CAPS: frozenset[str] = frozenset({
    "executes_shell",
    "executes_code",
})
EXFIL_OR_WRITE_CAPS: frozenset[str] = frozenset({
    "writes_filesystem",
    "http_egress",
})
# The third impact row (ADR-0012 revision, 2026-10-22). One capability,
# so a one-line set rather than a literal: `_matched_caps` takes a set
# and every impact row is read the same way.
WRITES_DB_CAPS: frozenset[str] = frozenset({"writes_db"})

# Untrusted source types by how much control the attacker needs to
# place content in them (ADR-0012): user-writable is +2, readable-only
# is +1. A type in neither set scores 0 — a source kind nobody has
# weighed is not silently treated as writable.
USER_WRITABLE_SOURCE_TYPES: frozenset[str] = frozenset({
    "user_upload",
    "rag_corpus",
})
USER_READABLE_SOURCE_TYPES: frozenset[str] = frozenset({
    "mcp_tool_description",
})

# "Short total chain length" (ADR-0012 leaves the threshold open). 6 is
# the write + read budget: `find_chain_groups` gives each side
# `max_hops = 6`, so a group that spends its whole budget on one side
# is at the edge of "short" and 7 is past it. The cutoff is echoed in
# the component's reason so a reader of the score can see it.
SHORT_CHAIN_TOTAL_HOPS = 6

# A node below this confidence makes the group's data uncertain
# (ADR-0012: `confidence < 0.7`).
STALE_CONFIDENCE_BELOW = 0.7

# Priority buckets (ADR-0012 §3). Configurable by the caller of
# `prioritize_chain_groups`; these are the defaults.
PRIORITY_HIGH_MIN = 15
PRIORITY_MEDIUM_MIN = 10

# Selection defaults (ADR-0012 §"Ranking and Selection").
MAX_GROUPS_SCORED = 10000
MAX_GROUPS_EVALUATED = 100
# The threshold was revised from 5 to 3 in the same pass that weighted
# `writes_db` (2026-10-22): a readable source (+1) plus a database
# write (+3) is a candidate at 4, and no group on FinBot cleared 5.
MIN_SCORE_TO_EVALUATE = 3

# Component keys of `score_components`, in report order. Stable: a
# downstream consumer filters or re-weights by these names. One key per
# row of the ADR-0012 table, so a weight can be traced to a key and
# back.
SCORE_COMPONENT_KEYS: tuple[str, ...] = (
    "lethal_trifecta",
    "dangerous_sink",
    "exfil_or_write",
    "writes_db",
    "authenticated_entry",
    "user_writable_source",
    "user_readable_source",
    "short_chain",
    "stale_node",
    "shared_entry",
)


def priority_for(
    score: float,
    high_min: int = PRIORITY_HIGH_MIN,
    medium_min: int = PRIORITY_MEDIUM_MIN,
) -> str:
    """The priority bucket for a score: high, medium or low.

    Public because the bucket boundaries are policy a caller may want
    to test or reuse, and because the pipeline that persists groups
    needs the same function the scorer used — a second implementation
    would be a second answer.
    """
    if score >= high_min:
        return "high"
    if score >= medium_min:
        return "medium"
    return "low"


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
    1.3c.1 makes it configurable.

    `write_chain_ids[i]` is the id of `write_chains[i]`, and the same
    for reads: the ids are recomputable from the node sequence with the
    same function that names `find_chains` results
    (`QueryLayer._chain_id`), so a group is auditable without the
    matching `CandidateChain`.

    Produced by `find_chain_groups`, which writes nothing. `score`,
    `score_components` and `priority` are filled in by
    `QueryLayer.prioritize_chain_groups` (1.3c, ADR-0012) on the groups
    it scores; an unprioritized group carries the defaults, which is
    how "not scored" is spelled. `to_record()` maps a selected group to
    its `chain_groups` row — the store is a separate step, so scoring
    stays testable without a database.
    """

    group_id: str
    untrusted_source_id: str
    untrusted_source_name: str
    write_chain_ids: list[str]
    read_chain_ids: list[str]
    write_chains: list[list[str]] = field(default_factory=list)
    read_chains: list[list[str]] = field(default_factory=list)
    score: float = 0.0
    score_components: dict[str, Any] = field(default_factory=dict)
    priority: str | None = None

    @property
    def write_hops(self) -> int:
        """Total hops on the writing side (summed over its chains)."""
        return sum(max(0, len(chain) - 1) for chain in self.write_chains)

    @property
    def read_hops(self) -> int:
        """Total hops on the reading side (summed over its chains)."""
        return sum(max(0, len(chain) - 1) for chain in self.read_chains)

    @property
    def total_hops(self) -> int:
        """Hops of the whole group — what "short" is measured on."""
        return self.write_hops + self.read_hops

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "untrusted_source_id": self.untrusted_source_id,
            "untrusted_source_name": self.untrusted_source_name,
            "write_chain_ids": self.write_chain_ids,
            "read_chain_ids": self.read_chain_ids,
            "write_chains": self.write_chains,
            "read_chains": self.read_chains,
            "score": self.score,
            "score_components": self.score_components,
            "priority": self.priority,
        }

    def to_record(self) -> ChainGroupRecord:
        """This group as a `chain_groups` row.

        Only the columns 1.3c owns are filled: the anchor, the id sets,
        `score`, `score_components`, `priority`, `status = 'active'`.
        The evaluation columns keep their model defaults — 1.3d/1.3e
        write them, and the repository's `upsert` refuses to touch them.

        `name` is derived, not invented: `<source name>:<w>x<r>` with
        the hop counts of each side, e.g.
        `mcp_tool_description:client.list_tools:3x4`. The source name is
        already the group's human anchor and two groups on one source
        differ exactly in their chain lengths, so the name identifies
        the row without an LLM (ADR-0001: no generated prose in stored
        data) and without a counter that would drift between runs.
        """
        return ChainGroupRecord(
            id=self.group_id,
            name=(
                f"{self.untrusted_source_name}:"
                f"{self.write_hops}x{self.read_hops}"
            ),
            untrusted_source_id=self.untrusted_source_id,
            write_chain_ids=list(self.write_chain_ids),
            read_chain_ids=list(self.read_chain_ids),
            score=self.score,
            score_components=self.score_components,
            priority=self.priority,
            status="active",
        )


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
        least one edge that a runtime run observed — that is,
        `"runtime" in edge.observed_by` (ADR-0016). On an AST-only scan
        no edge carries it, so the answer is `[]`; that is correct, not
        a failure. The default is off: control-only chains stay
        enumerable because chain groups and manual review need them
        (ADR-0015, decision 6).

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
        a path is reported only if at least one traversed edge was
        observed by a runtime run — `"runtime" in edge.observed_by`
        (ADR-0016).
        """
        results: list[tuple[list[str], list[str]]] = []

        # stack entries: (current_node, path_nodes, path_edges,
        # runtime_observed) — the last flag tracks whether any edge on
        # the path carries "runtime" in observed_by.
        stack: list[tuple[str, list[str], list[str], bool]] = [
            (start, [start], [], False)
        ]

        while stack:
            if len(results) >= max_candidates:
                break

            current, path_nodes, path_edges, runtime_observed = stack.pop()

            if current in sink_ids and len(path_edges) > 0:
                # A sink terminates the path either way: a chain that
                # reaches a sink is not extended past it.
                if not data_edges_only or runtime_observed:
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
                        runtime_observed or "runtime" in edge.observed_by,
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
        `max_chain_group_size` is 2, hard-coded until 1.3c.1 makes it
        configurable, because a pair is the canonical shape (ADR-0012)
        and larger groups are where the combinatorics bite. 1.3c scores
        and selects these pairs; it does not widen them.

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
    # prioritize_chain_groups — Layer 1 scoring (ADR-0012)
    # --------------------------------------------------------

    def prioritize_chain_groups(
        self,
        groups: list[ChainGroup],
        max_groups_scored: int = MAX_GROUPS_SCORED,
        max_groups_evaluated: int = MAX_GROUPS_EVALUATED,
        min_score_to_evaluate: int = MIN_SCORE_TO_EVALUATE,
        high_min: int = PRIORITY_HIGH_MIN,
        medium_min: int = PRIORITY_MEDIUM_MIN,
    ) -> list[ChainGroup]:
        """Score, rank and select groups for LLM evaluation (ADR-0012).

        Read-only over the graph and pure over `groups` (apart from
        filling in their score fields): it does not persist and it does
        not call an LLM. The store step is the caller's —
        `ChainGroup.to_record()` plus `ChainGroupsRepository.upsert` —
        which keeps scoring testable without a database and keeps the
        Query Layer free of writes.

        Four steps, each bounded by a parameter:

        1. **Score** the first `max_groups_scored` groups, in the order
           they were enumerated (deterministic: source id, write chain
           id, read chain id). A group's score cannot be known without
           scoring it, so the cap is on enumeration order, not on rank.
           Groups past the cap are left untouched — no score, no
           components, no priority — and are never selected.
        2. **Rank** by score descending. Ties break on `group_id`:
           ids are content-derived, so the order is a property of the
           groups and not of the enumeration that produced them, and a
           re-run with a different `max_groups_scored` selects the same
           groups when scores tie.
        3. **Select** the first `max_groups_evaluated` groups whose
           score is at least `min_score_to_evaluate`. Everything else
           is scored and dropped — visible in the caller's list, not
           returned.
        4. **Bucket** each selected group: `high` at `high_min`,
           `medium` at `medium_min`, else `low`. Unselected groups keep
           `priority = None`, which is what "not sent to the LLM"
           means in the table.

        `analysis_budget_seconds` (ADR-0012) is not enforced here — it
        bounds the LLM phase, which is 1.3e's, and there is nothing in
        this method to spend it on.

        The selected groups are returned in rank order, best first.
        """
        for name, value in (
            ("max_groups_scored", max_groups_scored),
            ("max_groups_evaluated", max_groups_evaluated),
            ("min_score_to_evaluate", min_score_to_evaluate),
        ):
            if value < 0:
                raise ValueError(f"{name} must be >= 0")

        scored = groups[:max_groups_scored]
        for group in scored:
            group.score, group.score_components = self.score_chain_group(
                group
            )

        ranked = sorted(scored, key=lambda g: (-g.score, g.group_id))
        selected = [
            group
            for group in ranked
            if group.score >= min_score_to_evaluate
        ][:max_groups_evaluated]

        chosen = {group.group_id for group in selected}
        for group in scored:
            group.priority = (
                priority_for(group.score, high_min, medium_min)
                if group.group_id in chosen
                else None
            )

        return selected

    def score_chain_group(
        self,
        group: ChainGroup,
    ) -> tuple[float, dict[str, Any]]:
        """The cheap score of one group, and its breakdown (ADR-0012).

        Capabilities and structure only — no LLM, no target content. The
        score is the sum of the components; it is *not* clamped at 0, so
        a stale, low-signal group ranks below a group that scored
        nothing rather than tying with it.

        Two of the ADR's signals are deliberately not applied, and their
        components are pinned at 0 with a reason saying so:

          - **authenticated entry point** — the graph does not model
            authentication. Every entry point would score +2, which is
            not a signal but a constant, and a pattern-matched guess
            (`Depends(get_current_user)`) has no corpus to be validated
            against.
          - **shared entry point** — a write chain starts at an entry
            point and a read chain starts at the source, so the two
            cannot share a first node. The ADR's wording does not
            describe a shape this enumerator can produce.

        Both stay in `score_components` at 0 so the ADR table and the
        stored breakdown stay one-to-one: a weight that is off is
        visible, not missing.
        """
        nodes = self._nodes_by_id(self._group_node_ids(group))

        components: dict[str, int] = {key: 0 for key in SCORE_COMPONENT_KEYS}
        reasons: dict[str, str] = {}

        self._score_lethal_trifecta(group, nodes, components, reasons)
        self._score_read_sinks(group, nodes, components, reasons)
        self._score_read_writes_db(group, nodes, components, reasons)
        self._score_source_type(group, nodes, components, reasons)

        if group.total_hops <= SHORT_CHAIN_TOTAL_HOPS:
            components["short_chain"] = SCORE_SHORT_CHAIN
            reasons["short_chain"] = (
                f"total {group.total_hops} hops"
                f" <= {SHORT_CHAIN_TOTAL_HOPS}"
            )
        else:
            reasons["short_chain"] = (
                f"total {group.total_hops} hops"
                f" > {SHORT_CHAIN_TOTAL_HOPS}"
            )

        stale = self._stale_node(nodes)
        if stale is not None:
            components["stale_node"] = SCORE_STALE_NODE
            reasons["stale_node"] = (
                f"confidence < {STALE_CONFIDENCE_BELOW} at {stale}"
            )
        else:
            reasons["stale_node"] = (
                f"every node has confidence >= {STALE_CONFIDENCE_BELOW}"
            )

        components["authenticated_entry"] = 0
        reasons["authenticated_entry"] = (
            "not modelled; the signal is skipped in 1.3c"
        )
        components["shared_entry"] = 0
        reasons["shared_entry"] = (
            "not modelled; write and read chains cannot share a first node"
        )

        total = sum(components.values())
        payload: dict[str, Any] = dict(components)
        payload["total"] = total
        payload["reasons"] = reasons
        return float(total), payload

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

    # --------------------------------------------------------
    # Scoring helpers (ADR-0012)
    # --------------------------------------------------------

    @staticmethod
    def _group_node_ids(group: ChainGroup) -> list[str]:
        """Every node id the group mentions, deduplicated and sorted.

        One read for the whole group rather than one per node: a pair
        group has at most a dozen nodes and the scorer runs for every
        enumerated group.
        """
        return sorted({
            node_id
            for chain in group.write_chains + group.read_chains
            for node_id in chain
        })

    def _nodes_by_id(self, node_ids: list[str]) -> dict[str, Node]:
        """Nodes by id, for the ids that exist.

        A missing id is simply absent: a group is a set of paths the
        graph produced, but `chain_groups` has no FK to nodes and a
        caller may hand in a group whose nodes were deleted between
        enumeration and scoring. Absence scores 0, it does not raise.
        """
        if not node_ids:
            return {}
        placeholders = ",".join("?" for _ in node_ids)
        rows = self._conn.execute(
            f"SELECT * FROM nodes WHERE id IN ({placeholders})",
            node_ids,
        ).fetchall()
        return {row["id"]: Node.from_row(row) for row in rows}

    @staticmethod
    def _matched_caps(
        capabilities: list[str],
        wanted: frozenset[str],
    ) -> list[str]:
        """The capabilities a node has out of a set, sorted.

        Sorted so a reason string is deterministic — two runs over the
        same graph produce the same text, which is what makes a stored
        `score_components` diffable.
        """
        return sorted(wanted.intersection(capabilities))

    @staticmethod
    def _source_type(group: ChainGroup, nodes: dict[str, Node]) -> str:
        """The anchor's source type: `rag_corpus`, `mcp_tool_description`...

        Read from the node's `source_type` attribute, which is what
        1.3a's detector writes. The name (`<type>:<call_target>`) is the
        fallback for a group whose anchor carries no attributes — a
        hand-built one in a test, or a node written before the attribute
        existed. Both paths give the same string for a graph 1.3a built.
        """
        source = nodes.get(group.untrusted_source_id)
        if source is not None:
            declared = source.attributes.get("source_type")
            if isinstance(declared, str) and declared:
                return declared
        return group.untrusted_source_name.split(":", 1)[0]

    def _score_lethal_trifecta(
        self,
        group: ChainGroup,
        nodes: dict[str, Node],
        components: dict[str, int],
        reasons: dict[str, str],
    ) -> None:
        """+5 when a node on the *reading* side has the trifecta.

        The definition is `patterns.lethal_trifecta_caps` (ADR-0009):
        reads untrusted input, can read secrets, can get data out. The
        answer is normally read off `node.attributes["lethal_trifecta"]`,
        which `LethalTrifectaStage` (1.3d, stage 18) writes for every
        node. The graph is therefore the authority, and a consumer of
        the flag and this score cannot disagree about a node.

        A node written before 1.3d carries no such attribute, and the
        attribute is the only thing missing then: the fallback walks the
        node's capabilities through the same function. The two paths
        cannot disagree about a node the stage saw, because they run one
        definition over the same capabilities.

        The writing side is not searched: the attacker's content enters
        through the read, and a trifecta node upstream of the write
        would be another chain's problem.
        """
        read_ids = sorted({
            node_id
            for chain in group.read_chains
            for node_id in chain
        })
        for node_id in read_ids:
            node = nodes.get(node_id)
            if node is None:
                continue
            caps = node.capabilities
            marked = node.attributes.get("lethal_trifecta")
            if marked is None:
                # Pre-1.3d graph, or a node the stage never saw: the
                # capability walk is the definition.
                marked = bool(lethal_trifecta_caps(caps))
            if not marked:
                continue
            matched = lethal_trifecta_caps(caps)
            components["lethal_trifecta"] = SCORE_LETHAL_TRIFECTA
            reasons["lethal_trifecta"] = (
                f"{'+'.join(matched)} at {node_id}" if matched
                else f"lethal_trifecta at {node_id}"
            )
            return

        reasons["lethal_trifecta"] = (
            "no read-side node reads untrusted input and secrets and can "
            "send data out"
        )

    def _score_read_sinks(
        self,
        group: ChainGroup,
        nodes: dict[str, Node],
        components: dict[str, int],
        reasons: dict[str, str],
    ) -> None:
        """+4 / +3 when a reading chain *ends* at a dangerous sink.

        The sink is the last node of a read chain — the node the
        enumeration stopped at, either a `sink` node or one carrying a
        sink capability (ADR-0009). A mid-chain node is not a sink: the
        chain reaches the sink, it does not pass through it.

        Each component is credited once per group (the best read chain
        wins), not once per read chain: the group is one finding, and
        "the read side can run a shell" is one fact about it. The two
        components are independent — a sink that both executes and
        exfiltrates earns both, because the ADR table's two rows are two
        signals, not two levels of one.

        The table's third impact row, `writes_db`, is *not* here: the
        revision reads it as any read-chain node, not the terminal. See
        `_score_read_writes_db`.
        """
        terminals = sorted({
            chain[-1]
            for chain in group.read_chains
            if chain
        })
        for node_id in terminals:
            node = nodes.get(node_id)
            if node is None:
                continue
            dangerous = self._matched_caps(
                node.capabilities, DANGEROUS_SINK_CAPS
            )
            if dangerous and not components["dangerous_sink"]:
                components["dangerous_sink"] = SCORE_DANGEROUS_SINK
                reasons["dangerous_sink"] = (
                    f"{'+'.join(dangerous)} at {node_id}"
                )
            exfil = self._matched_caps(node.capabilities, EXFIL_OR_WRITE_CAPS)
            if exfil and not components["exfil_or_write"]:
                components["exfil_or_write"] = SCORE_EXFIL_OR_WRITE
                reasons["exfil_or_write"] = f"{'+'.join(exfil)} at {node_id}"

        if not components["dangerous_sink"]:
            reasons["dangerous_sink"] = (
                "no reading chain ends at an executes_shell or "
                "executes_code node"
            )
        if not components["exfil_or_write"]:
            reasons["exfil_or_write"] = (
                "no reading chain ends at a writes_filesystem or "
                "http_egress node"
            )

    def _score_read_writes_db(
        self,
        group: ChainGroup,
        nodes: dict[str, Node],
        components: dict[str, int],
        reasons: dict[str, str],
    ) -> None:
        """+3 when *any* node on the reading side writes to a database.

        Deliberately wider than `_score_read_sinks`, and this is the one
        place the two readings of "reaches a sink" differ:

          - `dangerous_sink` / `exfil_or_write` keep 1.3c's reading — a
            read chain *ends* at the sink. It reaches the sink, it does
            not pass through it.
          - `writes_db` credits *any* node on a read chain, because the
            impact is the mutation and not the stopping point: a chain
            that writes a row mid-path and continues to another sink has
            still let untrusted input mutate the database. The ADR
            table's wording is "reading chain reaches a `writes_db`
            sink", and a mid-chain node is reached.

        The asymmetry is the ADR-0012 revision's, not the enumerator's:
        if the two other impact rows should widen the same way, that is
        a further revision of the table, not a silent change here.

        Credited once per group, like every other component: several
        read chains through the same database writer is one fact about
        the group, not three. The first match in id order wins, so the
        reason string is deterministic.
        """
        read_ids = sorted({
            node_id
            for chain in group.read_chains
            for node_id in chain
        })
        for node_id in read_ids:
            node = nodes.get(node_id)
            if node is None:
                continue
            matched = self._matched_caps(node.capabilities, WRITES_DB_CAPS)
            if matched:
                components["writes_db"] = SCORE_WRITES_DB
                reasons["writes_db"] = f"{'+'.join(matched)} at {node_id}"
                return

        reasons["writes_db"] = (
            "no reading chain node writes to a database"
        )

    def _score_source_type(
        self,
        group: ChainGroup,
        nodes: dict[str, Node],
        components: dict[str, int],
        reasons: dict[str, str],
    ) -> None:
        """+2 for a user-writable source, +1 for a readable-only one.

        Mutually exclusive: a source has one type, and the two weights
        are two rows of the ADR table for the two cases. A type in
        neither set scores 0 — an unweighed source kind is not silently
        treated as writable.
        """
        source_type = self._source_type(group, nodes)
        if source_type in USER_WRITABLE_SOURCE_TYPES:
            components["user_writable_source"] = SCORE_USER_WRITABLE_SOURCE
            reasons["user_writable_source"] = (
                f"source type {source_type} is user-writable"
            )
            reasons["user_readable_source"] = (
                f"source type {source_type} is writable, not readable-only"
            )
            return

        if source_type in USER_READABLE_SOURCE_TYPES:
            components["user_readable_source"] = SCORE_USER_READABLE_SOURCE
            reasons["user_readable_source"] = (
                f"source type {source_type} is user-readable only"
            )
            reasons["user_writable_source"] = (
                f"source type {source_type} is not user-writable"
            )
            return

        reasons["user_writable_source"] = (
            f"source type {source_type} is not user-writable"
        )
        reasons["user_readable_source"] = (
            f"source type {source_type} is not user-readable only"
        )

    @staticmethod
    def _stale_node(nodes: dict[str, Node]) -> str | None:
        """The first node below the confidence floor, or None.

        First in id order, so the reason is deterministic when several
        nodes are stale. The floor is ADR-0012's `confidence < 0.7`; the
        value itself is not echoed into the reason (ADR-0001: stored
        prose describes the rule, not the data).
        """
        for node_id in sorted(nodes):
            if nodes[node_id].confidence < STALE_CONFIDENCE_BELOW:
                return node_id
        return None
