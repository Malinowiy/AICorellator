# AICorellator — Backlog

Single registry of deferred, in-progress, and planned work. Complements the ADRs (which record *decisions*) with a working list of *what to do next*.

**Last updated:** 2026-10-20

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
- `Done` — completed. Kept briefly for reference; remove after a quarter.

---

## Recently done

### 2026-10-20 — ADR-0015 design and documentation

- **ADR-0015 accepted** (design). Control flow vs data flow: `flow_kind` on every edge, `data_flow_confirmed` on `has_access_to`, structural edges excluded from `find_chains`, `data_edges_only` parameter.
- **ADR-0009 updated** — edge capabilities are orthogonal to `flow_kind`.
- **ADR-0013 updated** — cross-file edges carry `flow_kind`.
- **ADR-0007 updated** — `guards` edge carries `flow_kind = "structural"`.
- **C4 L2 updated** — `flow_kind` lives in Graph Store; no container boundary change.
- **C4 L3 Phase 1 updated** — Flow Analyzer component; `flow_kind` in normalizer pipeline.
- **C4 L3 Phase 3 created** — Analysis and Reporting component view, including the prompt contract for `flow_kind`.

### 2026-10-14 — TSA investigation (item 1.1)

- **TSA `--structure --format json` provides symbols only.** Classes, methods, fields, imports, annotations, line ranges. No call edges, no type information, no argument information.
- **Implication:** `TSAOutput.call_edges` will never be populated. Flow-kind determination is our own AST analysis.
- **Action:** `TSAOutput.call_edges` cleanup (see Parsing). Unblocks 1.2.

### 2026-10-14 — P0/P2 review fixes (commit 2263fa7)

- `voters.py` — `exploitable` must be a real JSON boolean; non-boolean rejected as invalid.
- `judge.py` — per-judge `temperature`/`max_tokens` overrides honored.
- `evaluator.py` — node/edge serialization includes capabilities and precision; evidence validated against the candidate chain (`evidence_valid` / `evidence_unknown`).
- `prompts.py` — `PROMPT_VERSION` v1.1.0 → v1.2.0; calibration rules for FinBot FP root cause.

### Earlier

- **CLI and Streamlit entry points** (commit fc266b0). `if __name__ == "__main__":` at module level; Streamlit `st.chat_input` / `st.button` / `st.form_submit_button` / `st.file_uploader`. Both marked with `entry_kind`.
- **Project backlog created** (commit 8c78d15).
- **README user manual** (commit 49f0cd9).
- **PolyForm Noncommercial 1.0.0 license** (commit dd6da36).

### Deferred from the P0/P2 review

- **Judge determinism.** The judge runs with `temperature=0.6` (from `models.yaml`). Deterministic verdicts require `0.0`. Priority: Low. Configuration choice, not a code default.
- **Raw voter response persistence.** ADR-0005 asks for provenance; `evaluator.py` drops `raw` before persistence. Decision needed: store or not. Priority: Medium.

---

## Flow Semantics and Chain Precision

The graph mixes control flow and data flow, does not track taint, and produces chains whose verdicts depend on sampling noise rather than structure. On FinBot, ~160 of 180 chains are false positives. This category addresses the root cause. It sits at the top of the backlog because it is critical for MVP.

**Reference:** `Documentation/Analysis/FP_TP_analysis.md`.

### 1.0 — Write the FP/TP analysis document

**Priority:** High
**Status:** **Done** (2026-10-13)
**Result:** `Documentation/Analysis/FP_TP_analysis.md` — three FP examples, three TP examples, structural observations.

### 1.1 — Investigate what TSA can still provide

**Priority:** High
**Status:** **Done** (2026-10-14)
**Finding:** TSA `--structure --format json` provides symbols only — classes (name, visibility, line_range), methods (name, visibility, line_range, class_name, is_method), fields, imports, annotations. No call edges, no type information, no argument information. `TSAOutput.call_edges` will never be populated from TSA.
**Action taken:** Recorded in "Recently done". Unblocked 1.2. Triggers a cleanup item (Parsing / TSAOutput cleanup).

