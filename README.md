# AICorellator

**AI Infrastructure Attack Surface Correlator**

A static analyzer that builds a graph of AI infrastructure components — agents, LLM endpoints, MCP servers, tools, sinks, guardrails — and evaluates attack chains through that graph with a local LLM ensemble.

Not a CVE scanner. Not a runtime monitor. Not a replacement for SAST/DAST/SCA. A correlator: it takes the surface of control that a static scan can see, builds it into a graph, and asks an LLM ensemble whether a path through that graph is exploitable.

---

## Status

**v0.1 — prototype (2026-10-22).** Phase 1 (graph construction) works. Phase 3 (LLM evaluation) works. Phase 2 (runtime enrichment) is an interface without implementations.

The prototype has been validated against seven real targets:

| Target | Nodes | Edges | Chains |
|---|---|---|---|
| finbot-ctf | 283 | 93 | 182 |
| NMB-DAMN-Vulnerable-Support-Agent | 26 | 26 | 16 |
| dvmcp | 30 | 32 | 24 |
| mcploitable | 56 | 33 | 5 |
| AI-Vulnerabilities-Playground | 37 | 2 | 2 |
| damn-vulnerable-llm-agent | 9 | 6 | 1 |
| ai-goat | 10 | 7 | 7 |

**Known limitations, stated honestly:**

- `data_edges_only` returns an empty list on all four primary corpora. This is **the correct answer**, not a failure: the AST layer does not attempt to confirm data flow (ADR-0016). Data flows are the runtime layer's job, and the runtime layer is on the roadmap. The `flow_kind` and `observed_by` attributes exist and are honest: on a structural scan, every edge is `flow_kind = "control"` with `observed_by = ["ast"]`.
- `lethal_trifecta` is `False` on every node in every corpus. The mechanism is implemented and tested on synthetic nodes; no real target has exercised it.
- **Layer 3 validation checks existence, not semantics.** `is_chain_valid` verifies that nodes and edges exist in the graph. It does not verify direction, order, or roles. Semantic validation is on the roadmap.
- `chain_groups` was designed, implemented, and validated — and returns zero findings on FinBot. The five groups that were evaluated all came back `not_exploitable` with high confidence. This is the **correct** result: FinBot does not contain indirect prompt injection. It is also a signal that the group pipeline has not yet been exercised against a target where it should fire.
- **The project is tuned against FinBot-style layouts.** Name-based heuristics (`SINK_METHOD_NAMES`, `RUNNER_TO_AGENT`, `EXTRA_AGENT_CLASSES = {"CoPilotAssistant"}`) are literal names from one target. They are being removed (ADR-0016, roadmap step 3).

The next phase is a **restart of the AST layer**: remove name-based heuristics, replace kind classification with LLM classification that reads the body, and move flow analysis from AST to runtime. This is a deliberate rewrite, not an incremental fix. See `Documentation/BACKLOG.md`.

---

## How It Works

AICorellator does three things.

**Builds a graph.** It reads the source and finds AI components: agents, LLM clients, MCP servers, tools, dangerous calls, entry points. This is fast (seconds) and deterministic: the same code produces the same graph.

**Evaluates paths.** It finds every structural path from an entry point to a dangerous call and asks a local LLM which of them are actually exploitable. This is slow (minutes) and runs on request, as a separate command.

**Validates the result.** Every edge in a reported chain is checked against the graph. A chain that cites an edge that does not exist is discarded. Hallucinated chains are impossible.

The graph comes in two kinds:

- **Structural** — who can call whom. Always built, no LLM involved.
- **Enriched** — the same, plus confirmed data flows. Built only by runtime observation (Layer 2), currently Python-only and on the roadmap.

On a structural graph, `data_edges_only` returns an empty list. That is the correct answer: the AST cannot confirm data flow, and we do not pretend otherwise. Data flows are the runtime's job.

**What it does not do:** it does not scan the network, it does not run in production runtime, and it does not replace SAST/DAST/SCA. It finds **paths** through a graph of AI components and evaluates whether they are exploitable.

### What the graph contains

- **Nodes** — `agent`, `llm_endpoint`, `mcp_server`, `tool`, `sink`, `entry_point`, `guardrail`, `untrusted_source`.
- **Edges** — `uses_model`, `uses_tool`, `provides_tool`, `delegates_to`, `has_access_to`, `guards`, `writes_to`.
- **Flow attributes** — every node and every edge carries `flow_kind` (`control` | `data` | `structural`) and `observed_by` (a JSON array of who confirmed it: `["ast"]`, `["ast", "runtime"]`, ...). See `Documentation/Architecture/OVERVIEW.md` and ADR-0016.

### What the evaluation produces

A **candidate chain** is a structural path from an entry point to a sink. A **finding** is a candidate chain that the LLM ensemble has evaluated and that Layer 3 has validated. The report contains both, clearly separated:

- `## Candidate chains (Phase 1 enumeration)` — deterministic paths, no verdict.
- `## Evaluated chains (Phase 3)` — verdict, confidence, reasoning, validation.
- `## Chain groups (Phase 3)` — pairs of chains that meet through an `untrusted_source`, evaluated as a pair (indirect prompt injection).

