"""Markdown report renderer.

Reads the graph via QueryLayer and produces a single Markdown
document. Structure:

  1. Header (host, timestamp, phase)
  2. Summary (node/edge/chain counts)
  3. Nodes by kind (table)
  4. Edges by kind (table)
  5. Chains:
     - if evaluated chains exist (Phase 3) → render them
     - else → render candidate chains (Phase 1 enumeration)
  6. Chain groups:
     - if `chain_groups` rows exist (1.3c selection / 1.3e verdicts)
       → render them
     - else → omit the section: a group exists only once
       prioritization has stored a row, and a corpus where 1.3c has
       never run has nothing to say here

Everything is deterministic — same graph, same report.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from ..graph.models import Chain, ChainGroupRecord, Edge, Node
from ..graph.query import MAX_GROUPS_SCORED
from ..graph.store import GraphStore


# Cap on how many chains to render per entry point.
MAX_CHAINS_PER_ENTRY = 30

# How much of a group's reasoning to render. The linear section prints
# the whole paragraph; a group's reasoning is a merged voter transcript
# and there can be a hundred groups, so it is cut here instead
# (`run_eval.py` does the same for its summary lines).
MAX_REASONING_CHARS = 300


def render_report(store: GraphStore, host_id: str = "localhost") -> str:
    """Render a full Markdown report for a host."""
    nodes = store.nodes.list()
    edges = store.edges.list()
    evaluated = store.chains.list(status="active")
    candidates = store.query.find_chains() if not evaluated else []
    groups = store.chain_groups.list(status="active")

    sections: list[str] = []
    sections.append(_header(host_id))
    sections.append(_summary(nodes, edges, evaluated, candidates))
    sections.append(_nodes_by_kind(nodes))
    sections.append(_edges_by_kind(edges))

    if evaluated:
        sections.append(_evaluated_chains_section(store, evaluated))
    elif candidates:
        sections.append(_candidate_chains_section(store, candidates))
    else:
        sections.append("## Chains\n\n_No chains found._")

    # Omitted, not rendered empty: no rows means 1.3c has not run here,
    # which is the normal state of a Phase 1 scan.
    if groups:
        sections.append(_chain_groups_section(store, groups))

    return "\n\n".join(sections) + "\n"


# ============================================================
# Sections
# ============================================================

def _header(host_id: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"""# AICorellator Report

**Host:** `{host_id}`
**Generated:** {now}"""


def _summary(
    nodes: list[Node],
    edges: list[Edge],
    evaluated: list[Chain],
    candidates: list,
) -> str:
    return f"""## Summary

