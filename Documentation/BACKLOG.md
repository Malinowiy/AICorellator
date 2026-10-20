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

### 2026-10-20 — ADR-0015 implementation (backlog item 1.2a)

- **ADR-0015 accepted.** `flow_kind` on every edge (control | data | structural). `data_flow_confirmed` on `has_access_to` (six values). Structural edges (guards) excluded from `find_chains`. `provides_tool` is **control** (access path = attack path until runtime monitoring). `data_edges_only` parameter on `find_chains`. Prompt v1.3.0 receives `flow_kind` and `data_flow_confirmed`.
- **Flow Analyzer** (`parsing/flow_analyzer.py`): name-based argument matching, non-linear ranking, `literal_only` requires all literals.
- **Normalizer:** per-scan module cache; every edge carries `flow_kind`; `id_remap` preserves flow fields.
- **Known limitation:** `data_flow_confirmed = "unknown"` on all edges across all four validation corpora (FinBot, NMB, dvmcp, mcploitable). The generic analyzer finds no direct call from a tool to a sink in these projects — all four are colocation-only. `data_edges_only = 0` everywhere. Two follow-ups recorded as 1.2a.1 and 1.2a.2.
- **TSA fix:** `tsa_worker.py` pins `uvx --python 3.14` (tree-sitter-analyzer 2.2.0 → cachebox 5.2.3 → PyO3 0.27.1 does not support Python 3.15).
- **Tests:** 344 passed.
- **Scans (fresh, post-implementation):**

  | Target | nodes | edges | chains | data_edges_only |
  |---|---|---|---|---|
  | finbot-ctf | 283 | 93 | 182 | 0 |
  | NMB | 26 | 26 | 16 | 0 |
  | dvmcp | 33 | 47 | 39 | 0 |
  | mcploitable | 74 | 45 | 17 | 0 |

### 2026-10-20 — ADR-0015 design and documentation

- **ADR-0015 written and accepted** (design).
- **ADR-0009, ADR-0013, ADR-0007 updated** to reference `flow_kind`.
- **C4 L2, L3 Phase 1, L3 Phase 3 updated** — Flow Analyzer component; `flow_kind` in normalizer pipeline and prompt contract.

### 2026-10-14 — TSA investigation (item 1.1)

- **TSA `--structure --format json` provides symbols only.** No call edges, no type information, no argument information. `TSAOutput.call_edges` will never be populated from TSA. Flow-kind determination is our own AST analysis.

### 2026-10-14 — P0/P2 review fixes (commit 2263fa7)

- `exploitable` must be a real JSON boolean; non-boolean rejected as invalid.
- Per-judge `temperature`/`max_tokens` overrides honored.
- Node/edge serialization includes capabilities and precision; evidence validated against the candidate chain.
- `PROMPT_VERSION` v1.1.0 → v1.2.0; calibration rules for FinBot FP root cause.

### Earlier

- CLI and Streamlit entry points (commit fc266b0).
- Project backlog created (commit 8c78d15).
- README user manual (commit 49f0cd9).
- PolyForm Noncommercial 1.0.0 license (commit dd6da36).

### Deferred from the P0/P2 review

- **Judge determinism.** The judge runs with `temperature=0.6`. Deterministic verdicts require `0.0`. Priority: Low. Configuration choice, not a code default.
- **Raw voter response persistence.** ADR-0005 asks for provenance; `evaluator.py` drops `raw` before persistence. Decision needed: store or not. Priority: Medium.

---

## Flow Semantics and Chain Precision

The graph mixes control flow and data flow, does not track taint, and produces chains whose verdicts depend on sampling noise rather than structure. On FinBot, ~160 of 180 chains are false positives. This category addresses the root cause.

**Reference:** `Documentation/Analysis/FP_TP_analysis.md`.

### 1.0 — Write the FP/TP analysis document

**Priority:** High
**Status:** **Done** (2026-10-13)

### 1.1 — Investigate what TSA can still provide

**Priority:** High
**Status:** **Done** (2026-10-14)

