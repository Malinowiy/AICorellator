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
  validate_chain    — Layer 3 existence plus structural coherence:
                      connected, typed, anchored (ADR-0006, A.4)
  get_neighbors     — graph traversal for a single node
  get_subgraph      — slice by kind

find_chains traverses control and data flow only. Structural edges
(`guards`, `writes_to`, `reads_from`) are declarations, not paths, and
are skipped (ADR-0015, decision 5). `provides_tool` is control: the
access path is the candidate attack path. `find_chain_groups`
traverses the same kinds, for the same reason — except that it crosses
`writes_to` once, as a write chain's last hop, because there the edge
names the chain's end rather than extending it (ADR-0021 §6).
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
    UNTRUSTED_SOURCE_RULES,
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

# The source families an `untrusted_source` node may declare (A.4's
# anchor check). Derived from `patterns.UNTRUSTED_SOURCE_RULES` — the
# table that defines the families — rather than spelled out again: a
# family that gains a detector gains its membership here for free, so
# the check and the detector cannot drift apart. `user_upload` is
# absent because it has no detector yet (ADR-0010 gap, BACKLOG B.3);
# when it lands, it lands here too.
UNTRUSTED_SOURCE_TYPES: frozenset[str] = frozenset(
    source_type for source_type, _rule in UNTRUSTED_SOURCE_RULES
)

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
# Store joins (ADR-0021)
# ============================================================

# The boundary between a strong and a weak group (ADR-0021 §9). A group
# whose join confidence is below it is still real evidence, so it stays
# in the graph and out of the default enumeration; `include_weak=True`
# is how a caller asks for it. The value is `config.yaml`'s
# (`store.confidence_threshold`, read by the caller) and the default
# here is the policy default — the same 0.7, pinned by a test.
DEFAULT_STORE_CONFIDENCE_THRESHOLD = 0.7

# Below this there is no link at all (ADR-0021 §9): the base of §8's
# combination, and the floor under the two thresholds. A producer is
# not supposed to emit a `writes_to` / `reads_from` pair below it; the
# join drops one anyway rather than reporting a "store" the analysis
# does not believe in.
WEAK_JOIN_FLOOR = 0.3

# ADR-0023 §5's one exception to the floor above: a link the
# store-identity LLM fallback created carries
# `attributes["identity_source"] = "llm"` and a base of 0.2, and it is
# judged against `confidence.WEAK_JOIN_FLOOR_LLM` (0.1) instead. §9's
# floor is a floor on *structural* claims — a family's literal, a code
# layout — and the LLM's hint is an inference from the surrounding code,
# so the two are not the same kind of evidence and 0.3 would make the
# fallback inert (every fallback store would be below it). The exception
# is `identity_source="llm"` only: a deterministic link at 0.2 still
# fails 0.3, because the producers' bases are 0.3 / 0.4 / 0.5 and a
# number below one of them is not a deterministic answer. The constants
# live in `store/confidence.py` (they are the store subsystem's policy
# about its own links) and are imported lazily by `_join_floors`, for the
# reason `store.resolver._graph_ids` gives: `aicorellator.store` imports
# `graph.models`, so a module-level import here would close a cycle for a
# caller that imports `aicorellator.graph` first.

# The one edge kind a write chain may cross as its *last* hop, at the
# anchor. It is structural (ADR-0021 §4), so the ordinary traversal rule
# would skip it; the anchor hop is not a hop along a path, it is the
# declaration that names the chain's end (ADR-0021 §6). Deeper hops
# still require a traversable flow kind.
ANCHOR_EDGE_KINDS: frozenset[str] = frozenset({"writes_to"})


def _join_floors() -> tuple[float, float, str]:
    """`(floor, llm_floor, llm_source)` for the store join.

    The three values the floor check needs, imported once per call rather
    than per pair. `floor` is this module's own `WEAK_JOIN_FLOOR`; the
    other two are `store.confidence`'s, so the string that marks an LLM
    link and the floor it is judged against cannot drift apart from the
    base the fallback stamps on the link.
    """
    from ..store.confidence import LLM_IDENTITY_SOURCE, WEAK_JOIN_FLOOR_LLM

    return WEAK_JOIN_FLOOR, WEAK_JOIN_FLOOR_LLM, LLM_IDENTITY_SOURCE


