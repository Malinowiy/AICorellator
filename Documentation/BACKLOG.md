# AICorellator — Backlog

Single registry of deferred, in-progress, and planned work. Complements the ADRs (which record *decisions*) with a working list of *what to do next*.

**Last updated:** 2026-10-25
**Revision:** restructured around ADR-0016 and ADR-0017. See §0 for the reasoning and the Top-10 view.

---

## 0. Direction and priorities

### 0.1. The diagnosis this revision is built on

The previous revision (2026-10-23) was built on the premise that `data_edges_only = 0` on all corpora was a **bug to fix**. ADR-0016 changed that premise: the AST layer cannot confirm data flow, and pretending it can produces `unknown` everywhere — which is worse than `NULL` because it looks like a signal. The correct answer is that the AST layer answers a different question.

Three facts from the 2026-10-25 state:

1. **ADR-0016 and ADR-0017 are accepted.** The AST layer is control-only. `observed_by` is a first-class attribute on every node and edge. Layer 2 (runtime) is the only producer of `data` edges. The `flow/` module remains the mechanism Layer 2 will use, but it does not write to the AST graph.
2. **`lethal_trifecta` works on the AST layer.** It is a node property, computed from capabilities. It does not depend on Layer 2. The corpus answer (zero on four corpora) is about the *test* corpus, not about the *mechanism*. The mechanism is correct and will fire when a real target has a node that combines the three halves.
3. **Layer 3 validates existence, not semantics.** `is_chain_valid` checks that nodes and edges exist; it does not check direction, order, or roles. A voter that returns `exploitable: true` with `evidence: []` still produces `validated: true`. This is unchanged by ADR-0016 and is the most significant remaining correctness gap.

Plus two coverage gaps that ADR-0016 does not close:

- **Write-side detection works only for `mcp_tool_description`.** `rag_corpus`, `email_inbox`, and `user_upload` have no writer rule. Half of ADR-0010 does not function.
- **Cross-file `tool → sink` is not implemented.** `has_access_to` is same-file only, so chain enumeration misses real paths.

### 0.2. The direction this revision sets

**Fix the foundation, then build.** Concretely:

- **Phase A (now):** ADR-0016 and ADR-0017 are accepted; the implementation follows. Schema (`observed_by`), scan policy (`scan.yaml`), removal of class-A heuristics, Layer 3 semantic validation. These are the changes that make the graph honest.
- **Phase B (next):** close the coverage gaps that make existing features work — cross-file `tool → sink`, write-side for the remaining source families, guardrails as load-bearing, judge determinism.
- **Phase C (then):** Layer 2 runtime for Python. This is the only path to `data_edges_only > 0` on a real corpus, and it is the largest single piece of work in the project.
- **Phase D (then):** resume the roadmap — `openai_agents` detector, prompt engineering on a graph where the data exists.

**What is explicitly *not* planned in this cycle:** TUI (Icebox), new eval modes, multi-host, standalone executables, SCIP, schema migrations, non-Python runtime.

**What is explicitly *removed* from the backlog by this revision:** items `1.2a.1`, `1.2a.2`, `1.2a.3*`, `1.2a.4`, `1.2a.5`, the old "ADR-0016 — `data_flow_confirmed` quality", and `1.5` graph densification. They were all attempts to compute `data_flow_confirmed` on the AST layer. ADR-0016 supersedes them: the AST layer does not compute `data_flow_confirmed`, and no amount of analyzer refinement changes that.

### 0.3. Top 10 by impact

| # | Item | Priority | Why it is here |
|---|---|---|---|
| 1 | **Schema + `observed_by`** (ADR-0016 implementation) | **Critical** | The foundation. Everything else writes into this field. |
| 2 | **`scan.yaml` + `--clean` fix** (ADR-0016 implementation) | **Critical** | Scan policy must be a file, not flags. `--clean` currently deletes config — a bug. |
| 3 | **Remove class-A heuristics** (ADR-0016 implementation) | **Critical** | `SINK_METHOD_NAMES`, `RUNNER_TO_AGENT`, `EXTRA_AGENT_CLASSES` are FinBot-specific. Their removal is the point of ADR-0016. |
| 4 | **Layer 3 semantic validation** | **High** | `validated: true` currently does not mean "correct". |
| 5 | **Cross-file `tool → sink`** | **High** | Without it, chain enumeration misses real paths. |
| 6 | **Guardrails affect enumeration** | **High** | Guardrails are nodes but not blockers; the prompt is lying to the model. |
| 7 | **Write-side for `rag_corpus` / `email_inbox` / `user_upload`** | **Medium** | Half of ADR-0010 does not function. |
| 8 | **LLM classifier (`parsing/llm_classifier/`)** | **Medium** | The replacement for class-A heuristics on projects with custom SDKs. Opt-in. |
| 9 | **Layer 2 runtime — Python** | **High** (largest, but starts after Phase A/B) | The only path to `data_edges_only > 0`. |
| 10 | **Judge determinism** | **Medium** | Verdicts stored in `chain_groups` are not reproducible. |