### 1.2 — ADR-0015: Control flow vs data flow

**Priority:** High
**Status:** **Done** (2026-10-20) — design and documentation.

### 1.2a — Implement ADR-0015

**Priority:** High
**Status:** **Done** (2026-10-20) — code in place, tests green, scans run.
**Known limitation:** `data_flow_confirmed = "unknown"` on all edges across all four corpora. `data_edges_only = 0`. Follow-ups: 1.2a.1 and 1.2a.2.

### 1.2a.1 — Analyzer asks the right question

**Priority:** High
**Status:** Not started
**Rationale:** The generic Flow Analyzer asks "does this tool call a function named like the sink?" On real code, the sink node is the **definition** of the dangerous operation, not a call site. The interesting question for FinBot-style layouts is: "how does data reach the dangerous call **inside the sink method's own body**?" (`delete_file(file_id)` → `self.db.delete(f)` → `parameter_match`).
**Approach:** Add a second analyzer pass that inspects the body of each sink function and asks whether tool arguments reach the dangerous call inside it. Keep the existing `has_access_to` analyzer as-is (it correctly reports "no direct call" for colocation layouts).
**References:** Step 6 report; ADR-0015 Deferred.

### 1.2a.2 — Detector enrichment of `data_flow_confirmed`

**Priority:** High
**Status:** Not started
**Rationale:** ADR-0015 decision 7 declares `data_flow_confirmed` enrichable. Framework detectors know their framework's argument-passing patterns; the generic analyzer does not. Enrichment is the path to `data` edges on real projects until 1.2a.1 lands.
**Approach:** Each framework detector may upgrade `data_flow_confirmed` on the edges it owns:
- LangChain: if `create_agent(tools=[...])` and the agent's prompt template passes user input to a tool argument → `parameter_match`.
- FastMCP / MCP Python: if the tool function takes a parameter that flows to a dangerous call → `parameter_match`.
- OpenAI Agents SDK (when the detector lands): same.
**References:** ADR-0015 decision 7; Step 6 report.

### 1.2a.3 — ORM call sites in capability patterns

**Priority:** High
**Status:** Not started
**Rationale:** On FinBot, 7 of 8 sink nodes are `unknown` because they use ORM session calls (`self.db.delete(f)`, `self.db.commit()`, `session.add(...)`, `repo.*`) that are not in `CAPABILITY_WRITES_DB`. Same root cause as the Step 6 `unknown` — one level deeper. Adding these call sites to the capability patterns lets the sink analyzer fire on the common ORM layout.
**Approach:** Extend `patterns.CAPABILITY_WRITES_DB` (and `CAPABILITY_READS_DB` where relevant) with the ORM patterns: `*.db.delete`, `*.db.add`, `*.db.commit`, `*.session.delete`, `*.session.add`, `*.session.commit`, `*.session.query`. Keep the list narrow (well-known ORM method names) so the false-positive rate stays controlled (ADR-0009).
**References:** Step 7 report, limitation 1.

### 1.2a.4 — Constant collection rule

**Priority:** Medium
**Status:** Not started
**Rationale:** `subprocess.run(["docker", "info"])` classifies as `unknown`, not `literal_only`, because `_classify_argument` only treats `ast.Constant` as a literal. Constant collections (`ast.List`, `ast.Tuple`, `ast.Dict`, `ast.Set` whose elements are all constants) are equivalent to literals for the sink analyzer. FinBot's `check_docker` and `check_redis` are in this class.
**Approach:** In `_classify_argument`, treat a collection whose elements are all constants as `literal_only`. Nested collections recurse. Empty collections count as literal.
**References:** Step 7 report, limitation 2.

### 1.2a.5 — Class-body sink variant

