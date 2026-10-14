# AICorellator — Backlog

Single registry of deferred, in-progress, and planned work. Complements the ADRs (which record *decisions*) with a working list of *what to do next*.

**Last updated:** 2026-10-14

**Priority legend:**

- **Critical** — blocks MVP. Nothing in this category at the moment.
- **High** — next session or two.
- **Medium** — within a month.
- **Low** — when there is time.
- **Icebox** — deferred indefinitely; not cancelled.
- **Cancelled** — decided not to do.

**Status legend:**

- `Not started` — no work yet.
- `In progress` — actively being worked on.
- `Blocked by X` — waiting on another item.
- `Deferred until Y` — waiting on an external condition.
- `Partially done` — some work complete, some remaining.

---

## Recently done

### P0/P2 review fixes (commit 2263fa7)

- `voters.py` — `exploitable` must be a real JSON boolean. `bool("false")` no longer becomes True; a non-boolean value is treated as an invalid response, excluded from aggregation.
- `judge.py` — per-judge `temperature`/`max_tokens` overrides are now honored (ADR-0005: per-model overrides win).
- `evaluator.py` — node/edge serialization includes capabilities and precision; voter evidence is validated against the candidate chain (`evidence_valid` / `evidence_unknown`).
- `prompts.py` — `PROMPT_VERSION` v1.1.0 → v1.2.0. Calibration rules added targeting the FinBot false-positive root cause: colocation ≠ invocation, no assumed data flow without a data-flow capability, production vs test/dev entry points, prefer low confidence over invented scenarios.

### Deferred from the P0/P2 review

- **Judge determinism.** The judge currently runs with `temperature=0.6` (from `models.yaml`). If deterministic verdicts are wanted, set it to `0.0`. Priority: Low.
- **Raw voter response persistence.** ADR-0005 asks for provenance, but `evaluator.py` drops `raw` before persistence (`d.pop("raw")`). Decision needed: store or not. Priority: Medium.

### Also done

- **CLI and Streamlit entry points** (commit fc266b0). `if __name__ == "__main__":` at module level and Streamlit `st.chat_input` / `st.button` / `st.form_submit_button` / `st.file_uploader`. Both marked with `entry_kind`.
- **Project backlog created** (commit 8c78d15).
- **README user manual** (commit 49f0cd9).
- **PolyForm Noncommercial 1.0.0 license** (commit dd6da36).

---

## Flow Semantics and Chain Precision

The graph mixes control flow and data flow, does not track taint, and produces chains whose verdicts depend on sampling noise rather than structure. On FinBot, ~160 of 180 chains are false positives. This category addresses the root cause. It sits at the top of the backlog because it is critical for MVP: without it, the tool produces a graph but not actionable results.

**Reference:** `Documentation/Analysis/FP_TP_analysis.md` (item 1.0).

### 1.0 — Write the FP/TP analysis document

**Priority:** High
**Status:** Not started
**Rationale:** The FP/TP examples from FinBot must be recorded so future work references concrete cases, not memory. Without this, the next session starts from scratch.
**Approach:** Create `Documentation/Analysis/FP_TP_analysis.md` with three sections: FP examples (why the LLM was wrong), TP examples (why the chain is a real hypothesis), structural observations (what distinguishes them). Include the three FP and three TP examples from the analysis.

### 1.1 — Investigate what TSA can still provide

**Priority:** High
**Status:** **DONE** (2026-10-14)

**Finding:** TSA `--structure --format json` provides only symbol information: classes (name, visibility, line_range), methods (name, visibility, line_range, class_name, is_method), fields, imports, annotations. It does **not** provide call edges, type information, or argument information.

**Implication:** `TSAOutput.call_edges` will never be populated from TSA. Flow-kind determination (ADR-0015) must be built on our own AST analysis, which already provides a call graph (`entry_points._collect_call_names`). TSA remains useful for symbol extraction across languages but adds nothing for flow semantics.

**Action:** Remove or deprecate `TSAOutput.call_edges` (see Parsing / TSAOutput cleanup). Unblocks 1.2.

### 1.2 — ADR-0015: Control flow vs data flow

**Priority:** High
**Status:** **Unblocked** (1.1 done). Ready to start.

