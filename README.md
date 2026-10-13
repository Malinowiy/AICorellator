# AICorellator

**AI Infrastructure Attack Surface Correlator**

A static analyzer that finds multi-hop attack chains in AI systems.

Not a CVE scanner. Not SAST. Not a runtime monitor. AICorellator
builds a graph of AI components — agents, LLM endpoints, MCP servers,
tools, sinks, guardrails, untrusted sources — and uses a local LLM
voting ensemble to evaluate the exploitability of chains through
that graph.

---

## What It Finds

A chain in which untrusted input reaches a dangerous action through
AI components. Each node alone is legitimate. The chain is not.

Beyond direct chains, AICorellator detects:

- **Lethal trifecta** — a node that simultaneously reads untrusted
  data, accesses secrets, and can send data out.
- **Multi-chain groups** — indirect prompt injection, where one
  chain plants data in an untrusted source and another reads it
  and reaches a sink.

---

## What It Is Not

- **Not a network scanner.** AICorellator reads local sources:
  code, configs, container layers.
- **Not a runtime monitor.** However runtime scanners are planned.
- **Not a replacement for SAST/DAST/SCA.** Those are inputs;
  AICorellator correlates their findings into chains.
- **Not a CVE database.** CVE data is external, and optional.

---

## Pipeline

**Phase 1 — Graph construction (working).**
Sources are scanned, parsed with tree-sitter-analyzer, classified
by framework detectors and capability extraction, normalized into
a canonical Node/Edge model, and written to a SQLite graph store.

**Phase 2 — Enrichment (planned).**
External scanners attach findings to nodes. Untrusted sources are
detected: RAG corpora, MCP tool descriptions, email inboxes,
user uploads.

**Phase 3 — Evaluation (working).**
Candidate chains are enumerated deterministically. A local LLM
voting ensemble evaluates each chain. Every reported edge is
validated against the graph, so hallucinated chains are impossible.

---

## Modes

| Mode | Voters | Judge | Purpose |
|---|---|---|---|
| `fast` | 1 | no | Quick screening |
| `solo` | 1 with reasoning | no | Deep single-pass |
| `full` | N | yes | Ensemble with final verdict |
| `custom` | as configured | as configured | Manual control |

The ensemble is configured explicitly — models, weights, timeouts,
reasoning effort. No auto-discovery. All analysis runs locally via
LMStudio, Ollama, or vLLM. No external API calls.

---

## Design Principles

| Principle | Meaning |
|---|---|
| **Graph is alive** | Single mutable graph per scan. Nodes carry `confidence`; stale nodes lose confidence gradually. |
| **SQLite is the store** | No external services. Schema is recreated per scan. |
| **Scanners run on vertices** | Task queue. Each vertex gets scanned asynchronously. |
| **Evaluation is LLM-only** | Enumeration is deterministic; evaluation is LLM. No regex for evaluation. |
| **Hallucinations are impossible** | Every reported chain edge is validated against the graph. |
| **Reports on demand** | Evaluation is expensive. Reports are generated on request. |

---

## Status

**Early alpha.**

Working:
- Phase 1: ingestion, parsing, classification, normalization,
  graph store, entry point detection, LLM config extraction.
- Phase 3: LLM voting ensemble (fast, solo, full modes), report
  generation.

Planned:
- Phase 2: scanner enrichment queue, framework detectors,
  capability extraction, untrusted source detection.
- Multi-chain group analysis.

---

## Requirements

- Python 3.11+
- `uvx` (for tree-sitter-analyzer subprocess)
- Optional: LMStudio, Ollama, or vLLM for Phase 3 evaluation

---

## Quick Start

Three commands:

1. **Scan a target** — build the graph.
2. **Evaluate chains** — run the LLM ensemble.
3. **Generate the report** — produce the Markdown output.

All outputs live in `aicorellator_data/`: the SQLite graph,
the report bundle, and the ensemble config. See the scripts in
the repository root for exact commands.

---

## Architecture

Architectural decisions are recorded in `Documentation/ADR/`.
System structure is documented as C4 diagrams in
`Documentation/Architecture/` — system context (L1), containers
(L2), components (L3 per phase).

---

## License

AICorellator is released under the **PolyForm Noncommercial License
1.0.0**. Noncommercial use is free. Commercial use requires a paid
license.

- **Noncommercial use** — free, under the terms of `LICENSE`.
- **Commercial use** — requires a commercial license. See
  `COMMERCIAL.md` for pricing, contribution-based discounts, and how
  to purchase.
- **Contributions** — welcome, under the terms of `CONTRIBUTING.md`.

For commercial licensing inquiries: **crimsonmalinowiy@gmail.com**.