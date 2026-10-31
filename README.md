# AICorellator

**AI Infrastructure Attack Surface Correlator**

A static analyzer that builds a graph of AI infrastructure components — agents,
LLM endpoints, MCP servers, tools, sinks, stores, guardrails — and evaluates
attack chains through that graph with a local LLM ensemble.

Not a CVE scanner. Not a runtime monitor. Not a replacement for SAST/DAST/SCA.
A correlator: it takes the surface a static scan can see, builds it into a
graph, and asks an LLM ensemble whether a path through that graph is
exploitable.

---

## Why this exists

AI systems fail in a way traditional scanners do not model. A vulnerable
function is not the interesting unit any more; the interesting unit is a
**path** — an attacker-controlled entry point that reaches a dangerous action
through an LLM, an agent, an MCP server, a tool, and a store. Each component is
benign alone. The chain is the vulnerability.

Three things make that chain hard to see:

1. **The components are new.** Agents, MCP servers, tool callbacks, guardrails
   and vector stores have no established static signatures. A SAST rule for
   `subprocess.run` says nothing about whether an LLM can be prompted into
   calling a tool that runs it.
2. **The flow is indirect.** An MCP tool description is attacker-controlled
   text. A RAG corpus is attacker-seedable content. A model reads both and
   decides what to do — and no AST sees that decision.
3. **The trust boundaries are informal.** "The agent calls the tool" is a
   statement about capability, not about data. "The tool reads the corpus" is
   a statement about a place, not about a value. A scanner that conflates the
   two reports either noise or nothing.

AICorellator was built to answer one question honestly: **given the code, which
paths through the AI infrastructure are worth an analyst's time?** It answers
that question in three layers — a deterministic graph, an LLM ensemble, a
deterministic validator — and it is explicit about what each layer can and
cannot see.

---

## What it does, in one paragraph

It reads the source, finds the AI components (agents, LLM clients, MCP servers,
tools, dangerous calls, entry points, stores, guardrails), and builds them into
a graph. It enumerates every structural path from an entry point to a
dangerous call. It asks a local LLM ensemble which of those paths are actually
exploitable. It validates every edge in a reported chain against the graph, so
a hallucinated chain is discarded rather than reported. The output is not a
list of "vulnerable" and "safe" — it is a list of paths with reasoning,
confidence, and provenance.

---

## Architecture — and why it is shaped this way

The system is four layers. Each has one job, one cost profile, and one
determinism guarantee. The shape is a series of deliberate refusals, not an
accumulation of features.

| Layer | Job | Determinism | Cost | Status |
|---|---|---|---|---|
| **1a — Structural (AST)** | framework detectors, entry points, call graph, capabilities → a **control-only** graph | deterministic | seconds | works |
| **1b — LLM classification** | classify symbols the deterministic layers could not | non-deterministic, cached | minutes | opt-in, off by default |
| **2 — Runtime observation** | taint tracking, frame→AST mapping → **data** edges | observed, not inferred | minutes–hours | roadmap (Python only) |
| **3 — Chain evaluation** | LLM voter ensemble + judge; deterministic validation | LLM verdict, deterministic validation | minutes | works |

### Why AST is control-only

An AST can say that one function calls another. It cannot say that a value
flows from one to the other — not without type resolution, interprocedural
analysis, and a model of the runtime, which is a compiler's job, not a
scanner's. So Layer 1a refuses to claim data flow. Every edge it produces is
`flow_kind = "control"`, and `data_flow_confirmed` is `NULL`. That is not a
missing feature; it is the honest answer to the question the AST can answer.

The consequence is stated up front: `data_edges_only` returns an empty list on
an AST-only scan. **That is the correct answer, not a failure.** Data flow is
Layer 2's job, and Layer 2 is runtime observation, not a smarter AST.

### Why runtime, and what it will match

Layer 2 will observe the target under test (`pytest`, a staging run) and stitch
what it saw back to the AST nodes by `(file_path, line_start, name)`. An edge
the runtime observed gains `"runtime"` in `observed_by`; an edge only the
runtime saw is created with `observed_by = ["runtime"]`. The AST and the
runtime are not two guesses at the same thing — they are two different
observations, and `observed_by` is what keeps them apart. This is why the
schema carries `observed_by` from day one, before Layer 2 exists: the
attribute is the contract, and the contract is cheaper to keep than to add.

### Why the LLM ensemble, and why the validator is not an LLM