**Rationale:** The graph currently has one class of edges that mixes control flow and data flow. TSA does not help here — flow-kind determination is our own AST analysis.

**References:** 1.1 finding, ADR-0009, ADR-0013, FP examples.

**Approach:** Introduce `flow_kind` on edges: `control` | `data` | `structural`. Split `has_access_to` into `calls` (control) and `data_flows_to` (data). Determine `flow_kind` through AST-level argument analysis — name-based, not value-based, to stay within ADR-0001's "no full dataflow" constraint. The call graph already collected in `entry_points._collect_call_names` is the starting point; extend it with argument-name matching against sink parameters.

### 1.3 — Capability-based taint, combined with non-linear chains

**Priority:** High
**Status:** Blocked by 1.2
**Rationale:** Even with `flow_kind`, a chain is only exploitable if untrusted data enters somewhere and reaches a dangerous sink. Capability-based taint gives this without full dataflow analysis. Additionally, the current `find_chains` enumerates only linear paths; real attacks often require non-linear structure (one chain writes to an untrusted source, another reads from it — chain groups, ADR-0010).
**References:** ADR-0009 (capabilities), ADR-0010 (chain groups), ADR-0012 (prioritization), TP examples #1, #2.
**Approach:**
- Entry point must have `reads_untrusted` or `http_ingress` or `cli_input` (new capability).
- Path must contain at least one `data_flows_to` edge.
- Sink must have a sink capability (`executes_code`, `executes_shell`, `writes_filesystem`, `writes_db`, `http_egress`).
- Combine with `find_chain_groups` for non-linear chains — chains that meet through an `untrusted_source`.

### 1.3a — Architecture and data-model review

**Priority:** High
**Status:** Blocked by 1.2 and 1.3
**Rationale:** Introducing `flow_kind`, capability-based taint, and non-linear chains changes the data model, the graph schema, the query layer, the normalizer, and the Phase 3 prompt contract. Without an explicit review, documentation and schema drift from the code — as happened with `uses_tool` (edge kind added in code but not in `VALID_EDGE_KINDS`, `schema.sql`, or `patterns.EDGE_KINDS` until a test caught it).

**Deliverables:**

1. **Data-model review** — what changes in SQLite:
   - New column on `edges`: `flow_kind TEXT NOT NULL DEFAULT 'control'` with `CHECK (flow_kind IN ('control', 'data', 'structural'))`.
   - New edge kinds: `calls`, `data_flows_to` (replacing or complementing `has_access_to`).
   - New capabilities: `reads_untrusted`, `cli_input`.
   - New node attributes: `lethal_trifecta` (boolean), `taint_sources` (JSON list).
   - Possible new tables: `taint_paths` (if chain groups need explicit storage of data-flow paths).
   - Decision: recreate per scan (ADR-0003) or introduce migrations now.

2. **Python model review** — `graph/models.py`:
   - `Edge.flow_kind: str`.
   - `Node.lethal_trifecta: bool`, `Node.taint_sources: list[str]`.
   - Updates to `VALID_EDGE_KINDS`, `VALID_NODE_KINDS`, `VALID_FLOW_KINDS`.

3. **Query layer review** — `graph/query.py`:
   - `find_chains` filtering by `flow_kind` and taint.
   - `find_chain_groups` implementation.
   - Impact on `max_hops`, `max_candidates`.

4. **Normalizer review** — `parsing/ast_normalizer.py`:
   - How `flow_kind` is determined (AST-level, name-based).
   - How taint sources are propagated.
   - How `lethal_trifecta` is computed.

5. **Prompt contract review** — `analysis/prompts.py`:
   - What new fields the prompt receives (`flow_kind`, taint info, guardrail info).
   - How the LLM is expected to use them.
   - Prompt version bump.

6. **ADR updates:**
   - ADR-0009 (capabilities) — `flow_kind` on edges.
   - ADR-0010 (chain groups) — non-linear chains.
   - ADR-0013 (cross-file) — flow-kind-aware resolution.
   - ADR-0015 (new) — control flow vs data flow.
   - ADR-0016 (new, if needed) — taint model.
   - ADR-0003 (graph formation cycle) — if migrations are introduced.