---

## Priority legend

- **Critical** — blocks the ADR-0016 implementation. Nothing else starts until these land.
- **High** — next session or two.
- **Medium** — within a month.
- **Low** — when there is time.
- **Icebox** — deferred indefinitely; not cancelled.
- **Cancelled** — decided not to do.

## Status legend

- `Not started` — no work yet.
- `In progress` — actively being worked on.
- `Blocked by X` — waiting on another item.
- `Deferred until Y` — waiting on an external condition.
- `Partially done` — some work complete, some remaining.
- `Done` — completed. Kept briefly for reference; remove after a quarter.

---

## Recently done

### 2026-10-25 — ADR-0016 and ADR-0017

- **ADR-0016** — control-only AST layer; `observed_by` on every node and edge; classification layers; scan policy in `scan.yaml`; removal of class-A name heuristics.
- **ADR-0017** — Layer 1 / Layer 2 stitching; key `(file_path, line_start, name)`; no new edge kind (`has_access_to` + `observed_by`); idempotent; multiple runs distinguished by `runtime:<run_id>`.
- **README.md** — rewritten §"How It Works" for users; §"Status" reframed (`data_edges_only = []` is the correct answer, not a failure); §"Roadmap" updated.
- **Documentation/Architecture/OVERVIEW.md** — new; four layers, two attributes, `scan.yaml`, module layout, Mermaid diagrams.

### 2026-10-22 — Chain groups, end to end (1.3b, 1.3c, 1.3c.1, 1.3d, 1.3e)

The chain-group half of ADR-0010, plus ADR-0012 prioritization and ADR-0009 lethal-trifecta enrichment.

- **1.3a.1** — writer-side edges (`writes_to`, control/unknown).
- **1.3a.1.2** — dynamic MCP invocation + literal factory paths.
- **1.3a.1.3** — project-level literal factory anchor.
- **1.3b** — `find_chain_groups`: write chains × read chains around every `untrusted_source`.
- **1.3c** — `prioritize_chain_groups`: ADR-0012 deterministic scoring, ranking, top-N, storage.
- **1.3c.1** — ADR-0012 revision: `writes_db` +3, `min_score_to_evaluate` 5 → 3.
- **1.3d** — `lethal_trifecta` node attribute (stage 18). Zero flagged on the four corpora — mechanism ready, corpus unblocker is 1.3d.1.
- **1.3e** — `evaluate_chain_groups`: LLM ensemble, prompt v1.5.0, Layer 3 validation, report section, `chain_groups.json` in bundle.
- **Tests:** 905 passed.

### 2026-10-21 — Untrusted sources, part 1 (1.3a)

- **Detector** (`parsing/untrusted_sources.py`): three families, one capability.
- **Capability `reads_untrusted`**: credited on the reader, not on the source.
- **Node kind `untrusted_source`**: one per read site per file.
- **Stage `untrusted_sources`**: emits nodes + `source → reader` edges.

### 2026-10-20 — ADR-0015 implementation

- `flow_kind` on every edge (control | data | structural). `data_flow_confirmed` on `has_access_to`.
- Structural edges excluded from `find_chains`. `provides_tool` is control.
- **Known limitation (superseded by ADR-0016):** `data_flow_confirmed = "unknown"` on all edges across all four corpora. `data_edges_only = 0` everywhere. ADR-0016 changed this from a bug to a correct answer.

### Earlier

- CLI and Streamlit entry points (commit fc266b0).
- Project backlog created (commit 8c78d15).
- README user manual (commit 49f0cd9).
- PolyForm Noncommercial 1.0.0 license (commit dd6da36).
- TSA investigation (item 1.1) — TSA provides symbols only.
- P0/P2 review fixes (commit 2263fa7).

---

## Phase A — Implement ADR-0016 and ADR-0017

**The core promise of this phase:** after Phase A, the graph is honest. `data_edges_only = []` on an AST scan is documented as the correct answer. `observed_by` is on every node and edge. Scan policy is a file. Class-A heuristics are gone. Layer 3 validates semantics, not just existence.

### A.1 — Schema + `observed_by` + `FlowKindStage`

**Priority:** **Critical**
**Status:** Not started
**ADR:** ADR-0016
**Rationale:** The foundation. Every subsequent item writes into `observed_by`. `FlowKindStage` must stop writing `data_flow_confirmed` on AST edges and must set `flow_kind = "control"` unconditionally. `find_chains(data_edges_only=True)` must filter on `"runtime" in observed_by`, not on `flow_kind = "data"`.

**What changes:**
- `schema.sql`: `observed_by JSON NOT NULL DEFAULT '["ast"]'` on `nodes` and `edges`, with `CHECK (json_valid(observed_by))`.
- `graph/models.py`: `Node.observed_by: list[str]`, `Edge.observed_by: list[str]`.
- `graph/repositories/edges.py`, `nodes.py`: `upsert` merges `observed_by` as a set union (matches existing `capabilities` semantics).
- `parsing/normalizer_stages/flow_kind.py`: sets `flow_kind = "control"` on every AST edge; does not read or write `data_flow_confirmed`.
- `graph/query.py`: `find_chains(data_edges_only=True)` filters on `"runtime" in edge.observed_by`.
- Tests on `data_flow_confirmed` are rewritten: the AST-layer value is `NULL`, not `unknown`.