The verdict — is this path exploitable? — is the one question the AST cannot
answer and a runtime run may not reach (the target may never execute the
path). It is a judgement call, and a judgement call is what an LLM is for. So
the verdict is produced by an ensemble: independent voters, an optional judge,
weighted aggregation. The prompt is versioned; every chain row stores the
version that produced it.

But a verdict is only useful if the path is real. So validation is
deterministic, and it runs on the graph, not on the model's output: every node
and every edge in a reported chain must exist, the chain must be connected and
typed, an anchor must be a real source, and — since ADR-0018 revision 2 — at
least one voter must have cited an edge from **this** chain. A hallucinated
chain is structurally impossible to report. The LLM proposes; the graph
disposes.

### Why name-based heuristics were removed

The first prototype classified components by class name (`*Agent`,
`*Orchestrator`, `*Server`), by method name (`execute_*`, `get_*`), and by
file path (`agents/`, `mcp/servers/`). It worked on the corpus it was tuned
against and mislabelled every other project. A class called `FooAgent` that
never calls an LLM is not an agent; a helper called `execute` that runs
`subprocess.run` is a sink.

The heuristics were removed, not extended. Classification now comes from
capabilities (ADR-0009: what the body **calls**, not what it is called) and
from framework provenance (ADR-0011: a detector recognized the SDK). A project
no built-in detector covers is covered by a user detector — a file plus a
registry line, the same interface as the built-ins — or by the optional LLM
classifier. The rule is simple: **a name may withhold a node; a name may never
grant one.**

### Why a store is a graph entity, and a source type is not a family

An upload endpoint writes a file; a tool reads it. Neither side names the
other. The entity they share is the **store** — a place, identified by a
technology (`fs`, `s3`, `db`) and a literal (a path, a bucket, a table). The
store is a graph node because the two sides need something to meet at, and
`writes_to` / `reads_from` are structural edges because the AST sees the call
sites and reasons that they name one place — it does not see a value pass.
The link carries `identity_confidence` (how sure the analysis is that the two
ends name the same store) — a separate field from `confidence`, which says the
edge exists.

The **semantic role** of a read — `user_upload`, `rag_corpus`,
`mcp_tool_description` — is not a family. It is the `source_type` on the
`untrusted_source` node. A store is the technology; a source type is what the
read means. The split is what lets a guardrail, a chain group and a report
talk about the same place without conflating "it is a filesystem" with "it is
an upload".

### Why the prompt is a file, and the policy is a file

Which layers run, which detectors are enabled, which models vote, what the
judge temperature is — that is policy, and policy belongs in a file, not in a
flag. The first prototype scattered it across CLI arguments, and a scan was
not reproducible because "we ran with these flags" is not a record.
`config/config.yaml` is versionable, diffable, and reviewable; the CLI is
minimal (`--target`, `--config`, `--clean`). The same file carries the
evaluation ensemble and the classifier model — one file, sections per
subsystem.

### Why the graph is recreated per scan, and why there are no migrations

The graph is derived state. It is a function of the code and the config, and
when either changes the old graph is not "migrated" — it is replaced. The
database is rebuilt per scan (ADR-0003), the schema is applied as a single
file, and `--clean` wipes the derived state. The one thing `--clean` does not
touch is `config/`, because config is user input, not state. This is a
deliberate trade: no migration machinery, no schema drift, no "which version
of the graph are you looking at" — at the cost of not being able to compare
two scans in one database. That comparison is what `Documentation/Analysis/`
is for.

---

## Phase C — runtime observation (roadmap, not in this release)

The AST layer answers "who can call whom". It cannot answer "does data from X
reach Y". That question is Layer 2's, and Layer 2 is **not in this release**.

### What Phase C is

Runtime observation: the target runs under test (`pytest`, a staging
execution), a Python tracing layer tags values that enter from an untrusted
source, and the stitcher maps each observed `(co_filename, co_firstlineno,
co_name)` back to the AST node it belongs to. An edge the runtime observed
gains `"runtime"` in `observed_by`; an edge only the runtime saw is created
with `observed_by = ["runtime"]`.

The result is the one signal the AST cannot produce: a path whose data flow
was **observed**, not inferred. `find_chains(data_edges_only=True)` filters on
`"runtime" in observed_by` — not on a heuristic — so on a Layer 1a scan it
returns `[]` (correct, ADR-0016), and after Phase C it returns the chains the
runtime actually exercised.