7. **C4 updates:**
   - C4 L3 Phase 1 — normalizer pipeline, query layer.
   - C4 L3 Phase 3 — prompt structure, evaluation flow.
   - C4 L2 — if any container boundaries change.

8. **Test plan:**
   - Unit tests for `flow_kind` determination.
   - Unit tests for taint propagation.
   - Integration tests for `find_chains` with taint filter.
   - Tests for `lethal_trifecta` computation.
   - Prompt regression tests (can the LLM distinguish control from data flow?).

**Approach:** Write the review as concrete documents (or update existing ones), get sign-off, then implement. No code changes in this step — only design artifacts.

### 1.4 — Prompt engineering: grounded evaluation

**Priority:** High
**Status:** Blocked by 1.3a (prompt receives new fields defined there)
**Rationale:** The FP examples show the LLM gives contradictory verdicts on structurally identical chains (Chain 28 exploitable, Chain 11 not exploitable — same structure). The prompt lacks grounding criteria. Adding concrete questions and counter-questions will force the LLM to justify its verdict against specific structural properties, not against "typical implications."
**References:** FP example #1 (same structure, opposite verdicts), FP example #2 (speculation about test scripts), TP example #3 (read_config — needs an audit verdict, not a binary one).
**Approach:**
- Add explicit confidence calibration rules: control flow only → ≤ 0.3; data flow without taint source → ≤ 0.5; data flow with taint source and sink capability → ≥ 0.7.
- Add questions the LLM must answer in reasoning: "Does data from the entry point reach the sink? What is the flow kind of each edge? Is there a guardrail in the path?"
- Add the `uncertain` verdict with concrete criteria: structural reachability without implementation details → `uncertain`, not `exploitable`.
- Address the "test script" case: distinguish production entry points from test/dev scripts via path and context.

### 1.5 — Graph densification

**Priority:** Medium
**Status:** Deferred until 1.3a is complete
**Rationale:** The graph is sparse (FinBot: 283 nodes, 93 edges — ratio 0.33). Many real paths go through intermediate functions that are not classified as agents, tools, or sinks, and no edges are built. But adding call-graph edges *without* taint filtering would worsen false positives: more paths, same lack of data-flow distinction. Densification must come after flow semantics are in place.
**References:** FinBot graph ratio.
**Approach (when unblocked):**
- Add call-graph edges (`calls`) between all functions, not just classified nodes.
- Add import edges (`imports`) between modules.
- Extend cross-file resolution to all edge kinds, not just `uses_tool` and `has_access_to`.
- Re-tune `max_hops` — denser graphs produce longer paths.

### 1.6 — Lethal trifecta detection

**Priority:** High
**Status:** Blocked by 1.3a (needs `reads_untrusted` in the data model)
**Rationale:** ADR-0009 defines the combination but nothing implements it. On real AI systems this pattern is not rare — a node that reads untrusted data, accesses secrets, and can send data out is the canonical lethal combination. Cheap to implement once `reads_untrusted` exists.
**References:** ADR-0009, ADR-0010, `patterns.LETHAL_TRIFECTA_*`.
**Approach:** On node write, compute a `lethal_trifecta` attribute. Chains passing through such nodes get `priority: high`. No new node kind needed; this is an attribute.

---

## Parsing

### TSAOutput cleanup

**Priority:** Low
**Status:** Not started
**Rationale:** `TSAOutput.call_edges` and `TSAOutput.precision` are unused and will never be populated from TSA. Keeping them is misleading.
**Approach:** Remove `call_edges` from `TSAOutput`. Either remove `precision` or document that it is always `"syntactic"`. Update `tsa_worker.py` and tests.
**References:** 1.1 finding.

### Cross-file tool → sink

**Priority:** High
**Status:** Not started
**Rationale:** Currently `has_access_to` (tool → sink) is built only within a single file (the B2 resolver, `_link_entry_points_to_sinks`). In mcploitable and dvmcp, 8–12 sink nodes have no incoming edges because the tools that reach them live in other files. This is the same blind spot as agent-to-tool, solved by Resolver 1. When 1.2 introduces `flow_kind`, the resolver must build both `calls` and `data_flows_to` across files.
**References:** ADR-0013 (Deferred, item 6), `resolve_agent_tool_edges` (Resolver 1), Flow Semantics item 1.2.