### 1.2 — ADR-0015: Control flow vs data flow

**Priority:** High
**Status:** **Design done** (2026-10-20). Implementation is 1.2a.
**Result:** `Documentation/ADR/ADR_015.md` accepted. `flow_kind` on every edge (`control` | `data` | `structural`). `has_access_to` additionally carries `data_flow_confirmed` (six values). Structural edges are excluded from `find_chains`. `data_edges_only` parameter on `find_chains`. Prompt receives `flow_kind` and `data_flow_confirmed`. No hard calibration rules — the LLM decides confidence.
**References:** ADR-0015, ADR-0009 (updated), ADR-0013 (updated), ADR-0007 (updated), C4 L2 (updated), C4 L3 Phase 1 (updated), C4 L3 Phase 3 (created).

### 1.2a — Implement ADR-0015

**Priority:** High
**Status:** Not started. Next item after documentation.
**Rationale:** The design is accepted, but no code exists yet. Every edge in the graph is still missing `flow_kind`, and `data_flow_confirmed` is not computed. Until implementation, the FinBot FP rate is unchanged — the prompt calibration (P0/P2, v1.2.0) mitigates the symptom, but the payload still lacks the facts the LLM needs.
**Approach:**
1. **Schema** — `edges.flow_kind TEXT NOT NULL DEFAULT 'control'` with `CHECK`. `edges.data_flow_confirmed TEXT` (nullable) with `CHECK`. `graph/schema.sql`.
2. **Models** — `Edge.flow_kind: str`, `Edge.data_flow_confirmed: str | None`. `graph/models.py`. `VALID_FLOW_KINDS`.
3. **Patterns** — `FLOW_KINDS`, `STRUCTURAL_EDGE_KINDS`. `patterns.py`.
4. **Flow Analyzer** — `_analyze_data_flow(tool_body, sink_call)` in `ast_normalizer.py` (or a dedicated `flow_analyzer.py`). Name-based argument matching: parameter, local variable, call result, literal, no args, unknown.
5. **Normalizer** — set `flow_kind` on every edge. `structural` for `provides_tool` and `guards`. `control` for `uses_model`, `uses_tool`, `delegates_to`. `control` or `data` for `has_access_to`, based on `data_flow_confirmed`.
6. **Query layer** — `find_chains` gains `data_edges_only: bool = False`. Traverses `control` and `data` edges only; `structural` edges are excluded.
7. **Prompt** — `flow_kind` and `data_flow_confirmed` in the edge payload. `PROMPT_VERSION` → v1.3.0. Instructions: data chains are stronger evidence; structural edges on a chain path are a construction error; the LLM decides confidence.
8. **Tests** — unit tests for the Flow Analyzer (all six values); integration tests for `find_chains` with `data_edges_only` true and false; regression on FinBot (expected chain count drop from ~180 to a few).
9. **Update ADR-0015** — status `Accepted`, with a note that implementation is in place.

**References:** ADR-0015.

### 1.3 — Capability-based taint, combined with non-linear chains

**Priority:** High
**Status:** Blocked by 1.2a (needs `flow_kind` in the graph) and by `reads_untrusted`
**Rationale:** Even with `flow_kind`, a chain is only exploitable if untrusted data enters somewhere and reaches a dangerous sink. Capability-based taint gives this without full dataflow analysis. Non-linear chains (chain groups, ADR-0010) require untrusted sources.
**References:** ADR-0009, ADR-0010, ADR-0012, TP examples #1, #2.
**Approach:**
- Entry point must have `reads_untrusted` or `http_ingress` or `cli_input` (new capability).
- Path must contain at least one `data_flows_to` edge (currently: `flow_kind = "data"` on `has_access_to`).
- Sink must have a sink capability.
- Combine with `find_chain_groups` for non-linear chains.

