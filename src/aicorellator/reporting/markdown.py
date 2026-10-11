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

Everything is deterministic — same graph, same report.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from ..graph.models import Chain, Edge, Node
from ..graph.store import GraphStore


# Cap on how many chains to render per entry point.
MAX_CHAINS_PER_ENTRY = 30


def render_report(store: GraphStore, host_id: str = "localhost") -> str:
    """Render a full Markdown report for a host."""
    nodes = store.nodes.list()
    edges = store.edges.list()
    evaluated = store.chains.list(status="active")
    candidates = store.query.find_chains() if not evaluated else []

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