A Phase 1 scan produces the first section only. This is by design: evaluation is expensive, and running it on every scan would burn the analyst's budget on chains that no LLM has looked at yet. Phase 3 is on demand (`run_eval.py`).

---

## What It Is Not

- **Not a network scanner.** AICorellator reads local sources. It does not scan ports, services, or network traffic.
- **Not a runtime monitor.** Runtime analysis is a **planned** layer (Layer 2), not a current capability. The project has no eBPF, no instrumentation, no tracing.
- **Not a replacement for SAST / DAST / SCA.** Those tools find vulnerabilities. AICorellator finds **paths** through a graph of AI components. Findings from those tools are inputs to the graph, not replacements for it.
- **Not a CVE database.** CVE data is external and optional.
- **Not a SAST in the CodeQL sense.** It has no type resolution, no interprocedural dataflow, no symbolic execution. The capability extraction is name-based, and the flow analysis is best-effort at the AST level. Precision is lower than compiler-grade tools; the goal is chain enumeration, not vulnerability discovery at the statement level.

---

## Quick Start

### Requirements

- **Python 3.11+**
- **uv** — provides ``uvx``, used to run ``tree-sitter-analyzer`` as a subprocess. Install:

  ```
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

  Or via pip: ``pip install uv``.

- **Optional:** a local LLM runtime (LMStudio, Ollama, vLLM) for Phase 3. Without one, Phase 1 works; Phase 3 does not.

### Install

```
git clone https://github.com/Malinowiy/AICorellator.git
cd AICorellator
pip install -e .
```

### Configure

The first run creates a workspace under `./aicorellator_data/` and writes two configuration templates:

- `config/scan.yaml` — scan policy: which detectors, LLM classifier mode, runtime mode.
- `config/models.yaml` — the ensemble used for chain evaluation.

Both are created with a warning. **They are templates, not recommendations** — edit them to match your environment.

### Scan a target

```
python run_scan.py --target /path/to/target
```

The target can be any directory containing source code: a repository, a folder, an unpacked Docker image, a mounted filesystem. The scan creates a workspace under ``./aicorellator_data/``:

```
aicorellator_data/
├── aicorellator.db # SQLite graph
├── reports/
│ └── current/
│ ├── report.md # human-readable
│ └── chains.json # machine-readable
├── config/
│ ├── scan.yaml # scan policy (auto-created)
│ └── models.yaml # ensemble config (auto-created)
├── cache/
│ └── tree-sitter/ # TSA cache
└── logs/
  └── aicorellator.log