### `openai_agents.py` detector

**Priority:** High
**Status:** Not started
**Rationale:** Last detector in the ADR-0011 implementation order. The OpenAI Agents SDK is a rising framework; targets using it currently produce an empty graph.
**References:** ADR-0011 (Implementation Order, item 6).

### Additional TSA modules for non-Python languages

**Priority:** Low
**Status:** Deferred until a non-Python target appears
**Rationale:** All current validation targets are Python. Adding Go, Java, Rust, or TypeScript support has no consumer yet.
**References:** ADR-0002, ADR-0001.

### Relative imports in cross-file resolution

**Priority:** Low
**Status:** Not started
**Rationale:** Relative imports are skipped by the import collector because resolving them requires a package root, which the manifest does not provide.
**References:** ADR-0013 (Deferred, item 3).

### Per-name resolution in cross-file tool edges

**Priority:** Medium
**Status:** Not started
**Rationale:** Resolver 1 links an agent to *all* tools in an imported module, not just the tools in the imported name. Accepted false-positive trade-off (ADR-0009), but tighter resolution would reduce Phase 3 load.
**References:** ADR-0013 (Deferred, item 2).

---

## Analysis

### Chain groups (ADR-0010)

**Priority:** High
**Status:** Not started
**Rationale:** Indirect prompt injection requires chain groups, not linear chains. The `chain_groups` table exists in the schema; nothing populates it. Goes together with Flow Semantics item 1.3 (non-linear chains).
**References:** ADR-0010, ADR-0012.

### Chain group prioritization (ADR-0012)

**Priority:** High
**Status:** Not started
**Rationale:** Naive `find_chain_groups` is O(n²) in the LLM evaluation cost. Goes together with chain groups.
**References:** ADR-0012.

### `reads_untrusted` capability

**Priority:** High
**Status:** Blocked by untrusted source detection
**Rationale:** Requires knowing what an untrusted source is (RAG corpus, MCP tool description, email inbox, user upload). Goes with Flow Semantics item 1.3.
**References:** ADR-0010, ADR-0009.

### Dynamic chain selection

**Priority:** Medium/Low
**Status:** Not started
**Rationale:** Built-in prioritization (ADR-0012) is sufficient for MVP. Interactive selection is a TUI feature.
**References:** ADR-0012, TUI section.

### Small-model pre-scoring

**Priority:** Low
**Status:** Not started
**Rationale:** Optimization of Phase 3. Not needed at current chain counts.
**References:** ADR-0005 (Deferred, item 5).

---

## Guardrails

### `guards` edge emission

**Priority:** High
**Status:** Partially done (node kind only)
**Rationale:** ADR-0007 is marked "Partially Accepted". Guardrail nodes are created; the `guards` edge is registered in the schema but no component emits it. Chain semantics do not yet reflect that a guardrail can block a chain. Also relevant to Flow Semantics item 1.4 (prompt must reason about guardrails).
**References:** ADR-0007 (Implementation Status).

### Guardrail attributes

**Priority:** High
**Status:** Not started
**Rationale:** `policy` and `enforcement_point` are defined but not populated. Goes together with `guards` edge emission.
**References:** ADR-0007.

### `bypassed_by` derived edge

**Priority:** Medium
**Status:** Blocked by `guards` edge
**References:** ADR-0007.

### Guardrail-aware chain evaluation

**Priority:** Medium
**Status:** Not started
**Rationale:** Phase 3 should receive guardrail information and reason about whether a chain is blocked.
**References:** ADR-0007, Flow Semantics item 1.4.

---

## Untrusted Sources

### `untrusted_source` node kind

**Priority:** High
**Status:** Not started
**Rationale:** Not in `VALID_NODE_KINDS`. Required for chain groups and for `reads_untrusted`. Goes with Flow Semantics item 1.3.
**References:** ADR-0010.

### Untrusted source detection

**Priority:** High
**Status:** Not started
**Rationale:** RAG corpora, MCP tool descriptions, email inboxes, user uploads. Each needs its own detection logic.
**References:** ADR-0010.

### `find_chain_groups` operation