**Priority:** Medium
**Status:** Not started
**Rationale:** dvmcp's `DVMCPClient`, `VulnerableMCPServer`, `VulnerableChatAssistant` are class-kind sinks with no `FunctionNode` body. The analyzer takes a function and cannot see them. A class-body variant would inspect `__init__` and `__call__` (and class-level statements) for dangerous calls.
**Approach:** `analyze_sink_class(cls: ast.ClassDef) -> str`. Reuse `analyze_sink_body` for `__init__` and `__call__`; merge results with the same non-linear ranking. Wire when the sink node's source resolves to a class, not a function.
**References:** Step 7 report, limitation 4.

### 1.2a.3b — repo.<verb>_<noun> handling

**Priority:** Medium
**Status:** **Done** (2026-10-20)
**Result:** Option B (trailing-* verb families). `_matches_any` recognises patterns ending in `*`; `_matches_prefix` requires the qualifier segment to match exactly and the trailing segment to start with the verb. FinBot: 10 new sink nodes, 41 new chains (all pre-existing 605 preserved), sink_data_flow 35/65/2 → 43/69/2. NMB/dvmcp/mcploitable unchanged. Over-match measured as 0 in all four corpora.
**Deferred:** a word-boundary variant of `_matches_prefix` (rejects `repo.getter` while accepting `repo.get_file`) is a one-line change if a real over-match appears. Mid-pattern stars (`repo.*_file`) are not supported.

### 1.2a.3c — db.commit()-only sinks

**Priority:** Medium
**Status:** **Done** (2026-10-20)
**Result:** A bare `db.commit()` / `session.commit()` / `repo.commit()` / `conn.commit()` in a sink body is `parameter_match`. Exception lives in `analyze_sink_body`, not in `_dangerous_calls`. `ORM_COMMIT_CALLS` in `patterns.py` (not merged into `CAPABILITY_ORM_WRITES_DB`). Edge case: parameterless sink with commit-only body stays `unknown`.
**FinBot:** 43/69/2 → 81/31/2 (38 sinks changed, all `unknown → parameter_match`, 0 shape change). NMB/dvmcp/mcploitable unchanged.
**Follow-ups:** 1.2a.3e (leading-underscore qualifier), 1.2a.3f (function resolution by `(name, line_start)`), 1.2a.3g (attribute-based dangerous calls). Class-kind sinks are 1.2a.5.

### 1.2a.3d — Test-file sinks

**Priority:** Low
**Status:** **Done** (2026-10-20)
**Result:** Voter, solo, and judge system prompts note that test-file nodes are not production entry points. `PROMPT_VERSION` v1.4.1. No new attributes, no discovery change; the LLM uses `source_file` in node attributes.
**Rationale:** 22 of FinBot's 114 sinks come from `tests/`. Excluding tests would hide real test-file vulnerabilities; a prompt note lets the LLM discount them without losing signal.

### 1.2a.3e — Leading-underscore qualifier normalization

**Priority:** Medium
**Status:** Not started
**Rationale:** `_conn.commit()`, `_db.commit()`, `_session.commit()`, `_repo.get()` — leading underscore defeats exact-qualifier matching. mcploitable uses `_conn.commit()` throughout and shows no delta on 1.2a.3c because of it. Also `cur.execute(...)` vs `cursor.execute(...)` (abbreviated qualifier).
**Approach:** Normalize a leading `_` off the qualifier segment inside `_matches_prefix` / `_matches_any`. Touches capability extraction, so it must be verified against all four corpora for over-match. Optionally add `cur.execute` / `cur.executemany` / `cur.fetchone` / `cur.fetchall` to the DB statement call patterns.
**References:** 1.2a.3c report, finding 1.

### 1.2a.3f — Function resolution by (name, line_start)

**Priority:** Low
**Status:** Not started
**Rationale:** `_index_functions` is keyed by function name, first-definition-wins. When a module defines the same name twice (`toggle_enabled` at finbot/core/data/repositories.py:664 and :1426), the body analysis uses the wrong body. 1 of 38 sink changes in 1.2a.3c is this artifact.
**Approach:** Key `_index_functions` on `(name, line_start)` or `(name, line_start, end)`, and resolve a sink/tool node to the function definition that contains its `line_start`. Touches all three analyzers (flow, sink, tool).
**References:** 1.2a.3c report, finding 2.