**Size:** ~1 day.

**Risks:** many existing tests assert `data_flow_confirmed = "unknown"`. They must be updated. This is the largest test-rewrite in Phase A.

**References:** ADR-0016 §Schema, §Decision 1–4.

### A.2 — `scan.yaml` + `--clean` fix

**Priority:** **Critical**
**Status:** Not started
**ADR:** ADR-0016
**Rationale:** Scan policy must be a file, not flags. `--clean` currently deletes `config/` — a bug. Both are needed before any further configuration is added.

**What changes:**
- `workspace.py`: `Workspace.scan_config_path`; `ensure()` writes `scan.yaml` from a template with a warning; `clean()` deletes state (db, reports, cache, logs) and keeps `config/`; `ensure()` adds a warning for `models.yaml` too.
- `scan_config.py` (new): `ScanConfig` dataclass, `load_scan_config`, `default_scan_config`, `write_default_scan_config`.
- `run_scan.py`: flags become `--target`, `--config`, `--clean`. Everything else is read from `scan.yaml`.
- `run_eval.py`: flags become `--config`, `--limit`. Mode, voters, judge come from `scan.yaml`.
- `run_report.py`: `--workdir` (unchanged; this is a path, not policy).

**Size:** ~1 day.

**Risks:** `open_workspace(clean=...)` loses the parameter; callers are updated. `run_scan.py` and `run_eval.py` change behavior; their tests are updated.

**References:** ADR-0016 §Scan policy.

### A.3 — Remove class-A heuristics

**Priority:** **Critical**
**Status:** Not started
**ADR:** ADR-0016
**Rationale:** `SINK_METHOD_NAMES`, `SINK_CATEGORY_HINTS`, `RUNNER_TO_AGENT`, `EXTRA_AGENT_CLASSES`, `PROVIDER_HINTS`, `TOOL_METHOD_PREFIXES`, `SKIP_METHOD_NAMES` are literal names from FinBot. Their removal is the point of ADR-0016.

**What changes:**
- `patterns.py`: remove the seven dictionaries.
- `classification.py`: `classify_class` and `classify_method` read only capabilities and framework attributes.
- Tests on these dictionaries are removed; `classification.py` tests are rewritten to test "capability → kind" and "framework → kind".

**Size:** ~0.5 day.

**Risks:** FinBot-style projects produce fewer nodes. This is intentional — the old behavior was FinBot-specific and wrong on other projects.

**References:** ADR-0016 §Removal of class-A name heuristics.

### A.4 — Layer 3 semantic validation

**Priority:** **High**
**Status:** Not started
**Rationale:** `is_chain_valid` checks that nodes and edges exist; it does not check direction, order, or roles. A voter that returns `exploitable: true, evidence: []` produces `validated: true`. This is the most significant remaining correctness gap after ADR-0016.

**What changes:**
- `QueryLayer.is_chain_connected(nodes, edges)` — edge `i` links `nodes[i]` to `nodes[i+1]`.
- `QueryLayer.is_chain_typed(nodes, edges, entry_kind, sink_kind)` — first node is `entry_point`, last is `sink` (or has a sink capability, per ADR-0014).
- `QueryLayer.is_untrusted_anchor_valid(source_id)` — source node has `source_type` and its reader has `reads_untrusted`.
- Roll these into `is_chain_valid`; report which check failed.
- `Chain.validated_kind` ∈ `{existence, semantic}` — a new field, populated by the validator.

**Size:** ~1 day.

**Risks:** `validated: true` changes meaning. Chains stored with `validated: true` before this change become retroactively weaker under the new rules. The DB is recreated per scan (ADR-0003), so this is not a migration — but it is a reputation change. Document it in the ADR for Layer 3 semantic validation (new ADR, not yet written).

**References:** ADR-0006 Layer 3; `QueryLayer.is_chain_valid`; ADR-0016 §Decision.

---

## Phase B — Close the coverage gaps

### B.1 — Cross-file `tool → sink`

**Priority:** **High**
**Status:** Not started
**Rationale:** `has_access_to` (tool → sink) is built only within a single file. Real projects have `tool` in `mcp/servers/findrive/server.py` and `sink` in `mcp/servers/findrive/repositories.py` — no edge exists. Chain enumeration misses these paths.

**Approach:** Extend the call graph to span imports. `has_access_to` edges across files inherit the same `flow_kind` / `observed_by` discipline (`["ast"]` only; `control`). The `flow/` analyzer is not involved — ADR-0016 removed its AST write.

**Size:** ~1 day.

**References:** ADR-0013 Deferred.

### B.2 — Write-side for `rag_corpus` / `email_inbox` / `user_upload`