**Priority:** High
**Status:** Not started
**Rationale:** The core operation for indirect prompt injection.
**References:** ADR-0010, ADR-0012.

---

## Reporting

### SARIF output

**Priority:** Low
**Status:** Deferred until a consumer appears
**References:** ADR-0004.

### Report tiers (`--fast` / `--full`)

**Priority:** Low
**Status:** Not started
**References:** ADR-0004.

### Incremental reports

**Priority:** Low
**Status:** Not started
**References:** ADR-0004.

### Notifications

**Priority:** Low
**Status:** Not started
**References:** ADR-0004.

---

## Runtime (Phase 2)

### DynamicWorker implementations

**Priority:** Icebox
**Status:** Deferred until Phase 2 begins
**Rationale:** Months of work. Requires privileged mode or an MCP proxy.
**References:** ADR-0001, ADR-0003 (Stage 4).

### MCP-audit-tool integration

**Priority:** Icebox
**Status:** Not started
**Rationale:** Dynamic MCP server scanner. Requires runtime infrastructure.

### garak integration

**Priority:** Icebox
**Status:** Not started
**Rationale:** LLM vulnerability scanner. Requires a working LLM session.

### Enrichment queue + static workers

**Priority:** Icebox
**Status:** Deferred until Phase 2 begins
**References:** ADR-0003 (Stage 3).

### File watcher / Docker diff / OSSEC

**Priority:** Icebox
**Status:** Not started
**References:** ADR-0003 (Stage 4).

---

## Infrastructure

### Schema migrations

**Priority:** Icebox
**Status:** Deferred until the schema stabilizes
**Rationale:** The database is recreated per scan (ADR-0003). Revisit when the schema stabilizes, dynamic scanning is in production, and a schema change is required. Flow Semantics item 1.3a may force this decision earlier.
**References:** ADR-0003, C4 L3 Phase 0.

### SCIP integration

**Priority:** Icebox
**Status:** Deferred until a target requires it
**Rationale:** All current targets are Python; TSA covers Python. SCIP adds precision for languages and cases where TSA is heuristic. Cost is high. Per-language TSA modules (Parsing section) are a lighter alternative for new languages.
**References:** ADR-0002, ADR-0003 (Stage 2).

### Multi-host (Core + Agent)

**Priority:** Icebox
**Status:** Deferred
**References:** ADR-0003, C4 L2.

### `structural_heuristics.py`

**Priority:** Cancelled
**Status:** Not started, will not start
**Rationale:** ADR-0011 revised: its role was absorbed by `classification.py`.
**References:** ADR-0011 (Level 3, note).

---

## TUI

### TUI MVP: Scan / Graph / Chains

**Priority:** Medium
**Status:** Not started
**Rationale:** Operational control of the tool. The analyst's working interface, more important than CLI ergonomics.
**Approach:** Textual-based. `src/aicorellator/tui/`. Screens as separate modules. Async tasks for long operations.

### TUI: Evaluate / Report / Config

**Priority:** Medium
**Status:** Not started

### TUI: dynamic chain selection

**Priority:** Medium/Low
**Status:** Not started

---

## CLI

### `cli.py` wrapper

**Priority:** Low
**Status:** Not started
**Rationale:** A thin wrapper that calls `run_scan`, `run_eval`, `run_report`. `python run_scan.py` already works.

### Console script (`[project.scripts]`)

**Priority:** Low
**Status:** Not started
**References:** `pyproject.toml`.

### Standalone executable (mac + linux + win)

**Priority:** Icebox
**Status:** Not started
**Rationale:** Premature. Would need to package Python, tree-sitter-analyzer, `uvx`, and all dependencies. No users without Python exist yet.
**Approach:** PyInstaller or Nuitka, plus packaging `uvx` / tree-sitter-analyzer.

### Docker image

**Priority:** Icebox
**Status:** Not started

### `run_*.py` wrappers

**Priority:** Low
**Status:** Not started

---

## Maintenance

- **Update priorities** when external conditions change.
- **Update status** when work begins or ends.
- **Add new entries** when a deferred item surfaces.
- **Remove entries** when they are done or cancelled.
- This file records *what to do next*. The ADRs remain the authoritative record of *why*.