### What Phase C gives you

- **Observed data edges.** `flow_kind = "data"` with `observed_by` containing
  `"runtime"` — the strongest evidence the graph can carry. The LLM ensemble
  weighs them above control edges, and the prompt can finally be grounded in a
  signal that exists (today it teaches the model to weigh a signal that is
  never present on an AST corpus).
- **A second observer, not a second guess.** The AST and the runtime are two
  independent observations of the same system. `observed_by` is what keeps
  them apart, and the stitcher is what makes them meet. An edge only one of
  them saw is still a fact about the system, and the graph says which.
- **A shorter path to the dangerous action.** A control chain says "reachable";
  a data chain says "reachable **and** the attacker's value arrived". The
  second is the one an analyst triages.

### What Phase C does not give you

- **Not all paths.** A runtime run observes the paths the test exercised. A
  path the test never reached is invisible to Layer 2 — and that is the AST
  layer's territory, not a gap to paper over. The two layers are
  complementary, not redundant.
- **Not a production monitor.** Phase C runs the target under test, in
  staging, under instrumentation. Production runtime is explicitly out of
  scope: taint tracking belongs where the target can be re-run, and the
  observability cost is not worth paying for security analysis alone.
- **Not multi-language.** Runtime analysis is language-specific
  (`sys.settrace` for Python, eBPF for Go/Rust, bytecode agents for Java).
  Python is first because it is the target. Adding a language means adding a
  runtime module, not a runtime flag.

### Why it is not here yet

The AST layer is not finished. Phase B (the store model, the guardrail
producers, the provenance columns) closed the coverage gaps that made the
Layer 1a graph honest; Phase C is the next phase, and it is a **large** one —
the taint tracker, the frame mapper, the stitcher, the runner, and the
config surface are each a piece of work, and the target has to be runnable
under test for any of it to be exercised.

The order is deliberate. A runtime layer on top of a graph that claims data
flow it cannot see would be a second guess stacked on a first. The graph
first tells the truth about control; then the runtime adds the truth about
data. The `observed_by` contract is what makes the second step possible
without re-reading the first — and it is in the schema today, before Layer 2
exists, because the contract is cheaper to keep than to add.

**Status:** roadmap (Python only). The interface — `observed_by`, the
`runtime:` config section, the `runtime.mode` and `runtime.test_command`
keys — exists and is documented; the implementation does not.

---

## What the output looks like

A **candidate chain** is a structural path from an entry point to a sink. A
**finding** is a candidate chain the ensemble has evaluated and Layer 3 has
validated. The report keeps them separate, because they cost different things:

- `## Candidate chains (Phase 1 enumeration)` — deterministic paths, no
  verdict.
- `## Evaluated chains (Phase 3)` — verdict, confidence, reasoning,
  validation.
- `## Chain groups (Phase 3)` — pairs of chains that meet through an
  `untrusted_source`, evaluated as a pair (indirect prompt injection).

A Phase 1 scan produces the first section only. That is by design: evaluation
is expensive, and running it on every scan burns the analyst's budget on chains
no model has looked at yet. Phase 3 is on demand (`run_eval.py`), and the
report is regenerated on demand (`run_report.py`).

The graph itself carries two independent facts on every node and edge:
`flow_kind` (`control` / `data` / `structural`) and `observed_by` (who
confirmed it — `["ast"]`, `["ast", "runtime"]`, ...). They answer different
questions and are never collapsed into one.

---

## What it is not

- **Not a network scanner.** It reads local sources; it does not scan ports or
  services.
- **Not a runtime monitor.** Runtime analysis is a planned layer (Layer 2),
  not a current capability. There is no eBPF, no instrumentation, no tracing
  today.
- **Not a replacement for SAST / DAST / SCA.** Those find vulnerabilities;
  AICorellator finds paths through a graph of AI components. Their findings are
  inputs, not replacements.
- **Not a CVE database.** CVE data is external and optional.
- **Not a SAST in the CodeQL sense.** No type resolution, no interprocedural
  dataflow, no symbolic execution. Precision is lower than a compiler-grade
  tool; the goal is chain enumeration, not statement-level discovery.

**Known limitations, stated honestly:**

- `data_edges_only` is `[]` on all AST corpora — correct, by design
  (ADR-0016). Data flow is Layer 2's job.
- `lethal_trifecta` is `False` on every real node; the mechanism is tested on
  synthetic nodes, and no corpus has exercised it yet.