**Priority:** **Medium**
**Status:** Not started
**Rationale:** 1.3a.1 explicitly says RAG corpora, mailboxes, and user uploads have no writer evidence. Chain groups require a write chain and a read chain. For three of the four source families, the write chain does not exist. ADR-0010 describes four families; only one (`mcp_tool_description`) functions end-to-end.

**What "write-side" means per family:**
- **`rag_corpus`** — a call that adds documents to a vector store (`add_documents`, `upsert`, `add_texts`, `index`). Detector rule in `patterns.py`, new source-type gate in `untrusted_source_writers`.
- **`email_inbox`** — a call that sends an email (`sendmail`, `smtp.send`, `send_message`). Detector rule.
- **`user_upload`** — a structural signal, not a call-site rule. Deferred to B.3.

**Approach:** three rules, one per family, each with corpus evidence. Do not add a rule without a corpus.

**Size:** ~1 day per family, with corpus evidence.

**References:** ADR-0010; ADR-0015 decision 5.

### B.3 — `user_upload` source type

**Priority:** **Medium**
**Status:** Not started
**Blocked by:** entry-point detector extension
**Rationale:** `user_upload` is not a call shape; it is a structural signal — a route handler that writes a file and later reads it. Detecting it requires entry-point analysis (which handler? which directory? which tool reads from it?). This is a cross-component detector, not a `CallPattern`.

**Approach:** entry-point detector gains a `detect_uploads` pass that emits `untrusted_source` nodes with `source_type = "user_upload"` and a `has_access_to` edge to the handler. `untrusted_source_writers` gets a rule for this family (the upload handler is the writer).

**Size:** ~1 day.

**References:** ADR-0010 source families; `parsing/entry_points.py`.

### B.4 — Guardrails affect enumeration

**Priority:** **High**
**Status:** Not started
**Blocked by:** `guards` edge emission
**Rationale:** ADR-0007 is honest: chains do not yet account for guardrails. Guardrail nodes exist but are inert. The prompt tells the model about guardrails, and the model sees nodes without knowing whether they matter.

**Three sub-problems:**
1. **`guards` edge emission** — the edge kind is registered, `flow_kind = "structural"`, and nothing emits it.
2. **`bypassed_by` derived edge** — the reverse: which agent or tool bypasses which guardrail.
3. **Guardrail-aware `find_chains`** — either chains through a guardrail are marked `blocked_by_guardrail` (soft) or excluded from default enumeration (hard). This is a new decision, not covered by ADR-0007. It should be an ADR.

**Size:** ~3 days total, across three sub-items.

**References:** ADR-0007; ADR-0015 (structural edges excluded from `find_chains`).

### B.5 — Judge determinism

**Priority:** **Medium**
**Status:** Not started
**Rationale:** The judge runs with `temperature=0.6`. Two runs on the same graph with the same prompt can produce different `verdict` values, persisted in `chains.verdict` and `chain_groups.verdict`. The report is non-deterministic even when the graph is not.

**Approach:** `judge.temperature = 0.0` as the default, with an explicit override in the config for users who want stochasticity. Document the trade-off.

**Size:** ~0.5 day.

**References:** ADR-0005; the P0/P2 review.

### B.6 — `chain_groups.lethal_trifecta` column

**Priority:** **Low**
**Status:** Not started
**Rationale:** `chain_groups.lethal_trifecta` exists in the schema and has no writer. It is `False` on every row. The per-node flag exists (1.3d); the per-group aggregate does not. The report does not show it, but `chain_groups.json` includes it as `false` on every row, which reads as a claim.

**Approach:** one line in `_evaluate_one_group`: `record.lethal_trifecta = any(node.attributes.get("lethal_trifecta") for node in read_chain_nodes)`. No schema change.

**Size:** ~0.5 hour.

**References:** 1.3d; 1.3e report.

### B.7 — `chain_groups.prompt_version` / `voter_outputs` columns

**Priority:** **Medium**
**Status:** Not started
**Rationale:** `chains` has `prompt_version` and `voter_outputs`. `chain_groups` does not. A group verdict cannot be traced to prompt v1.5.0 in storage.

**Approach:** two columns (`prompt_version TEXT`, `voter_outputs JSON`), populated by `evaluator._evaluate_one_group`. Schema change; DB recreated per scan, no migration.

**Size:** ~0.5 day.

**References:** ADR-0005; chain-group evaluator.

### B.8 — `untrusted_source` identity review

**Priority:** **Medium**
**Status:** Not started
**Rationale:** Node identity is `(source_type, call_target, source_file)`. Two `retriever.invoke` calls in one file → one node, even if different retrievers. Two calls in two files → two nodes, even if the same store. The graph does not record this uncertainty.

**What this should produce:** a short ADR or documentation note that names the limitation and specifies how downstream features behave. Options: accept (document), enrich (`corpus_hint` attribute), merge (not recommended).

**Size:** ~0.5 day (documentation).

**References:** `untrusted_source_id`; 1.3a design note.