### 1.2a.3g — Attribute-based dangerous calls

**Priority:** Low
**Status:** Not started
**Rationale:** 10 of FinBot's remaining 31 unknown sinks call dangerous operations via attribute chains (`self.adapter.run(...)`, `self.client.post(...)`) or have no argument-level evidence at all. The capability patterns cover the well-known module-level functions (`subprocess.run`, `os.remove`, `cursor.execute`) but not class-attribute chains.
**Approach:** Deferred — needs a decision on how deep to chase attribute chains without turning the classifier into a type-inference engine. ADR-0001 (no full dataflow) suggests a conservative approach: only well-known adapter shapes (`self.<known-adapter>.<method>`), not arbitrary attribute chains.
**References:** 1.2a.3c report, finding 3.

### 1.3 — Capability-based taint, combined with non-linear chains

**Priority:** High
**Status:** Blocked by `reads_untrusted` and by 1.2a.1/1.2a.2 (needs `data` edges on real targets)
**Rationale:** Even with `flow_kind`, a chain is only exploitable if untrusted data enters somewhere and reaches a dangerous sink. Non-linear chains (chain groups, ADR-0010) require untrusted sources.
**References:** ADR-0009, ADR-0010, ADR-0012.

### 1.3a — Architecture and data-model review

**Priority:** High
**Status:** Blocked by 1.3
**Rationale:** Introducing capability-based taint and non-linear chains changes the data model further (taint sources, chain groups table usage).
**Deliverables:** Data-model review, Python model review, query layer review, normalizer review, prompt contract review, ADR updates, C4 updates, test plan.

### 1.4 — Prompt engineering: grounded evaluation

**Priority:** High
**Status:** **Partially done** (P0/P2 v1.2.0 calibration; v1.3.0 flow_kind instructions). Full version blocked by 1.3a.
**Remaining:** `uncertain` verdict with concrete criteria; explicit production-vs-test entry point distinction; prompt regression tests.

### 1.5 — Graph densification

**Priority:** Medium
**Status:** Deferred until 1.2a.1, 1.2a.2, and 1.3 design are in place.
**Rationale:** The graph is sparse (FinBot: 283 nodes, 93 edges — ratio 0.33). Adding call-graph edges without taint would worsen false positives.

### 1.6 — Lethal trifecta detection

**Priority:** High
**Status:** Blocked by 1.3 (needs `reads_untrusted`)

---

## Parsing

### Cross-file tool → sink

**Priority:** High
**Status:** Not started
**Rationale:** `has_access_to` (tool → sink) is built only within a single file. In mcploitable and dvmcp, sink nodes with no incoming edges exist because tools that reach them live in other files.
**Approach:** Extend the call graph to span imports. Populate `flow_kind` and `data_flow_confirmed` on the resulting edges (ADR-0015).

### `openai_agents.py` detector

**Priority:** High
**Status:** Not started
**Rationale:** Last detector in ADR-0011 implementation order. The OpenAI Agents SDK is a rising framework; targets using it currently produce an empty graph.

### TSAOutput cleanup

**Priority:** Low
**Status:** Not started
**Rationale:** `TSAOutput.call_edges` and `TSAOutput.precision` are unused and will never be populated from TSA.

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
**Status:** Not started. Goes with 1.3.

### Chain group prioritization (ADR-0012)

**Priority:** High
**Status:** Not started. Goes with chain groups.

### `reads_untrusted` capability

**Priority:** High
**Status:** Blocked by untrusted source detection. Goes with 1.3.

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
**Status:** Partially done (node kind only). When emitted, the edge carries `flow_kind = "structural"`.

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
**Status:** Not started. Goes with 1.3.

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
**Status:** Deferred until a target requires it.

### Multi-host (Core + Agent)

**Priority:** Icebox

### `structural_heuristics.py`

**Priority:** Cancelled. Its role was absorbed by `classification.py`.

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