### 1.3a — Architecture and data-model review

**Priority:** High
**Status:** Blocked by 1.2a and 1.3
**Rationale:** Introducing capability-based taint and non-linear chains changes the data model further (taint sources, chain groups table usage). This review happens once 1.2a and 1.3 are in place, so it can verify the whole picture, not the first half.
**Deliverables:**
1. **Data-model review** — taint sources on nodes, `chain_groups` table population, any new tables or columns.
2. **Python model review** — `Node.lethal_trifecta`, `Node.taint_sources`, `ChainGroup`.
3. **Query layer review** — `find_chain_groups`, interaction with `data_edges_only`.
4. **Normalizer review** — taint propagation.
5. **Prompt contract review** — taint information in the payload, prompt version bump.
6. **ADR updates** — ADR-0009, ADR-0010, ADR-0015, ADR-0016 (taint model, if needed).
7. **C4 updates** — C4 L3 Phase 1, C4 L3 Phase 3.
8. **Test plan.**

### 1.4 — Prompt engineering: grounded evaluation

**Priority:** High
**Status:** **Partially done** (P0/P2, v1.2.0: calibration rules for the FinBot root cause). Full version blocked by 1.2a (prompt receives `flow_kind` only after implementation).
**Rationale:** The FP examples show the LLM gives contradictory verdicts on structurally identical chains. The prompt lacks grounding criteria. The P0/P2 fix added calibration rules; the full fix needs `flow_kind` in the payload.
**Approach (remaining after 1.2a):**
- Prompt v1.3.0 receives `flow_kind` and `data_flow_confirmed`.
- Instructions: data chains are stronger than control chains; structural edges on a path are a construction error; `data_flow_confirmed` describes how the data flow was established.
- `uncertain` verdict with concrete criteria for structural reachability without implementation details.
- Test scripts and dev scripts distinguished from production entry points.
- **No hard confidence rules.** The LLM decides.

### 1.5 — Graph densification

**Priority:** Medium
**Status:** Deferred until 1.2a is complete and 1.3 design is in place.
**Rationale:** The graph is sparse (FinBot: 283 nodes, 93 edges — ratio 0.33). Many real paths go through intermediate functions that are not classified. Adding call-graph edges without flow semantics would worsen false positives: more paths, same lack of data-flow distinction. Densification must come after `flow_kind` and taint are in place.
**Approach (when unblocked):**
- Add call-graph edges (`calls`) between all functions.
- Add import edges (`imports`) between modules.
- Extend cross-file resolution to all edge kinds.
- Re-tune `max_hops` — denser graphs produce longer paths.

### 1.6 — Lethal trifecta detection

**Priority:** High
**Status:** Blocked by 1.3 (needs `reads_untrusted`)
**Rationale:** ADR-0009 defines the combination but nothing implements it. Cheap to implement once `reads_untrusted` exists.
**Approach:** On node write, compute `lethal_trifecta`. Chains passing through such nodes get `priority: high`.

---

## Parsing

### Cross-file tool → sink

**Priority:** High
**Status:** Not started
**Rationale:** Currently `has_access_to` (tool → sink) is built only within a single file. In mcploitable and dvmcp, 8–12 sink nodes have no incoming edges because the tools that reach them live in other files.
**Approach:** Extend the call graph to span imports. Reuse the import table in `entry_points._collect_module_imports`. Populate `flow_kind` and `data_flow_confirmed` on the resulting edges (ADR-0015).

### `openai_agents.py` detector

**Priority:** High
**Status:** Not started
**Rationale:** Last detector in ADR-0011 implementation order.

### TSAOutput cleanup

