# AICorellator

**AI Infrastructure Attack Surface Correlator**

A static analyzer that finds multi-hop attack chains in AI systems.

Not a CVE scanner. Not SAST. Not a runtime monitor. AICorellator builds a graph of AI components — agents, LLM endpoints, MCP servers, tools, sinks, guardrails — and uses a local LLM voting ensemble to evaluate the exploitability of chains through that graph.

---

## Quick Start

### 1. Requirements

- **Python 3.11+**
- **uv** — provides `uvx`, used to run `tree-sitter-analyzer` as a subprocess.

  Install `uv`:

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

  Or via pip:

  ```bash
  pip install uv
  ```

- **Optional:** LMStudio, Ollama, or vLLM for Phase 3 (chain evaluation). Without a local LLM runtime, Phase 1 (graph construction) still works; Phase 3 does not.

### 2. Install

```bash
git clone https://github.com/Malinowiy/AICorellator.git
cd AICorellator
pip install -e .
```

This installs the package in editable mode. It does not install `uvx` or a local LLM runtime; those are separate steps.

### 3. Scan a target

```bash
python run_scan.py --target /path/to/target
```

The target can be any directory containing source code: a repository, a folder, an unpacked Docker image, or a mounted filesystem.

Scanning creates a workspace under `./aicorellator_data/`:

```
aicorellator_data/
├── aicorellator.db          # SQLite graph
├── reports/
│   └── current/
│       ├── report.md        # human-readable report
│       └── chains.json      # machine-readable chains
├── config/
│   └── models.yaml          # ensemble config (auto-created)
├── cache/
│   └── tree-sitter/         # TSA cache
└── logs/
    └── aicorellator.log
```

### 4. Read the report

```bash
cat aicorellator_data/reports/current/report.md
```

The report lists nodes by kind, edges by kind, and candidate chains — deterministic structural paths from entry points to sinks. At this stage, chains are **candidates**, not evaluated findings. Phase 3 (see below) evaluates them.

### 5. Evaluate chains (Phase 3, optional)

Requires a local LLM runtime (LMStudio, Ollama, or vLLM).

```bash
python run_eval.py --mode fast --limit 10
```

- `--mode fast` — one voter, no judge. Quick screening.
- `--mode solo` — one voter with reasoning. Deep single-pass.
- `--mode full` — N voters + judge. Ensemble with final verdict.
- `--limit` — how many chains to evaluate.

The ensemble is configured in `aicorellator_data/config/models.yaml`. Edit it to point at your local models.

### 6. Regenerate the report

After evaluation, regenerate the report without rescanning:

```bash
python run_report.py
```

### Useful flags

```bash
python run_scan.py --target ./app --workdir /tmp/scan-1    # custom workspace
python run_scan.py --target ./app --clean                  # wipe workspace first
python run_scan.py --target ./app --frameworks langchain,fastmcp  # enable specific detectors
```

### What to do if the graph is empty

- **No nodes at all** — check that `uvx` is installed (`uvx --version`). Without it, TSA does not run, and no symbols are extracted.
- **Nodes but no edges** — the target may not match any known framework pattern. Check the log at `aicorellator_data/logs/aicorellator.log`.
- **No chains** — the graph may have no entry points (HTTP routes, CLI scripts, Streamlit apps, or MCP servers) or no sinks. Check the report's node counts.

---

## What It Finds

A chain in which untrusted input reaches a dangerous action through AI components. Each node alone is legitimate. The chain is not.

Beyond direct chains, AICorellator is designed to detect:

- **Lethal trifecta** — a node that simultaneously reads untrusted data, accesses secrets, and can send data out.
- **Multi-chain groups** — indirect prompt injection, where one chain plants data in an untrusted source and another reads it and reaches a sink.

Both are on the roadmap (see `Documentation/BACKLOG.md`); the graph primitives that support them are in place, but the operations are not yet implemented.

---

## What It Is Not

- **Not a network scanner.** AICorellator reads local sources: code, configs, container layers.
- **Not a runtime monitor.** Runtime scanners are planned for Phase 2.
- **Not a replacement for SAST/DAST/SCA.** Those are inputs; AICorellator correlates their findings into chains.
- **Not a CVE database.** CVE data is external and optional.

---

## Pipeline