### B.9 — Evidence presence affects validation

**Priority:** **Medium**
**Status:** Not started
**Rationale:** `is_chain_valid` does not look at voter evidence. A voter that returns `exploitable: true, evidence: []` produces `validated: true`. The `evidence_valid` field is computed per voter but does not propagate to `Chain.validated`.

**Approach:** `Chain.validated` becomes a two-part condition: every node and edge exists, and at least one voter cited at least one edge from the chain. If the second fails, the chain is stored with `validated: false` and the reason is visible in the report.

**Size:** ~0.5 day.

**References:** `evaluator._voter_response_dict`; `QueryLayer.is_chain_valid`.

---

## Phase C — Layer 2 runtime (Python)

**The core promise of this phase:** after Phase C, `data_edges_only > 0` on at least one real corpus, because runtime observation produces `data` edges.

### C.1 — Runtime taint tracking (Python)

**Priority:** **High**
**Status:** Not started
**ADR:** ADR-0017
**Rationale:** The only path to `data_edges_only > 0`. Runtime analysis is Python-only for now; adding a language means adding a runtime module, not a runtime flag.

**What changes:**
- `runtime/python/taint_tracker.py` — `sys.settrace` + value tagging.
- `runtime/python/frame_mapper.py` — `(co_filename, co_firstlineno, co_name)` → `(file_path, line_start, name)` via `Target.root`.
- `runtime/python/observer.py` — collects observations, writes `observed_by` additions.
- `runtime/python/stitcher.py` — matches by `(file_path, line_start, name)`; adds `"runtime"` and `"runtime:<run_id>"`; creates `has_access_to` with `observed_by = ["runtime"]` when no AST match.
- `runtime/python/runner.py` — runs `runtime.test_command` from `scan.yaml`, collects observations, invokes the stitcher.
- `scan.yaml`: `runtime.mode: off | python`, `runtime.test_command`.
- `run_scan.py`: after writing the AST graph, if `runtime.mode == "python"`, run the runtime module and stitch.

**Size:** ~7–10 days.

**Risks:** the largest single piece of work. `sys.settrace` slows execution by 10–100×; tests must be small. The stitcher's decorator window (`MAX_DECORATOR_LINES = 5`) is a heuristic; functions with more decorators are logged, not stitched.

**References:** ADR-0017 §Decision.

### C.2 — Layer 2 log rotation on `--clean`

**Priority:** **Medium**
**Status:** Not started
**Depends on:** `scan.yaml` (Phase A.2)
**Rationale:** `--clean` currently deletes `logs/` along with the rest of the state. Once `scan.yaml` exists, log rotation becomes possible: current log → `aicorellator.log.<UTC ts>`, archives beyond `logs.keep_archives` (default 5) pruned. The `run_scan.py` flow must load `scan.yaml` before `clean()`, which is a small change on top of A.2.

**Size:** ~0.5 day.

**References:** ADR-0016 §Deferred; ADR-0017 §Deferred.

---

## Phase D — Resume the roadmap

### D.1 — `openai_agents` detector

**Priority:** **Medium** (was High — after Phase A)
**Status:** Not started
**Rationale:** Last detector in ADR-0011's implementation order. A user detector for OpenAI Agents SDK is a `FrameworkDetector` subclass in `framework_detectors/`, registered in `registry.py` — same interface as the built-in detectors.

**Caveat:** this detector must not reintroduce the class-A patterns that ADR-0016 removed. It reads bodies via capabilities and framework attributes only.

**Size:** ~1 day.

**References:** ADR-0011.

### D.2 — LLM classifier (`parsing/llm_classifier/`)

**Priority:** **Medium**
**Status:** Not started
**ADR:** ADR-0016
**Rationale:** The replacement for class-A heuristics on projects with custom SDKs. Opt-in (`mode: auto | required | off` in `scan.yaml`). When `mode: auto`, invoked only for symbols the deterministic layers did not classify.

**What changes:**
- `parsing/llm_classifier/prompt.py` — prompt with `[UNTRUSTED_DATA]` markers; its own `PROMPT_VERSION`.
- `parsing/llm_classifier/classifier.py` — `classify(file_path, tree, source, host_id) -> DetectionResult`.
- `parsing/llm_classifier/cache.py` — SQLite cache by `(content_hash, prompt_version, model_id)`.
- `parsing/llm_classifier/config.py` — `ClassifierConfig` from `config/llm_classifier.yaml`.
- `parsing/llm_classifier/runner.py` — orchestration: `off` / `auto` / `required`.
- `parsing/llm_classifier/interactive.py` — `unclassified.action: ask` in TTY.
- `run_scan.py`: invoked after deterministic layers, before `normalize()`; result merged as `DetectionResult`.

**Size:** ~2–3 days.

**Risks:** non-determinism without cache. The cache is mandatory. Temperature 0.0. Prompt versioned independently of `PROMPT_VERSION` (chain evaluation).

**References:** ADR-0016 §Classification layers.