| Metric | Count |
|---|---|
| Nodes | {len(nodes)} |
| Edges | {len(edges)} |
| Evaluated chains | {len(evaluated)} |
| Candidate chains | {len(candidates)} |"""


def _nodes_by_kind(nodes: list[Node]) -> str:
    counts = Counter(n.kind for n in nodes)
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    lines = ["## Nodes by kind", "", "| Kind | Count |", "|---|---|"]
    for kind, count in rows:
        lines.append(f"| `{kind}` | {count} |")
    return "\n".join(lines)


def _edges_by_kind(edges: list[Edge]) -> str:
    counts = Counter(e.kind for e in edges)
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    lines = ["## Edges by kind", "", "| Kind | Count |", "|---|---|"]
    for kind, count in rows:
        lines.append(f"| `{kind}` | {count} |")
    return "\n".join(lines)


# ============================================================
# Candidate chains (Phase 1 enumeration)
# ============================================================

def _candidate_chains_section(store: GraphStore, candidates: list) -> str:
    """Render Phase 1 candidate chains.

    Candidate chains are deterministic structural paths enumerated
    by the Query Layer. They are NOT evaluated — no LLM verdict,
    no confidence. Evaluation happens in Phase 3.
    """
    by_entry: dict[str, list] = defaultdict(list)
    for chain in candidates:
        if not chain.nodes:
            continue
        entry_id = chain.nodes[0]
        by_entry[entry_id].append(chain)

    entry_names: dict[str, str] = {}
    for entry_id in by_entry:
        node = store.nodes.get(entry_id)
        entry_names[entry_id] = node.name if node else entry_id

    lines: list[str] = []
    lines.append("## Candidate chains (Phase 1 enumeration)")
    lines.append("")
    lines.append(
        "Deterministic structural paths from entry points to sinks, "
        "enumerated by `find_chains`. Not yet evaluated by LLM."
    )
    lines.append("")

    # Summary table
    lines.append(f"### By entry point ({len(by_entry)} entries)")
    lines.append("")
    lines.append("| Entry point | Chains |")
    lines.append("|---|---|")
    for entry_id, chains in sorted(by_entry.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"| `{entry_names[entry_id]}` | {len(chains)} |")
    lines.append("")

    # Detailed chains per entry point
    for entry_id, chains in sorted(by_entry.items(), key=lambda kv: -len(kv[1])):
        name = entry_names[entry_id]
        lines.append(f"### `{name}` — {len(chains)} chains")
        lines.append("")
        for i, chain in enumerate(chains[:MAX_CHAINS_PER_ENTRY], start=1):
            path = _render_candidate_path(store, chain)
            lines.append(f"**Chain {i}** ({chain.length} hops)")
            lines.append("")
            lines.append(f"> {path}")
            lines.append("")
        if len(chains) > MAX_CHAINS_PER_ENTRY:
            remaining = len(chains) - MAX_CHAINS_PER_ENTRY
            lines.append(f"_... and {remaining} more chains_")
            lines.append("")

    return "\n".join(lines)


def _render_candidate_path(store: GraphStore, chain) -> str:
    """Render a CandidateChain as `kind:name → kind:name → ...`."""
    parts: list[str] = []
    for node_id in chain.nodes:
        node = store.nodes.get(node_id)
        if node:
            parts.append(f"`{node.kind}:{node.name}`")
        else:
            parts.append(f"`{node_id}`")
    return " → ".join(parts)


# ============================================================
# Evaluated chains (Phase 3)
# ============================================================

def _evaluated_chains_section(store: GraphStore, chains: list[Chain]) -> str:
    """Render Phase 3 evaluated chains, grouped by entry point."""
    # Group by first node (entry point)
    by_entry: dict[str, list[Chain]] = defaultdict(list)
    for chain in chains:
        if not chain.chain_nodes:
            continue
        by_entry[chain.chain_nodes[0]].append(chain)

    entry_names: dict[str, str] = {}
    for entry_id in by_entry:
        node = store.nodes.get(entry_id)
        entry_names[entry_id] = node.name if node else entry_id

    lines: list[str] = []
    lines.append("## Evaluated chains (Phase 3)")
    lines.append("")
    lines.append(
        f"Chains evaluated by the LLM ensemble. "
        f"Total: {len(chains)}."
    )
    lines.append("")

    # Summary by verdict
    verdict_counts = Counter(c.verdict for c in chains)
    lines.append("### By verdict")
    lines.append("")
    lines.append("| Verdict | Count |")
    lines.append("|---|---|")
    for verdict, count in sorted(verdict_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{verdict}` | {count} |")
    lines.append("")

    # Summary by entry point
    lines.append("### By entry point")
    lines.append("")
    lines.append("| Entry point | Chains | Exploitable | Not exploitable | Uncertain |")
    lines.append("|---|---|---|---|---|")
    for entry_id, entry_chains in sorted(by_entry.items(), key=lambda kv: -len(kv[1])):
        name = entry_names[entry_id]
        exploitable = sum(1 for c in entry_chains if c.verdict == "exploitable")
        not_expl = sum(1 for c in entry_chains if c.verdict == "not_exploitable")
        uncertain = sum(1 for c in entry_chains if c.verdict == "uncertain")
        lines.append(f"| `{name}` | {len(entry_chains)} | {exploitable} | {not_expl} | {uncertain} |")
    lines.append("")

    # Detailed chains per entry point (only exploitable ones get reasoning)
    for entry_id, entry_chains in sorted(by_entry.items(), key=lambda kv: -len(kv[1])):
        name = entry_names[entry_id]
        lines.append(f"### `{name}` — {len(entry_chains)} chains")
        lines.append("")

        # Sort by confidence descending
        sorted_chains = sorted(entry_chains, key=lambda c: -c.confidence)

        for i, chain in enumerate(sorted_chains, start=1):
            path = _render_chain_path(store, chain)
            validated = "✓" if chain.validated else "✗"
            lines.append(
                f"**Chain {i}** — `{chain.verdict}` "
                f"(conf={chain.confidence:.2f}, {validated}, {chain.length} hops)"
            )
            lines.append("")
            lines.append(f"> {path}")
            lines.append("")
            if chain.reasoning:
                # Indent reasoning as a quote
                reasoning = chain.reasoning.strip().replace("\n", "\n> ")
                lines.append(f"> **Reasoning:** {reasoning}")
                lines.append("")

    return "\n".join(lines)

def _render_chain_path(store: GraphStore, chain: Chain) -> str:
    """Render a Chain (evaluated) as `kind:name → kind:name → ...`."""
    parts: list[str] = []
    for node_id in chain.chain_nodes:
        node = store.nodes.get(node_id)
        if node:
            parts.append(f"`{node.kind}:{node.name}`")
        else:
            parts.append(f"`{node_id}`")
    return " → ".join(parts)


# ============================================================
# Chain groups (ADR-0010 — indirect prompt injection, 1.3e)
# ============================================================

def _chain_groups_section(
    store: GraphStore, groups: list[ChainGroupRecord]
) -> str:
    """Render the selected chain groups and their verdicts.

    A row exists because 1.3c selected it and stored it; a verdict exists
    because 1.3e evaluated it. The two are kept apart throughout, because
    they are paid for separately:

      - the summary counts `validated` groups only. A group whose verdict
        came back and whose edges did not survive Layer 3 is not a
        finding, and a group that has not been evaluated at all is not a
        rejected one either.
      - rejected groups (evaluated, not validated) get their own list, so
        "we looked and did not believe it" can be told apart from "we did
        not look".
    """
    evaluated = [g for g in groups if g.verdict is not None]
    accepted = [g for g in evaluated if g.validated]
    rejected = [g for g in evaluated if not g.validated]
    pending = [g for g in groups if g.verdict is None]

    lines: list[str] = []
    lines.append("## Chain groups (Phase 3)")
    lines.append("")
    lines.append(
        "Pairs of chains that meet through an `untrusted_source`: one "
        "**write chain** plants content in the channel, one **read "
        "chain** reaches a sink from it. Neither side is dangerous alone "
        "(ADR-0010), so the pair is what the ensemble evaluates. Rows are "
        "1.3c's prioritization selection; verdicts are 1.3e's."
    )
    lines.append("")

    lines.append("| Metric | Count |")
    lines.append("|---|---|")
    lines.append(f"| Groups selected | {len(groups)} |")
    lines.append(f"| Evaluated | {len(evaluated)} |")
    lines.append(f"| Validated (Layer 3) | {len(accepted)} |")
    lines.append(f"| Not evaluated yet | {len(pending)} |")
    lines.append("")

    # Only validated groups are counted: an unvalidated verdict is not a
    # finding, and the tables below are the report's "what did we find".
    lines.append("### By verdict (validated groups)")
    lines.append("")
    lines.append("| Verdict | Count |")
    lines.append("|---|---|")
    verdict_counts = Counter(g.verdict for g in accepted)
    if verdict_counts:
        for verdict, count in sorted(verdict_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"| `{verdict}` | {count} |")
    else:
        lines.append("| _none_ | 0 |")
    lines.append("")

    lines.append("### By priority (validated groups)")
    lines.append("")
    lines.append("| Priority | Count |")
    lines.append("|---|---|")
    priority_counts = Counter(g.priority for g in accepted)
    if priority_counts:
        for priority, count in sorted(
            priority_counts.items(), key=lambda kv: (kv[0] is None, kv[0] or "")
        ):
            lines.append(f"| `{priority}` | {count} |")
    else:
        lines.append("| _none_ | 0 |")
    lines.append("")

    if rejected:
        lines.append(
            f"**Rejected by Layer 3: {len(rejected)}.** These were "
            "evaluated and did not hold up: an edge of the group, or one a "
            "voter cited, is not in the graph. They are not counted above."
        )
        lines.append("")
        for record in rejected:
            lines.append(
                f"- `{record.id}` — `{record.verdict}` "
                f"(conf={record.confidence:.2f}), score={record.score:g}"
            )
        lines.append("")

    paths = group_chain_paths(store, groups)
    for record in groups:
        lines.append(_group_detail(store, record, paths))
        lines.append("")

    return "\n".join(lines)


def _group_detail(
    store: GraphStore,
    record: ChainGroupRecord,
    paths: dict[str, list[str]],
) -> str:
    """One group's block: the pair, the score, the verdict."""
    source = store.nodes.get(record.untrusted_source_id)
    if source is None:
        source_label = f"`{record.untrusted_source_id}`"
        source_type = "unknown"
    else:
        source_label = f"`{source.name}`"
        source_type = source.attributes.get("source_type", "unknown")

    verdict = record.verdict if record.verdict is not None else "unevaluated"
    validated = "✓" if record.validated else "✗"

    lines = [f"### `{record.name or record.id}` — `{verdict}`", ""]
    lines.append(
        f"`{record.id}` · source {source_label} ({source_type}) · "
        f"score={record.score:g} · priority={record.priority or 'none'} · "
        f"conf={record.confidence:.2f} · validated={validated}"
    )
    lines.append("")
    lines.append(
        f"- **Write chain:** "
        f"{_chain_paths(store, record.write_chain_ids, paths)}"
    )
    lines.append(
        f"- **Read chain:** "
        f"{_chain_paths(store, record.read_chain_ids, paths)}"
    )
    lines.append("")

    if record.reasoning:
        reasoning = record.reasoning.strip()
        if len(reasoning) > MAX_REASONING_CHARS:
            reasoning = reasoning[:MAX_REASONING_CHARS] + "…"
        reasoning = reasoning.replace("\n", "\n> ")
        lines.append(f"> **Reasoning:** {reasoning}")
        lines.append("")

    return "\n".join(lines)