**Phase 1 — Graph construction (working).**
Sources are scanned, parsed with tree-sitter-analyzer, classified by framework detectors and capability extraction, normalized into a canonical Node/Edge model, and written to a SQLite graph store.

**Phase 2 — Enrichment (planned).**
External scanners attach findings to nodes. Untrusted sources are detected: RAG corpora, MCP tool descriptions, email inboxes, user uploads.

**Phase 3 — Evaluation (working).**
Candidate chains are enumerated deterministically. A local LLM voting ensemble evaluates each chain. Every reported edge is validated against the graph, so hallucinated chains are impossible.

---

## Framework Detectors

Level 1 classification. Per-file, framework-specific.

| Framework | Signature | Status |
|---|---|---|
| LangChain | `langchain.agents`, `AgentExecutor`, `create_agent` | Implemented |
| LangGraph | `langgraph.graph`, `StateGraph` | Implemented |
| FastMCP | `mcp.server.fastmcp`, `FastMCP` | Implemented |
| MCP Python SDK | `mcp.server`, `Server` | Implemented |
| OpenAI Agents SDK | `openai.agents`, `Agent`, `Runner` | Planned |

Level 2 — capability extraction from AST calls. Level 3 — name-based heuristics tuned for FinBot-style projects.

See `Documentation/ADR/ADR_011.md` for the full design.

---

## Modes

Phase 3 evaluation runs in one of four modes:

| Mode | Voters | Judge | Purpose |
|---|---|---|---|
| `fast` | 1 | no | Quick screening |
| `solo` | 1 with reasoning | no | Deep single-pass |
| `full` | N | yes | Ensemble with final verdict |
| `custom` | as configured | as configured | Manual control |

The ensemble is configured explicitly — models, weights, timeouts, reasoning effort. No auto-discovery. All analysis runs locally via LMStudio, Ollama, or vLLM. No external API calls.

---

## Design Principles

| Principle | Meaning |
|---|---|
| **Graph is alive** | Single mutable graph per scan. Nodes carry `confidence`; stale nodes lose confidence gradually. |
| **SQLite is the store** | No external services. Schema is recreated per scan. |
| **Scanners run on vertices** | Task queue. Each vertex gets scanned asynchronously (Phase 2). |
| **Evaluation is LLM-only** | Enumeration is deterministic; evaluation is LLM. No regex for evaluation. |
| **Hallucinations are impossible** | Every reported chain edge is validated against the graph. |
| **Reports on demand** | Evaluation is expensive. Reports are generated on request. |
| **False positives accepted** | Detection prefers a spurious edge over a missing one. Phase 3 filters. |

---

## Status

**Phase 1 MVP.** Validated against seven real targets:

| Target | Nodes | Edges | Chains |
|---|---|---|---|
| finbot-ctf | 283 | 93 | 182 |
| NMB-DAMN-Vulnerable-Support-Agent | 26 | 26 | 16 |
| dvmcp | 30 | 32 | 24 |
| mcploitable | 56 | 33 | 5 |
| AI-Vulnerabilities-Playground | 37 | 2 | 2 |
| damn-vulnerable-llm-agent | 9 | 6 | 1 |
| ai-goat | 10 | 7 | 7 |

- **Working:** ingestion, parsing, framework detectors, capability extraction, cross-file resolution, MCP entry synthesis, CLI and Streamlit entry points, Phase 3 evaluation ensemble, report generation.
- **Planned:** untrusted source detection, chain groups, guardrails, OpenAI Agents SDK detector, runtime enrichment.
- **Deferred:** schema migrations, SCIP integration, standalone executables, Docker image.
- **Backlog:** see `Documentation/BACKLOG.md`.

---

## Architecture

Architectural decisions are recorded in `Documentation/ADR/`. System structure is documented as C4 diagrams in `Documentation/Architecture/` — system context (L1), containers (L2), and components per phase (L3).

---

## License

AICorellator is released under the **PolyForm Noncommercial License 1.0.0**. Noncommercial use is free. Commercial use requires a paid license.

- **Noncommercial use** — free, under the terms of `LICENSE`.
- **Commercial use** — requires a commercial license. See `COMMERCIAL.md` for pricing, contribution-based discounts, and how to purchase.
- **Contributions** — welcome, under the terms of `CONTRIBUTING.md`.