### D.3 — 1.4 — Prompt engineering

**Priority:** **Medium**
**Status:** Partially done (P0/P2 v1.2.0; v1.3.0 `flow_kind`; v1.5.0 group prompt). Full version blocked by Phase A and Phase C.
**Rationale:** The prompt currently says "a chain with data edges is stronger evidence" — and on AST corpora such chains do not exist. The prompt teaches the model to ignore a signal that is never present. After Phase C, data edges exist, and the prompt can be grounded.

**Remaining work:**
- `observed_by` instructions: `["ast"]` vs `["ast", "runtime"]`.
- `uncertain` verdict with concrete criteria.
- Explicit production-vs-test entry point distinction (already partly done in v1.4.1).
- Prompt regression tests.

**Size:** ~1 day.

**References:** ADR-0016 §Positive 6.

### D.4 — 1.3c.2 — Any-node semantics for impact sink capabilities

**Priority:** **Low**
**Status:** Not started
**Rationale:** `_score_read_sinks` walks only the last node of a read chain; `_score_read_writes_db` credits any node. Inconsistent on paper; no-op on the four corpora. On new corpora with `writes_db` mid-chain, scoring will systematically under-count.

**Approach:** widen `dangerous_sink` / `exfil_or_write` to any-node, or narrow `writes_db` to terminal-only. Prefer widening.

**Size:** ~0.5 day.

**References:** ADR-0012 table.

### D.5 — Guardrails — `guards` edge emission

**Priority:** **High** (part of B.4)
**Status:** Partially done (node kind only).
**Rationale:** `guards` is registered, `flow_kind = "structural"`, and nothing emits it.

**Approach:** detect guardrail-to-agent and guardrail-to-tool relationships. Emit `guards` edges with `flow_kind = "structural"`. Depends on guardrail attributes (D.6).

### D.6 — Guardrails — attributes

**Priority:** **High** (part of B.4)
**Status:** Not started
**Rationale:** Nodes exist without attributes. To know whether a guardrail blocks a chain, the graph needs to know what it filters and where it sits.

**Approach:** source-level analysis. `policy` ∈ `prompt_injection`, `pii`, `tool_allowlist`, `rate_limit`, `unknown`. `enforcement_point` ∈ `pre_tool_call`, `post_llm_response`, `pre_agent_step`, `unknown`.

### D.7 — Guardrails — `bypassed_by` derived edge

**Priority:** **Medium** (part of B.4)
**Status:** Blocked by D.5.

### D.8 — Guardrails — guardrail-aware chain evaluation

**Priority:** **Medium** (part of B.4)
**Status:** Blocked by B.4.

---

## Deferred (documented, not scheduled)

### 1.3e.2 — Linear-chain prompt v1.6.0

**Priority:** **Low**
**Rationale:** The group prompt (v1.5.0) and the linear prompt (v1.4.1) diverge. Compare line by line; either lift the group's "OUR SCORING" framing into the linear prompt, or document the divergence. **Blocked by D.2** (the prompt gains `observed_by`).

### `openai_agents` — enrichment parity

**Priority:** **Low**
**Rationale:** When D.1 lands, verify it does not reintroduce the "enrichment only when the prompt names the tool" pattern.

### Enrichment — cross-file `uses_tool`

**Priority:** **Medium**
**Rationale:** NMB's 9 `uses_tool` edges are built by `cross_file

### 1.3c.1 — Triples and quadruples (`max_chain_group_size > 2`)

**Priority:** **Low**
**Status:** Not started.
**Rationale:** ADR-0012's default is pairs only (`max_chain_group_size = 2`). Scoring and selection code paths for larger groups are not implemented. The cap exists in the ADR, not in the code.

---

## Icebox (deferred indefinitely)

- **TUI** (was Medium × 3 — demoted; not a security tool feature).
- **Non-Python runtime analysis** (eBPF for Go/Rust, bytecode agents for Java). Deferred until Python works (Phase C).
- **Multi-host (Core + Agent).**
- **Schema migrations.** Deferred until the schema stabilizes. The DB is recreated per scan.
- **SCIP integration.** TSA provides symbols; that is what the AST layer needs.
- **Standalone executable** (mac / linux / win). A Docker image is under "Low" and covers most of the same ground.
- **Production runtime monitoring.** Not planned. Taint tracking belongs in staging or test.
- **Phase 2 enrichment queue + static workers** (AAK, LLM-scanner, MCP-scanner). Not planned; the honest description is "static correlator with an enrichment interface that has no implementations".
- **DynamicWorker implementations** (file watcher, Docker diff, OSSEC).

---

## Low — when there is time

### Docker image

**Priority:** **Low**
**Rationale:** The current entry cost is `pip install -e .` + `uvx` + Python 3.11+. A Docker image removes the Python/uv dependency from the user's machine. It is not a "standalone executable" (which is Icebox) — it is a runnable artifact for evaluation.

### CLI wrapper (`cli.py`) and console script (`[project.scripts]`)