```

### Evaluate chains (Phase 3, optional)

Requires a local LLM runtime.

```
python run_eval.py --mode fast --limit 10
```

- ``--mode fast`` — one voter, no judge. Quick screening.
- ``--mode solo`` — one voter with reasoning. Deep single-pass.
- ``--mode full`` — N voters + judge. Ensemble with final verdict.
- ``--limit`` — how many chains to evaluate.

The ensemble is configured in ``aicorellator_data/config/models.yaml``. Edit it to point at your local models.

### Regenerate the report

```
python run_report.py
```

### Useful flags

```
python run_scan.py --target ./app --workdir /tmp/scan-1    # custom workspace
python run_scan.py --target ./app --clean                  # wipe workspace first
python run_scan.py --target ./app --frameworks langchain,fastmcp  # enable specific detectors
```

### What to do if the graph is empty

- **No nodes at all** — check that ``uvx`` is installed (``uvx --version``). Without it, TSA does not run and no symbols are extracted.
- **Nodes but no edges** — the target may not match any known framework pattern. Check ``aicorellator_data/logs/aicorellator.log``.
- **No chains** — the graph may have no entry points (HTTP routes, CLI scripts, Streamlit apps, MCP servers) or no sinks. Check the report's node counts.

---

## Roadmap

The roadmap is a **restart of the AST layer**, not an incremental improvement. The reasoning:

1. **Name-based heuristics are the wrong foundation.** `SINK_METHOD_NAMES`, `RUNNER_TO_AGENT`, `EXTRA_AGENT_CLASSES = {"CoPilotAssistant"}` are literal names from one target. On another project they are noise. They exist because the AST layer has no body-aware classification; the fix is to add one, not to extend the vocabulary.
2. **Flow semantics cannot be inferred from AST.** `flow_kind` and `observed_by` were designed to distinguish "X calls Y" from "data from X reaches Y". The AST analyzer resolves `control` / `unknown` almost everywhere. The promise is not kept, and it cannot be kept at the AST level. Runtime observation is the honest place for this signal.
3. **Framework-agnostic classification is achievable.** An LLM that reads the body of a class or method can classify it without a name vocabulary. The cost is minutes per scan (cached), not seconds — but the result is framework-agnostic and carries reasoning.

### Phases

| Phase | What | Estimate | Stops at |
|---|---|---|---|
| 0 | Freeze v0.1; write ADR-0016 (`data_flow_confirmed` is enrichment metadata, not a promise; `observed_by` as a JSON array) | 0.5 day | v0.1 tagged |
| 1 | Add `observed_by` to schema and models; `FlowKindStage` sets `control` only; `data_edges_only` filters on `"runtime"` | 0.5 day | — |
| 2 | `scan.yaml` + `--config` flag; fix `--clean` to keep `config/` | 1 day | **v0.2** — reproducible scans |
| 3 | Remove class-A heuristics (name-based) | 0.5 day | — |
| 4 | `framework_detectors/loader.py` — load user detectors from paths | 0.5 day | — |
| 5 | **Layer 1b** — LLM classification (kind + capability, cached, opt-in) | 2–3 days | **v0.3** — framework-agnostic |
| 6 | **Layer 3 semantic validation** — direction, order, roles | 1 day | — |
| 7 | **Layer 2** — runtime taint tracking (Python) | 7–10 days | **v1.0** — `data_edges_only > 0` on real corpora |

Estimates assume vibecoding pace: the current prototype (166 files, ~15k lines, 905 tests) was written in 10 working days. The restart is expected to take **~14 working days** and to reduce the codebase by roughly half — fewer heuristics, fewer tests, better outcomes.

### What is explicitly not planned

- **Production runtime analysis.** Taint tracking belongs in staging or test. Production is not instrumentable without observability cost that is not worth paying for security analysis alone.
- **Non-Python runtime analysis** (eBPF, Java bytecode). Deferred until Python works.
- **TUI.** Not a security tool feature.
- **Multi-host topology.** The current single-host model is enough for the target audience.
- **Schema migrations.** The database is recreated per scan.
- **SCIP integration.** TSA provides symbols; that is what the AST layer needs.

See `Documentation/BACKLOG.md` for the full backlog, and `Documentation/ADR/` for the reasoning behind each architectural decision.

---

## Framework Detectors

Layer 1a classification. Per-file, framework-specific.

| Framework | Signature | Status |
|---|---|---|
| LangChain | `langchain.agents`, `AgentExecutor`, `create_agent` | Implemented |
| LangGraph | `langgraph.graph`, `StateGraph` | Implemented |
| FastMCP | `mcp.server.fastmcp`, `FastMCP` | Implemented |
| MCP Python SDK | `mcp.server`, `Server` | Implemented |
| OpenAI Agents SDK | `openai.agents`, `Agent`, `Runner` | Planned |

See `Documentation/ADR/ADR_011.md` for the full design.

---

## Evaluation Modes

Phase 3 evaluation runs in one of four modes:

| Mode | Voters | Judge | Purpose |
|---|---|---|---|
| `fast` | 1 | no | Quick screening |
| `solo` | 1 with reasoning | no | Deep single-pass |
| `full` | N | yes | Ensemble with final verdict |
| `custom` | as configured | as configured | Manual control |

The ensemble is configured explicitly — models, weights, timeouts, reasoning effort. No auto-discovery. All analysis runs locally via LMStudio, Ollama, or vLLM. No external API calls.

The prompt is versioned (`PROMPT_VERSION`). Every chain row stores the version that produced it.

---

## Design Principles

| Principle | Meaning |
|---|---|
| **Graph is alive** | Single mutable graph per scan. Nodes carry `confidence`; stale nodes lose confidence gradually. |
| **SQLite is the store** | No external services. Schema recreated per scan. |
| **Enumeration is deterministic** | Candidate chains are structural paths, enumerated by DFS. No LLM. Bounded by `max_hops` and `max_candidates`. |
| **Evaluation is LLM-only** | The verdict is produced by an LLM ensemble. No regex, no heuristics for *evaluation*. |
| **Validation is deterministic** | Every reported edge is validated against the graph. Hallucinated chains are impossible. |
| **False positives accepted** | Detection prefers a spurious edge over a missing one. Phase 3 filters. |
| **Reports on demand** | Evaluation is expensive. Reports are generated on request, not on every mutation. |
| **Reasoning over verdicts** | The output is not a list of "vulnerable" and "safe". It is a list of paths with reasoning, confidence, and provenance. |

---

## Documentation

- `Documentation/ADR/` — architectural decisions, one per file. Authoritative record of *why*.
- `Documentation/Architecture/OVERVIEW.md` — the shape of the system, for developers.
- `Documentation/Architecture/` — C4 diagrams (L1 system context, L2 containers, L3 components per phase).
- `Documentation/Analysis/` — false-positive / true-positive analysis on real corpora.
- `Documentation/BACKLOG.md` — what to do next. Authoritative record of *what*.

---

## License

AICorellator is released under the **PolyForm Noncommercial License 1.0.0**. Noncommercial use is free. Commercial use requires a paid license.

- **Noncommercial use** — free, under the terms of `LICENSE`.
- **Commercial use** — requires a commercial license. See `COMMERCIAL.md` for pricing, contribution-based discounts, and how to purchase.
- **Contributions** — welcome, under the terms of `CONTRIBUTING.md`.