def _chain_paths(
    store: GraphStore,
    chain_ids: list[str],
    paths: dict[str, list[str]],
) -> str:
    """Render a group's chain ids as paths, falling back to the ids.

    A row stores ids, not paths (ADR-0010 keeps the enumeration
    transient), so the paths are recovered — see `group_chain_paths` — and
    an id whose path was not recovered is printed as itself rather than
    dropped.
    """
    rendered = []
    for chain_id in chain_ids:
        nodes = paths.get(chain_id)
        if nodes:
            rendered.append(_render_node_path(store, nodes))
        else:
            rendered.append(f"`{chain_id}`")
    return " + ".join(rendered) if rendered else "_none_"


def _render_node_path(store: GraphStore, nodes: list[str]) -> str:
    """Render a node sequence as `kind:name → kind:name → ...`."""
    parts: list[str] = []
    for node_id in nodes:
        node = store.nodes.get(node_id)
        if node:
            parts.append(f"`{node.kind}:{node.name}`")
        else:
            parts.append(f"`{node_id}`")
    return " → ".join(parts)


def group_chain_paths(
    store: GraphStore, groups: list[ChainGroupRecord]
) -> dict[str, list[str]]:
    """Recover `chain_id → node sequence` for the chains these groups name.

    A `chain_groups` row carries ids, not paths, so the paths are
    re-derived from the graph by enumerating the groups once with the same
    caps prioritization uses. Enumeration is deterministic and cheap — no
    LLM, bounded by `MAX_GROUPS_SCORED` — and it stops as soon as every
    wanted chain has been found, which for a set of groups sharing a few
    sources is a handful of sources' worth of walking.

    Shared by the report (which draws the pair as a path) and the bundle
    (which serializes it the way `chains.json` serializes node names), so
    the two cannot disagree about what a chain looks like.

    An id that is not recovered (a row written by a run with wider caps, a
    group the enumerator now truncates) simply stays an id in the output:
    a report that names a chain it cannot draw is still truthful.
    """
    wanted = {
        chain_id
        for record in groups
        for chain_id in (*record.write_chain_ids, *record.read_chain_ids)
    }
    if not wanted:
        return {}

    found: dict[str, list[str]] = {}
    for group in store.query.find_chain_groups(max_groups=MAX_GROUPS_SCORED):
        for chain_id, nodes in zip(group.write_chain_ids, group.write_chains):
            if chain_id in wanted:
                found.setdefault(chain_id, list(nodes))
        for chain_id, nodes in zip(group.read_chain_ids, group.read_chains):
            if chain_id in wanted:
                found.setdefault(chain_id, list(nodes))
        if len(found) == len(wanted):
            break
    return found