**Priority:** **Low**
**Status:** Not started.
**Rationale:** Currently the entry points are `run_scan.py`, `run_eval.py`, `run_report.py`. A single `aicorellator` command with subcommands would be friendlier, but it is not a security-tool feature.

### TSAOutput cleanup

**Priority:** **Low**
**Status:** Not started.
**Rationale:** `TSAOutput.call_edges` is never populated (the TSA investigation, item 1.1). The field is dead. Remove it, or document that it stays empty by design.

### Relative imports in cross-file resolution

**Priority:** **Low**
**Status:** Not started.
**Rationale:** `framework_detectors/cross_file.py` skips relative imports (`from .tools import X`) — resolving them requires a package root, which the manifest does not provide. Rare in agent code; deferred.

### Per-name resolution in cross-file tool edges

**Priority:** **Medium** — see "Precision" below.

### `MAX_CHAINS_PER_ENTRY` — configurable

**Priority:** **Low**
**Rationale:** `reporting/markdown.py` caps at 30 chains per entry point. On FinBot, 182 chains → "… and 152 more chains". The summary count is truthful; the detail is truncated. Make it configurable, or remove the cap and paginate.

---

## Precision — better matching, not more matching

### Enrichment — cross-file `uses_tool`

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** NMB's 9 `uses_tool` edges are built by `cross_file.py` (import-driven) and never read the system prompt, so they stay `control` / `unknown`. The enrichment that `LangChainDetector._find_agent_edges` applies within a file does not run for cross-file edges.

### Per-name resolution in cross-file tool edges

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** `resolve_agent_tool_edges` links an agent to **every tool in each imported module**. A module with 20 tools produces 20 edges even if the agent uses 3. Accepted false-positive trade-off (ADR-0009), but it inflates chain counts on real projects.

### Verb families — audit for overreach

**Priority:** **Low**
**Status:** Done (2026-10-20).
**Note:** verb families (`repo.get*` etc.) were added to cover `<verb>_<noun>` method names. They match **any** noun. A `repo.get_or_create` lands in reads rather than writes. Audited on the four corpora: no misfires found. Not re-audited since.

### Adapter patterns — monitor

**Priority:** **Low**
**Status:** Done (2026-10-20).
**Note:** the adapter patterns are a new source of false positives (they match `adapter.execute`, `client.post`, etc.). No corpus has exercised them yet at scale.

### Test-file sinks — monitor

**Priority:** **Low**
**Status:** Done (2026-10-20) — prompt note in voter, solo, judge.

### Cursor fetch reads — monitor

**Priority:** **Low**
**Status:** Done (2026-10-20).

### Dev script discovery

**Priority:** **Low**
**Rationale:** `scripts/test_websocket.py` (FinBot) contributes a sink `push_event` and a chain edge — low-value noise. `is_dev_code: true` node attribute or discovery exclusion.

---

## Reporting

### SARIF output

**Priority:** **Low**
**Status:** Deferred until a consumer appears.

### Report tiers (`--fast` / `--full`)

**Priority:** **Low**
**Status:** Not started.

### Incremental reports

**Priority:** **Low**
**Status:** Not started.

### Report honesty — candidate vs finding

**Priority:** **Medium**
**Rationale:** A `report.md` on a Phase 1 scan shows "Candidate chains (Phase 1 enumeration)" — and 182 candidates look like 182 findings to a reader who does not read the header.

**Two fixes:**
1. Rename the section to "Enumerated paths (not findings)".
2. Add a banner: "This report contains candidate paths, not evaluated findings. Run `python run_eval.py` to evaluate."

Neither is a code change; both are a trust change.

### `run_scan.py` writes a bundle — but ADR-0004 says "report on demand"

**Priority:** **Low** (documentation)
**Rationale:** ADR-0004 says reports are generated on request. `run_scan.py` always writes a bundle at the end. Either ADR-0004 is wrong, or `run_scan.py` should not write. **Recommendation:** keep the bundle write (it is useful for Phase 1 inspection), and update ADR-0004 to say "the initial Phase 1 bundle is written by the scan; evaluated reports are on-demand". A one-line ADR update.

### Report — `data_edges_only` on an AST-only scan

**Priority:** **Low**
**Rationale:** After ADR-0016, an AST-only scan has `data_edges_only = []` by construction. The report should say so explicitly, not leave the reader to infer it. A one-line note in the chains section: "This scan has no runtime layer; data-flow edges are not produced. See `Documentation/Architecture/OVERVIEW.md` §Layer 2."

---

## Cancelled / absorbed (do not resurrect)

### `1.2a.1` — Analyzer asks the right question

**Status:** Cancelled (superseded by ADR-0016).
**Rationale:** The AST layer does not compute `data_flow_confirmed` after ADR-0016. The analyzer is the mechanism Layer 2 will use, but its output does not land on AST edges. Improving it on the AST side is improving something that is not written.

### `1.2a.2` — Enrichment on MCP edges

**Status:** Cancelled (superseded by ADR-0016).
**Rationale:** Same reason: `data_flow_confirmed` does not land on AST edges after ADR-0016.