- `chain_groups` returns zero findings on FinBot. That is the correct result
  (FinBot has no indirect prompt injection) — and a reminder that the group
  pipeline has not yet fired on a target where it should.
- The AST layer is tuned against a small set of public corpora; a corpus
  regression is a dated snapshot (`Documentation/Analysis/`), not a live
  claim.

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

The first run creates a workspace under `./aicorellator_data/` and writes one configuration template: `config/config.yaml`. It holds the scan policy, the evaluation ensemble, and the LLM classifier model — one file, sections per subsystem. Edit it here, or point at another file with --config.

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
│ ├── config.yaml # config (auto-created)
├── cache/
│ └── tree-sitter/ # TSA cache
└── logs/
  └── aicorellator.log
```

### Evaluate chains (Phase 3, optional)

Requires a local LLM runtime.

```
python run_eval.py --limit 10
```
The ensemble is configured in ``aicorellator_data/config/config.yaml``. Edit it to point at your local models.

### Regenerate the report

```
python run_report.py
```

### Useful flags

```
python run_scan.py --target ./app --workdir /tmp/scan-1    # custom workspace
python run_scan.py --target ./app --clean                  # wipe workspace first
```

### If the graph is empty

- **No nodes at all** — check that `uvx` is installed (`uvx --version`).
  Without it, TSA does not run and no symbols are extracted.
- **Nodes but no edges** — the target may not match any known framework
  pattern. Check `aicorellator_data/logs/aicorellator.log`.
- **No chains** — the graph may have no entry points (HTTP routes, CLI
  scripts, Streamlit apps, MCP servers) or no sinks. Check the report's node
  counts.

---

## Framework detectors

Layer 1a classification. Per-file, framework-specific.

| Framework | Signature | Status |
|---|---|---|
| LangChain | `langchain.agents`, `AgentExecutor`, `create_agent` | Implemented |
| LangGraph | `langgraph.graph`, `StateGraph` | Implemented |
| FastMCP | `mcp.server.fastmcp`, `FastMCP` | Implemented |
| MCP Python SDK | `mcp.server`, `Server` | Implemented |
| Guardrails AI | `guardrails.Guard(...)` constructions | Implemented |
| NeMo Guardrails | `nemoguardrails.LLMRails(...)` | Implemented |
| LangChain callbacks | `BaseCallbackHandler` subclasses | Implemented |
| FastMCP middleware | `fastmcp.server.middleware.Middleware` subclasses | Implemented |
| OpenAI Agents SDK | `openai.agents`, `Agent`, `Runner` | Planned |

See `Documentation/ADR/ADR_0011.md` for the design, and ADR-0024 for the
guardrail producers.

---

## Evaluation modes

Phase 3 evaluation runs in one of four modes:

| Mode | Voters | Judge | Purpose |
|---|---|---|---|
| `fast` | 1 | no | Quick screening |
| `solo` | 1 with reasoning | no | Deep single-pass |
| `full` | N | yes | Ensemble with final verdict |
| `custom` | as configured | as configured | Manual control |

The ensemble is configured explicitly — models, weights, timeouts, reasoning
effort. No auto-discovery. All analysis runs locally via LMStudio, Ollama, or
vLLM. No external API calls.

The prompt is versioned (`PROMPT_VERSION`). Every chain row stores the version
that produced it.

---

## Design principles

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
| **A name withholds, never grants** | No class-name, method-name or path heuristic grants a node its kind (ADR-0016). |

---

## Documentation

- `Documentation/ADR/` — architectural decisions, one per file. The
  authoritative record of *why*.
- `Documentation/Architecture/OVERVIEW.md` — the shape of the system, for
  developers.
- `Documentation/Architecture/` — C4 diagrams (L1 system context, L2
  containers, L3 components per phase).
- `Documentation/Analysis/` — false-positive / true-positive analysis, and
  dated corpus snapshots (`corpus_regression_<date>.md`).
- `Documentation/BACKLOG.md` — what to do next. The authoritative record of
  *what*.

---

## License

AICorellator is released under the **PolyForm Noncommercial License 1.0.0**.
Noncommercial use is free. Commercial use requires a paid license.

- **Noncommercial use** — free, under the terms of `LICENSE`.
- **Commercial use** — requires a commercial license. See `COMMERCIAL.md`.
- **Contributions** — welcome, under the terms of `CONTRIBUTING.md`.