**Priority:** Low
**Status:** Not started
**Rationale:** `TSAOutput.call_edges` and `TSAOutput.precision` are unused and will never be populated from TSA (item 1.1). Keeping them is misleading.
**Approach:** Remove `call_edges` from `TSAOutput`. Either remove `precision` or document it is always `"syntactic"`. Update `tsa_worker.py` and tests.

### Additional TSA modules for non-Python languages

**Priority:** Low
**Status:** Deferred until a non-Python target appears

### Relative imports in cross-file resolution

**Priority:** Low
**Status:** Not started

### Per-name resolution in cross-file tool edges

**Priority:** Medium
**Status:** Not started

---

## Analysis

### Chain groups (ADR-0010)

**Priority:** High
**Status:** Not started. Goes with Flow Semantics item 1.3.

### Chain group prioritization (ADR-0012)

**Priority:** High
**Status:** Not started. Goes with chain groups.

### `reads_untrusted` capability

**Priority:** High
**Status:** Blocked by untrusted source detection. Goes with Flow Semantics item 1.3.

### Dynamic chain selection

**Priority:** Medium/Low
**Status:** Not started. TUI feature.

### Small-model pre-scoring

**Priority:** Low
**Status:** Not started.

---

## Guardrails

### `guards` edge emission

**Priority:** High
**Status:** Partially done (node kind only). When emitted, the edge carries `flow_kind = "structural"` (ADR-0015).

### Guardrail attributes

**Priority:** High
**Status:** Not started.

### `bypassed_by` derived edge

**Priority:** Medium
**Status:** Blocked by `guards` edge.

### Guardrail-aware chain evaluation

**Priority:** Medium
**Status:** Not started.

---

## Untrusted Sources

### `untrusted_source` node kind

**Priority:** High
**Status:** Not started. Goes with Flow Semantics item 1.3.

### Untrusted source detection

**Priority:** High
**Status:** Not started.

### `find_chain_groups` operation

**Priority:** High
**Status:** Not started.

---

## Reporting

### SARIF output

**Priority:** Low
**Status:** Deferred until a consumer appears.

### Report tiers (`--fast` / `--full`)

**Priority:** Low
**Status:** Not started.

### Incremental reports

**Priority:** Low
**Status:** Not started.

### Notifications

**Priority:** Low
**Status:** Not started.

---

## Runtime (Phase 2)

### DynamicWorker implementations

**Priority:** Icebox

### MCP-audit-tool integration

**Priority:** Icebox

### garak integration

**Priority:** Icebox

### Enrichment queue + static workers

**Priority:** Icebox

### File watcher / Docker diff / OSSEC

**Priority:** Icebox

---

## Infrastructure

### Schema migrations

**Priority:** Icebox
**Status:** Deferred until the schema stabilizes.

### SCIP integration

**Priority:** Icebox
**Status:** Deferred until a target requires it. TSA does not provide call edges (item 1.1), so SCIP would not change flow-kind determination.

### Multi-host (Core + Agent)

**Priority:** Icebox

### `structural_heuristics.py`

**Priority:** Cancelled. Its role was absorbed by `classification.py` (ADR-0011).

---

## TUI

### TUI MVP: Scan / Graph / Chains

**Priority:** Medium

### TUI: Evaluate / Report / Config

**Priority:** Medium

### TUI: dynamic chain selection

**Priority:** Medium/Low

---

## CLI

### `cli.py` wrapper

**Priority:** Low

### Console script (`[project.scripts]`)

**Priority:** Low

### Standalone executable (mac + linux + win)

**Priority:** Icebox

### Docker image

**Priority:** Icebox

### `run_*.py` wrappers

**Priority:** Low

---

## Maintenance

- **Update priorities** when external conditions change.
- **Update status** when work begins or ends.
- **Add new entries** when a deferred item surfaces.
- **Remove entries** when they are done or cancelled.
- **Recently done** keeps a short log; remove entries after a quarter.
- This file records *what to do next*. The ADRs remain the authoritative record of *why*.