### `1.2a.2a` / `1.2a.2b` / `1.2a.2c` — Enrichment sub-items

**Status:** Cancelled (superseded by ADR-0016).
**Rationale:** Same reason. The enrichment path was an attempt to compute `data_flow_confirmed` on the AST layer; ADR-0016 removed that target.

### `1.2a.3*` — Argument analysis refinements

**Status:** Cancelled (superseded by ADR-0016).
**Rationale:** Same reason. The refinements improved `data_flow_confirmed` values that no longer land on AST edges.

### `1.2a.4` — Constant collections

**Status:** Cancelled (superseded by ADR-0016).
**Rationale:** Same reason.

### `1.2a.5` — Class-kind sinks

**Status:** Cancelled (superseded by ADR-0016).
**Rationale:** Same reason. The class-kind sink analyzer remains in `flow/` (Layer 2 uses it), but its result no longer lands on AST nodes.

### Old "ADR-0016 — `data_flow_confirmed` quality"

**Status:** Cancelled (ADR-0016 is written and accepted; it is not "quality", it is "the AST layer does not answer this question").
**Rationale:** The old item framed the problem as "improve `data_flow_confirmed` quality". The accepted ADR framed it as "the AST layer does not answer this question". The framing that won is the second.

### `1.5` — Graph densification

**Status:** Cancelled (superseded by ADR-0016).
**Rationale:** Densification was proposed as a way to give flow analysis more edges to work with. After ADR-0016, flow analysis on the AST side is not a target. Densification remains a Layer 1a concern only if it improves control-graph coverage — that is a different item, not this one.

### `1.2a.3` — ORM call sites in capability patterns

**Status:** Cancelled (absorbed by 1.2a.3b/c/e/f/g, which are themselves cancelled).
**Rationale:** The parent was "Done — covered by children". The children are cancelled. The parent is cancelled.

### `structural_heuristics.py`

**Status:** Cancelled. Its role was absorbed by `classification.py`.

---

## Maintenance

- **Update priorities** when external conditions change.
- **Update status** when work begins or ends.
- **Add new entries** when a deferred item surfaces.
- **Remove entries** when they are done or cancelled. Keep "Recently done" to one quarter.
- **Do not** add a "Done" entry whose sub-items are still open — mark it "Partially done" and link the open children.
- **Do not** add a "Low" item whose rationale is "no-op on the four corpora". On a new corpus, "no-op" is a hypothesis, not a fact. If the effect is unknown, say "unknown".
- This file records *what to do next*. The ADRs remain the authoritative record of *why*.

---

## Open questions

These are not backlog items; they are decisions the next planning session should settle before the backlog is revised again.

### Resolved by ADR-0016 / ADR-0017

1. **Is `data_flow_confirmed` a promise, or enrichment metadata?** → **Resolved.** It is enrichment metadata. The AST layer does not answer the question; `data_flow_confirmed` is `NULL` on AST edges and populated only by Layer 2.
2. **Is `lethal_trifecta` a claim?** → **Resolved.** Yes, and it is valid on the AST layer. It is a node property, computed from capabilities. Runtime strengthens the path evidence around it, not the flag.
3. **Does `validated: true` mean "correct"?** → **Resolved in principle, not yet in code.** ADR-0016 does not change `validated`; the Layer 3 semantic validation item (A.4) does. Until A.4 lands, `validated: true` still means "exists", not "correct". The ADR for Layer 3 semantic validation (not yet written) will document the reputation change.

### Still open

4. **Is Phase 2 (runtime) planned at all?** → **Resolved:** yes, Phase C. The README and ADR-0003 have been updated to say so. ADR-0017 specifies the stitching.
5. **Should `provides_tool` chains survive `data_edges_only=True`?** → **Still open.** The parameter is named "data edges only" and `provides_tool` is control — by construction, MCP chains vanish on the shortlist. Either the parameter is misnamed, or MCP is not a first-class use case. Decide after Phase C, when runtime produces real data edges and the question can be answered with data.
6. **Is `user_upload` a source family?** → **Still open.** ADR-0010 lists four families; the implementation supports one and a half (two read-side, one write-side). The gap is acknowledged in B.2 and B.3. Decide when B.3 lands.
7. **What is the identity of an `untrusted_source` node when two read sites are the same store?** → **Still open.** B.8 will produce a short ADR or documentation note.

---

## Roadmap at a glance

| Phase | What | Stops at |
|---|---|---|
| A | Implement ADR-0016 and ADR-0017 (schema, `scan.yaml`, class-A removal, Layer 3 semantic) | v0.2 — the graph is honest |
| B | Close the coverage gaps (cross-file tool → sink, write-side, guardrails, judge determinism) | — |
| C | Layer 2 runtime (Python) | v1.0 — `data_edges_only > 0` on a real corpus |
| D | Resume the roadmap (`openai_agents`, LLM classifier, prompt engineering) | v1.1 — framework-agnostic, grounded prompt |