def _identity_source(attributes: object) -> str | None:
    """The `identity_source` an edge's JSON `attributes` carry, or None.

    `None` for every deterministic link, which has no such attribute, and
    for an unreadable blob: the column is written by this codebase, and a
    single row written by something else must not take down a query (the
    rule `ClassifierCache.get` applies to its own column). The value is
    only ever compared against `confidence.LLM_IDENTITY_SOURCE`; nothing
    dispatches on it.
    """
    if not attributes:
        return None
    try:
        data = json.loads(attributes)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    source = data.get("identity_source")
    return source if isinstance(source, str) else None


def join_confidence(
    write: float | None, read: float | None
) -> float | None:
    """A pair's confidence: the weaker of its two links (ADR-0021 §7).

    A pair is as strong as the side the analysis is least sure about, so
    the join takes `min`. `None` means one side carries no identity
    confidence at all — the analysis cannot say the two ends name one
    store — and the join then declines to state a confidence rather than
    inventing one.
    """
    if write is None or read is None:
        return None
    return min(write, read)


def is_weak(confidence: float, threshold: float) -> bool:
    """Is a join confidence below the strong/weak boundary?"""
    return confidence < threshold


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

    ADR-0021 adds a second shape: a group joined through a **store**.
    Then `store_id` names the shared node the two chains meet at, and
    `store_confidence` is the join's confidence — `min` of the write
    link's and the read link's `identity_confidence` (§7). A
    store-joined group whose confidence is below the threshold carries
    `weak = True`; it is in the graph but not in the default
    enumeration (§9). A group from the B.2-shaped path has
    `store_id = None`, `store_confidence = None` and `weak = False`:
    it pairs by `untrusted_source`, so there is no identity question to
    answer and nothing to call weak.

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
    # The store the pair was joined through, when it was joined through
    # one (ADR-0021 §6). `None` on a B.2-shaped group.
    store_id: str | None = None
    store_name: str | None = None
    # The join's confidence (`min` of the two links) and whether it is
    # below the strong/weak boundary (ADR-0021 §7, §9).
    store_confidence: float | None = None
    weak: bool = False
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
            "store_id": self.store_id,
            "store_name": self.store_name,
            "store_confidence": self.store_confidence,
            "weak": self.weak,
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

        A store-joined group maps here too, anchored on its read site's
        source. The store itself is not a column yet: `chain_groups` has
        no store reference and ADR-0021 §12 leaves the report's view of
        stores open, so what a stored row says today is which source the
        pair reads, not which store joined it.
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
        include_weak: bool = False,
        store_confidence_threshold: float = (
            DEFAULT_STORE_CONFIDENCE_THRESHOLD
        ),
    ) -> list[ChainGroup]:
        """Enumerate write/read chain pairs around each anchor.

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

        **The write side is whatever the graph contains.** Write chains
        are what the edges say: this method walks the edges that point
        at S, in reverse, and reports the paths that start at an entry
        point. 1.3a emitted `source → reader` only; B.2 added the first
        `writer → source` edges, and B.3c — with B.3b-mcp-catalogue —
        retargeted every one of them to a store, so a source-anchored
        walk finds no write chain on a scanned graph and the store path
        below is where the groups come from. The enumerator is complete
        regardless: it reads the graph, it does not produce edges,
        exactly like `find_chains` — which writer writes into which
        source was a *detection* question (1.3a.1, B.2), not an
        enumeration one.

        **The store path (ADR-0021 §6).** A second enumeration joins the
        same two sides through a `store`: write chains end at the store,
        and the store's `reads_from` edges name the sources that read
        from it, so each (store, source, write, read) tuple is a group
        whose confidence is `min` of the two links'
        `identity_confidence`. The join is by node id — the store is a
        node the two sides share — not a traversal, because a store is
        *between* two paths, not a hop along one. A store-joined group
        below `store_confidence_threshold` is **weak**: reported with
        `weak = True` only when `include_weak` asks for it, and never
        below `WEAK_JOIN_FLOOR`, which is where §9 says there is no link
        at all. B.3a declared this shape and produced none of its edges;
        B.3b's families and B.3c's migration are what produce them, so
        this is the join that yields groups today. The source-anchored
        join is **unchanged** and still runs: B.3c moved the write side
        onto the store model, so a writer's `writes_to` ends at a store
        rather than at the source, and the join by `writes_to →
        untrusted_source` stays the shape it enumerates. It finds no
        write chain on a graph whose writers all target stores, which is
        why the two joins coexist instead of one replacing the other.

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
        if not 0.0 <= store_confidence_threshold <= 1.0:
            raise ValueError(
                "store_confidence_threshold must be between 0.0 and 1.0"
            )

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

            for write_id, write_nodes, _, _ in write_chains:
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

        # The store path (ADR-0021 §6). Enumerated after the pairs above
        # and appended to them: the two joins are different claims (one
        # shares a source, the other shares a store) and their ids differ,
        # so a source reachable both ways yields two groups rather than
        # one deduplicated group. Both joins stay — B.3c migrated the
        # write side onto the store model and did not remove the
        # source-anchored one, which is where a `writes_to` onto the
        # source itself is still read.
        groups.extend(
            self._store_chain_groups(
                entry_ids=entry_ids,
                sink_ids=sink_ids,
                max_hops=max_hops,
                max_write_chains=max_write_chains_per_source,
                max_read_chains=max_read_chains_per_source,
                max_groups=max_groups - len(groups),
                include_weak=include_weak,
                threshold=store_confidence_threshold,
            )
        )

        return groups

    def _store_chain_groups(
        self,
        entry_ids: set[str],
        sink_ids: set[str],
        max_hops: int,
        max_write_chains: int,
        max_read_chains: int,
        max_groups: int,
        include_weak: bool,
        threshold: float,
    ) -> list[ChainGroup]:
        """Groups joined **by store id** (ADR-0021 §6).

        For each active `store`: the write chains that end at it, the
        `reads_from` links from it to the sources that read from it, and
        the read chains out of those sources. Every (store, source,
        write, read) tuple is a group; the store is the anchor both
        sides share, and the join is by id — no edge is traversed to
        reach the other side.

        A chain's write link must carry an `identity_confidence`: the
        `reads_from` link carries the other one, and the pair's
        confidence is the weaker of the two. A link with no confidence
        states no identity, and §5's rule is that unresolved identity
        produces no group, so such a pair is skipped rather than joined
        at some invented confidence. A pair below the applicable floor is
        skipped for the same reason — §9 says there is no link there.

        **Which floor.** `WEAK_JOIN_FLOOR` (0.3), or
        `confidence.WEAK_JOIN_FLOOR_LLM` (0.1) when either link of the
        pair carries `attributes["identity_source"] = "llm"` — ADR-0023
        §5's exception, and the reason a fallback store at 0.2 exists at
        all. The exception is per *pair*, decided by the links' own
        attribute: a pair with one LLM side is an LLM guess about the
        store, and it is the side the join is least sure about. A pair
        with no such attribute is judged exactly as before.

        Read chains are enumerated once per source and reused: the
        `store → source` cardinality is N:1, but a source's read chains
        do not depend on which store named it.

        Order is by store id, then source id, then chain id, so the
        result is a property of the graph and not of the walk.
        """
        if max_groups <= 0:
            return []

        floor, llm_floor, llm_source = _join_floors()

        stores = self._conn.execute(
            """
            SELECT id, name FROM nodes
            WHERE kind = 'store' AND status = 'active'
            ORDER BY id
            """
        ).fetchall()
        if not stores:
            return []

        groups: list[ChainGroup] = []
        by_source: dict[str, list[tuple[str, list[str]]]] = {}

        for store in stores:
            if len(groups) >= max_groups:
                break

            write_chains = self._write_chains_to(
                store["id"], entry_ids, max_hops, max_write_chains
            )
            if not write_chains:
                continue

            for source_id, source_name, read_link, read_source in (
                self._store_read_links(store["id"])
            ):
                if source_id not in by_source:
                    by_source[source_id] = self._read_chains_from(
                        source_id, sink_ids, max_hops, max_read_chains
                    )

                for write_id, write_nodes, write_link, write_source in (
                    write_chains
                ):
                    confidence = join_confidence(write_link, read_link)
                    if confidence is None:
                        continue
                    llm_pair = (
                        write_source == llm_source
                        or read_source == llm_source
                    )
                    if confidence < (llm_floor if llm_pair else floor):
                        continue
                    weak = is_weak(confidence, threshold)
                    if weak and not include_weak:
                        continue

                    for read_id, read_nodes in by_source[source_id]:
                        if len(groups) >= max_groups:
                            break
                        groups.append(
                            ChainGroup(
                                group_id=self._chain_group_id(
                                    source_id,
                                    [write_id],
                                    [read_id],
                                    store_id=store["id"],
                                ),
                                untrusted_source_id=source_id,
                                untrusted_source_name=source_name,
                                write_chain_ids=[write_id],
                                read_chain_ids=[read_id],
                                write_chains=[write_nodes],
                                read_chains=[read_nodes],
                                store_id=store["id"],
                                store_name=store["name"],
                                store_confidence=confidence,
                                weak=weak,
                            )
                        )

        return groups

    def _store_read_links(
        self, store_id: str
    ) -> list[tuple[str, str, float | None, str | None]]:
        """`(source_id, source_name, identity_confidence, identity_source)`.

        One row per read site that reads from this store, ordered by
        source id. Only active links between active nodes: a stale link
        is not evidence about the graph as it stands.

        `identity_source` is the link's producer attribute — `"llm"` for a
        link the store-identity fallback created (ADR-0023 §5) and `None`
        for every deterministic one, which carries no such attribute. The
        join reads it to pick the floor; nothing else does.
        """
        rows = self._conn.execute(
            """
            SELECT e.target_id AS source_id, n.name AS source_name,
                   e.identity_confidence AS identity_confidence,
                   e.attributes AS attributes
            FROM edges e
            JOIN nodes n ON n.id = e.target_id
            WHERE e.source_id = ?
              AND e.kind = 'reads_from'
              AND e.status = 'active'
              AND n.kind = 'untrusted_source'
              AND n.status = 'active'
            ORDER BY e.target_id
            """,
            (store_id,),
        ).fetchall()
        return [
            (
                r["source_id"],
                r["source_name"],
                r["identity_confidence"],
                _identity_source(r["attributes"]),
            )
            for r in rows
        ]

    def _write_chains_to(
        self,
        source_id: str,
        entry_ids: set[str],
        max_hops: int,
        max_chains: int,
    ) -> list[tuple[str, list[str], float | None, str | None]]:
        """Paths `entry_point → ... → source_id`, as (chain, nodes, link).

        The third element is the anchor hop's `identity_confidence` — the
        link that names the anchor, not a hop along the path. It is what
        a store join reads (ADR-0021 §7); the B.2-shaped caller ignores
        it. `None` when the anchor edge carries none. The fourth is that
        same link's `identity_source` attribute (`"llm"` for a fallback
        link, `None` otherwise), which the join reads to pick the floor
        (ADR-0023 §5).

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

        found: list[tuple[str, list[str], float | None, str | None]] = []
        seen: set[str] = set()

        # stack entries: (current, path_nodes, path_edges, link, source)
        stack: list[
            tuple[str, list[str], list[str], float | None, str | None]
        ] = [
            (source_id, [source_id], [], None, None)
        ]

        while stack:
            if len(found) >= max_chains:
                break

            current, path_nodes, path_edges, anchor_link, anchor_source = (
                stack.pop()
            )

            if path_edges and current in entry_ids:
                nodes = list(reversed(path_nodes))
                chain_id = self._chain_id(nodes, list(reversed(path_edges)))
                if chain_id not in seen:
                    seen.add(chain_id)
                    found.append((chain_id, nodes, anchor_link, anchor_source))
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

            # The hop that lands on the anchor is the anchor's own link —
            # `writes_to`, a declaration that names the chain's end
            # (ADR-0021 §6) — so a structural one is crossed here, which
            # the rule below would otherwise refuse. Every deeper hop is
            # an ordinary path hop and must carry a traversable flow
            # kind.
            at_anchor = not path_edges

            # Push in reverse so the first edge is explored first.
            for row in reversed(rows):
                edge = Edge.from_row(row)
                if (
                    at_anchor
                    and edge.kind in ANCHOR_EDGE_KINDS
                ):
                    link = edge.identity_confidence
                    source = edge.attributes.get("identity_source")
                else:
                    # Structural edges are declarations, not paths.
                    if edge.flow_kind not in TRAVERSABLE_FLOW_KINDS:
                        continue
                    link = anchor_link
                    source = anchor_source
                previous = edge.source_id
                # Cycle guard: do not revisit a node in the same path.
                if previous in path_nodes:
                    continue
                stack.append(
                    (
                        previous,
                        path_nodes + [previous],
                        path_edges + [edge.id],
                        link,
                        source,
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
        store_id: str | None = None,
    ) -> str:
        """Deterministic group ID: `chain_group:` + first 16 hex of sha256.

        Over the anchor(s) and the two id sets, sorted, so an id depends
        on the group's membership and not on the order pairs were
        enumerated in: the same graph always yields the same ids. The
        payload is canonical JSON — the convention `_chain_id` uses —
        rather than the parts concatenated with a separator, because
        JSON delimits and types the fields, and no two distinct groups
        can then collide by running together.

        A store-joined group carries `store` as well as `source`: the two
        joins are different claims about the same pair of chains, and the
        id says which one named the group. The key is **absent** — not
        null — on a B.2-shaped group, so ids written before B.3a keep
        their value; B.3c re-ids the groups it migrates.
        """
        payload: dict[str, Any] = {
            "source": source_id,
            "write": sorted(write_chain_ids),
            "read": sorted(read_chain_ids),
        }
        if store_id is not None:
            payload["store"] = store_id
        payload = json.dumps(
            payload,
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

    # --------------------------------------------------------
    # Layer 3 deterministic validation (ADR-0006, A.4)
    # --------------------------------------------------------

    def validate_chain(
        self,
        chain_nodes: list[str],
        chain_edges: list[str],
        entry_kind: str = "entry_point",
        sink_kind: str = "sink",
    ) -> tuple[bool, str, list[str]]:
        """Validate a chain: `(ok, validated_kind, notes)`.

        Four checks, in the order a chain earns them:

          1. existence — every node and every edge is in the graph.
          2. connected — edge `i` runs from `chain_nodes[i]` to
             `chain_nodes[i + 1]`.
          3. typed — the first node is an `entry_kind` and the last is
             a sink.
          4. anchor — if the chain carries an `untrusted_source`, that
             node declares a real source family and has a reader.

        Existence is checked first and on its own: a direction cannot
        be judged against an edge that is not there, so a hallucinated
        chain is rejected on existence without a second, meaningless
        complaint.

        `validated_kind` names the rule level that judged the chain:
        `"semantic"` when every check passed — and then `ok` is true —
        and `"existence"` otherwise. That is the level the chain
        reached, which is why a chain that exists but is incoherent
        reads `"existence"` rather than `"semantic"` (ADR-0006 Layer 3
        before A.4 is exactly the `"existence"` level). The field is
        where the third level is named: `"evidence"` (B.9, ADR-0018
        revision 2), which the evaluator assigns when these checks
        passed and a voter cited an edge of the chain — the only level
        that sets `validated` (`graph.models.VALID_VALIDATED_KINDS`).

        `notes` holds one short, name-based line per failure — never
        source text, the same rule `FlowDecision.evidence` follows.
        It is empty when every check passed.
        """
        notes = self._existence_notes(chain_nodes, chain_edges)
        if notes:
            return False, "existence", notes

        notes.extend(self._connection_notes(chain_nodes, chain_edges))
        notes.extend(self._typing_notes(chain_nodes, entry_kind, sink_kind))
        notes.extend(self._anchor_notes(chain_nodes))
        if notes:
            return False, "existence", notes
        return True, "semantic", []

    def is_chain_valid(
        self,
        chain_nodes: list[str],
        chain_edges: list[str],
    ) -> bool:
        """True if the chain exists AND is structurally coherent.

        The boolean face of `validate_chain`, for callers that want the
        verdict and not the reasons. Before A.4 this meant "every node
        and every edge exists"; it now also means the chain is
        connected, typed and — where it claims one — correctly
        anchored (ADR-0006 Layer 3).
        """
        return self.validate_chain(chain_nodes, chain_edges)[0]

    def is_chain_connected(
        self,
        chain_nodes: list[str],
        chain_edges: list[str],
    ) -> bool:
        """True if edge `i` links `chain_nodes[i]` to `chain_nodes[i+1]`.

        Direction is the point: a chain is a path, so `chain_edges[i]`
        must *go from* node `i` to node `i + 1`, not merely exist
        between them. A reversed edge does not connect the pair, and a
        chain with a hole is not connected either — `len(chain_edges)`
        must be `len(chain_nodes) - 1`.
        """
        return not self._connection_notes(chain_nodes, chain_edges)

    def is_chain_typed(
        self,
        chain_nodes: list[str],
        chain_edges: list[str],
        entry_kind: str = "entry_point",
        sink_kind: str = "sink",
    ) -> bool:
        """True if the chain starts at an `entry_kind` and ends at a sink.

        A sink is a node whose kind is `sink_kind` *or* a node carrying
        one of `patterns.SINK_CAPABILITIES` — the two rules `find_chains`
        enumerates endpoints by (ADR-0009, ADR-0014), so a candidate
        that was enumerated cannot fail this on a technicality.

        Intermediate kinds are deliberately not checked: a chain
        through a `tool` is the normal shape, and `find_chains` already
        refuses to revisit a node. Only the two ends carry the claim.

        `chain_edges` is accepted for symmetry with the other checks
        and is not read — typing is a property of the endpoints.
        """
        return not self._typing_notes(chain_nodes, entry_kind, sink_kind)

    def is_untrusted_anchor_valid(self, source_id: str) -> bool:
        """True if `source_id` is a usable untrusted-source anchor.

        Three things must hold, and all three are about the *claim* the
        anchor makes:

          - the node exists and its kind is `untrusted_source`;
          - it declares a `source_type` in `UNTRUSTED_SOURCE_TYPES`, the
            families that actually have a detector (ADR-0010);
          - at least one `has_access_to` edge leaves it for a node
            carrying `reads_untrusted` — an anchor with no reader is a
            label, not a channel.

        The reader is looked up in the graph rather than in the chain:
        this check is about the anchor's structure, and a caller with a
        chain validates the chain separately. Status is not filtered,
        for the same reason `validate_edges` does not filter it —
        Layer 3 answers "is this in the graph", and the graph is
        rebuilt per scan (ADR-0003).
        """
        row = self._conn.execute(
            "SELECT kind, attributes FROM nodes WHERE id = ?", (source_id,)
        ).fetchone()
        if row is None or row["kind"] != "untrusted_source":
            return False

        attributes = json.loads(row["attributes"] or "{}")
        if attributes.get("source_type") not in UNTRUSTED_SOURCE_TYPES:
            return False

        readers = self._conn.execute(
            """
            SELECT n.capabilities AS capabilities
            FROM edges e JOIN nodes n ON n.id = e.target_id
            WHERE e.source_id = ? AND e.kind = 'has_access_to'
            """,
            (source_id,),
        ).fetchall()
        for reader in readers:
            capabilities = json.loads(reader["capabilities"] or "[]")
            if "reads_untrusted" in capabilities:
                return True
        return False

    # --------------------------------------------------------
    # Layer 3 note builders — one line per failure, no source text
    # --------------------------------------------------------

    def _existence_notes(
        self,
        chain_nodes: list[str],
        chain_edges: list[str],
    ) -> list[str]:
        """Which nodes and which edges of the chain are not in the graph.

        The A.4 predecessor of `is_chain_valid`, unchanged in what it
        accepts: every node and every edge must exist.
        """
        if not chain_nodes:
            return ["no nodes"]
        if not chain_edges:
            return ["no edges"]

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

        notes = [
            f"missing node {node_id}"
            for node_id in dict.fromkeys(chain_nodes)
            if node_id not in found_nodes
        ]
        _, missing_edges = self.validate_edges(chain_edges)
        notes.extend(f"missing edge {edge_id}" for edge_id in missing_edges)
        return notes

    def _connection_notes(
        self,
        chain_nodes: list[str],
        chain_edges: list[str],
    ) -> list[str]:
        """Which hops of the chain do not link their two nodes."""
        notes: list[str] = []
        expected = len(chain_nodes) - 1
        if len(chain_edges) != expected:
            notes.append(
                f"hop count: {len(chain_edges)} edge(s) for "
                f"{len(chain_nodes)} node(s), expected {expected}"
            )

        edges = self._edges_by_id(chain_edges)
        for i in range(min(len(chain_edges), expected)):
            edge_id = chain_edges[i]
            source, target = chain_nodes[i], chain_nodes[i + 1]
            edge = edges.get(edge_id)
            if edge is None:
                # Check 1 requires the edge to *exist* as well as to
                # point the right way, so a standalone `is_chain_connected`
                # call catches a hole here. `validate_chain` never gets
                # this far with one: existence is checked first.
                notes.append(
                    f"disconnected: edge {edge_id} is not in the graph"
                )
                continue
            if edge.source_id != source or edge.target_id != target:
                notes.append(
                    f"disconnected: edge {edge_id} links "
                    f"{edge.source_id} → {edge.target_id}, "
                    f"expected {source} → {target}"
                )
        return notes

    def _typing_notes(
        self,
        chain_nodes: list[str],
        entry_kind: str,
        sink_kind: str,
    ) -> list[str]:
        """Whether the chain's two ends have the kinds a chain claims."""
        if not chain_nodes:
            return ["no nodes"]

        nodes = self._nodes_by_id(chain_nodes)
        notes: list[str] = []

        first = nodes.get(chain_nodes[0])
        if first is None or first.kind != entry_kind:
            notes.append(
                f"first node {chain_nodes[0]} is not a {entry_kind}"
            )

        last = nodes.get(chain_nodes[-1])
        if last is None or (
            last.kind != sink_kind
            and not SINK_CAPABILITIES.intersection(last.capabilities)
        ):
            notes.append(f"last node {chain_nodes[-1]} is not a {sink_kind}")
        return notes

    def _anchor_notes(self, chain_nodes: list[str]) -> list[str]:
        """Which `untrusted_source` nodes fail the anchor check.

        A chain with no anchor has nothing to check and yields no notes:
        the check applies where an anchor is claimed, not everywhere. A
        chain that is not part of an injection group never mentions a
        source, and that is not a defect.
        """
        nodes = self._nodes_by_id(chain_nodes)
        return [
            f"untrusted anchor {node_id} is not a usable source"
            for node_id in dict.fromkeys(chain_nodes)
            if nodes.get(node_id) is not None
            and nodes[node_id].kind == "untrusted_source"
            and not self.is_untrusted_anchor_valid(node_id)
        ]

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

    def _edges_by_id(self, edge_ids: list[str]) -> dict[str, Edge]:
        """Edges by id, for the ids that exist.

        Layer 3's connection check needs each hop's two endpoints, not
        the whole row, and it needs them for a handful of ids at most.
        A missing id is simply absent — the existence check has already
        reported it, and the connection check has nothing to say about
        an edge that is not there.
        """
        if not edge_ids:
            return {}
        out: dict[str, Edge] = {}
        chunk_size = 900
        for i in range(0, len(edge_ids), chunk_size):
            chunk = edge_ids[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT * FROM edges WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                edge = Edge.from_row(row)
                out[edge.id] = edge
        return out

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
