# AICorellator — Backlog

Single registry of deferred, in-progress, and planned work. Complements the ADRs (which record *decisions*) with a working list of *what to do next*.

**Last updated:** 2026-10-22

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

### 2026-10-22 — Chain groups, end to end (1.3b, 1.3c, 1.3c.1, 1.3d, 1.3e)

The chain-group half of ADR-0010, plus ADR-0012 prioritization and ADR-0009 lethal-trifecta enrichment.

- **1.3a.1** — writer-side edges (`writes_to`, control/unknown). 1.3a built `source → reader`; 1.3a.1 added `writer → source`. Zero on the four corpora without a writer; the mechanism is in place.
- **1.3a.1.2** — dynamic MCP invocation + literal factory paths. `agent` with `reads_untrusted` on a catalogue gains `uses_tool` to every tool of every server that writes that catalogue; `_SERVER_INTROSPECTORS` literal factory paths anchor the writer.
- **1.3a.1.3** — project-level literal factory anchor. A server named by any factory-path literal anywhere in the project writes any catalogue read anywhere in the project. FinBot: 15 `writes_to`, 38 `uses_tool`, 1000 groups (cap) / 2920 uncapped.
- **1.3b** — `find_chain_groups`: write chains (`entry → … → source`) × read chains (`source → … → sink`) around every `untrusted_source`. Pure enumerator, no `chain_groups` writes, no writer→source producer.
- **1.3c** — `prioritize_chain_groups`: ADR-0012 deterministic scoring (lethal trifecta +5, `executes_*` +4, `writes_filesystem` / `http_egress` / `writes_db` +3, user-writable source +2, user-readable source +1, short chain +1, stale −2), ranking, top-N selection, storage in `chain_groups`. New `ChainGroupsRepository`.
- **1.3c.1** — ADR-0012 revision: `writes_db` weight +3 (parallel `writes_filesystem`), `min_score_to_evaluate` 5 → 3. FinBot: 1000 groups / 2920 uncapped, 100 selected (score 4, priority low), 100 rows written.
- **1.3d** — `lethal_trifecta` node attribute (stage 18, last). `True` / `False` on every node; recomputed, not guarded; explicit `False` so absence means "not computed". Zero flagged on the four corpora (honest, mechanism ready). `patterns.lethal_trifecta_caps` is the one definition; the scorer reads the attribute with a capability fallback for pre-1.3d graphs.
- **1.3e** — `evaluate_chain_groups`: LLM ensemble extension of Phase 3 to chain groups. Prompt v1.5.0 (`GROUP_SYSTEM_PROMPT`, `build_group_prompt`: pair framing, scoring outside `[UNTRUSTED_DATA]` as a hint, `lethal_trifecta` inside). Layer 3 validation (every edge exists, every cited edge in the group's own set, every hop resolved). `chain_groups.verdict` / `confidence` / `reasoning` / `validated` persisted. Report section `## Chain groups (Phase 3)`; `chain_groups.json` in the bundle; `run_eval.py --groups --max-groups`.
- **Tests:** 905 passed (up from 344 at the start of 1.3).
- **FinBot (real LLM, `qwen3.6-35b-a3b` in fast role, config outside the repo):** 5 evaluated, 4 validated, 5 not_exploitable, 0 exploitable, ~5 min/group. One group rejected by Layer 3 (edge outside the pair) — the evidence gate firing on genuine model output. Model reasoning: not exploitable because `mcp_tool_description` is user-readable only.
- **FinBot (mock, 100-group pass):** 100 evaluated, 100 validated, 0.07 s. `_chain_edges` reproduced all 100 chain ids with zero warnings.

### 2026-10-21 — Untrusted sources, part 1: node kind, capability, stage (1.3a)

The read side of ADR-0010, end-to-end in Phase 1. Chain groups are **not** part of this item; they remained the rest of 1.3 and landed on 2026-10-22.

- **Detector** (`parsing/untrusted_sources.py`): `find_untrusted_source_reads(module)` returns every call site that reads a known untrusted source, with the enclosing function/method and class. Three families, one capability: RAG corpora, MCP tool descriptions, mailbox reads (`patterns.UNTRUSTED_SOURCE_RULES` → `patterns.untrusted_source_match`).
- **Capability `reads_untrusted`** (`capability_extractor`): credited on the **reader** — the function or method whose body makes the call. In `VALID_CAPABILITIES` and in `LETHAL_TRIFECTA_READ_CAPS`.
- **Node kind `untrusted_source`** (`graph/models.py`, `schema.sql`, `patterns.NODE_KINDS`): one node per read site per file, `id = sha256(source_type + call_target + source_file)`, `name = "<source_type>:<call_target>"`. **Empty capabilities by design.**
- **Stage `untrusted_sources`** (`normalizer_stages/untrusted_sources.py`, 6 of 18): emits the nodes and one `has_access_to` edge per read to the reader node. `data_flow_confirmed = "call_result_match"`, therefore `data`.
- **Placement:** after `entry_points` and before `detector_merge`. Order pinned by `tests/test_normalizer_stages/test_stage_order.py`.
- **Scans:** FinBot 384/153/647, NMB 26/26/16, dvmcp 33/47/39, mcploitable 76/46/19. Five source nodes, one edge with a resolvable reader.

### 2026-10-20 — ADR-0015 implementation (backlog item 1.2a)

- **ADR-0015 accepted.** `flow_kind` on every edge (control | data | structural). `data_flow_confirmed` on `has_access_to` (six values). Structural edges (guards) excluded from `find_chains`. `provides_tool` is **control**. `data_edges_only` parameter on `find_chains`. Prompt v1.3.0 receives `flow_kind` and `data_flow_confirmed`.
- **Flow Analyzer** (`parsing/flow/`): `FlowDecision` (kind, evidence, source), `flow_kind_for`, name-based argument matching, non-linear ranking, `literal_only` requires all literals.
- **Normalizer:** per-scan module cache; every edge carries `flow_kind`; `id_remap` preserves flow fields.
- **Known limitation:** `data_flow_confirmed = "unknown"` on all edges across all four corpora. `data_edges_only = 0` everywhere. Two follow-ups: 1.2a.1 and 1.2a.2 (1.2a.2 done 2026-10-20).
- **TSA fix:** `tsa_worker.py` pins `uvx --python 3.14` (tree-sitter-analyzer 2.2.0 → cachebox 5.2.3 → PyO3 0.27.1 does not support Python 3.15).
- **Tests:** 344 passed at the time; the Flow Analyzer split and the normalizer stage pipeline later raised this to 687 before 1.3.

### 2026-10-20 — ADR-0015 design and documentation

- **ADR-0015 written and accepted** (design).
- **ADR-0009, ADR-0013, ADR-0007 updated** to reference `flow_kind`.
- **C4 L2, L3 Phase 1, L3 Phase 3 updated** — Flow Analyzer component; `flow_kind` in normalizer pipeline and prompt contract.

### 2026-10-14 — TSA investigation (item 1.1)

- **TSA `--structure --format json` provides symbols only.** No call edges, no type information, no argument information. `TSAOutput.call_edges` will never be populated from TSA.

### 2026-10-14 — P0/P2 review fixes (commit 2263fa7)

- `exploitable` must be a real JSON boolean; non-boolean rejected as invalid.
- Per-judge `temperature`/`max_tokens` overrides honored.
- Node/edge serialization includes capabilities and precision; evidence validated against the candidate chain.
- `PROMPT_VERSION` v1.1.0 → v1.2.0.

### Earlier

- CLI and Streamlit entry points (commit fc266b0).
- Project backlog created (commit 8c78d15).
- README user manual (commit 49f0cd9).
- PolyForm Noncommercial 1.0.0 license (commit dd6da36).

### Deferred from the P0/P2 review

- **Judge determinism.** The judge runs with `temperature=0.6`. Deterministic verdicts require `0.0`. Priority: Low.
- **Raw voter response persistence.** ADR-0005 asks for provenance; `evaluator.py` drops `raw` before persistence. Priority: Medium.

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
**Known limitation:** `data_flow_confirmed = "unknown"` on all edges across all four corpora. `data_edges_only = 0`. Follow-ups: 1.2a.1 and 1.2a.2 (1.2a.2 done).

### 1.2a.1 — Analyzer asks the right question

**Priority:** High
**Status:** Not started
**Rationale:** The generic Flow Analyzer asks "does this tool call a function named like the sink?" On real code, the sink node is the **definition** of the dangerous operation, not a call site. The interesting question for FinBot-style layouts is: "how does data reach the dangerous call **inside the sink method's own body**?" (`delete_file(file_id)` → `self.db.delete(f)` → `parameter_match`).
**Approach:** Add a second analyzer pass that inspects the body of each sink function and asks whether tool arguments reach the dangerous call inside it. Keep the existing `has_access_to` analyzer as-is.
**References:** Step 6 report; ADR-0015 Deferred.

### 1.2a.2 — Detector enrichment of `data_flow_confirmed`

**Priority:** High
**Status:** **Done** (2026-10-20)
**Result:** `enrichment.py` with three pure helpers. LangChain: `uses_tool` → `parameter_match` if the tool is named in the system prompt; `uses_model` → `literal_only` / `parameter_match` / `unknown`. FastMCP and MCP Python: `tool_data_flow` on tool nodes. Normalizer copies `tool_data_flow` at two waived sites.
**Results:** dvmcp `data_edges_only` 0 → 24; mcploitable 0 → 6. **Step 6 caveat closed.**
**Gaps (follow-ups):** 1.2a.2a, 1.2a.2b, 1.2a.2c.

### 1.2a.2a — FinBot-style MCP tool enrichment

**Priority:** Medium
**Status:** Not started
**Rationale:** FinBot's MCP servers are path-heuristic (no `framework` attribute); `tool_data_flow` never reaches them. The `provides_tool` enrichment site is wired but dormant.
**Approach:** In `_node_from_method` (or `_body_attributes`), set `tool_data_flow` on normalizer-created tool nodes by analysing the TSA method body. Reuses the detector enrichment rules.
**References:** 1.2a.2 report, gap 1.

### 1.2a.2b — Cross-file `uses_tool` enrichment

**Priority:** Low
**Status:** Not started
**Rationale:** NMB's 9 `uses_tool` edges are built by `cross_file.py` (import-driven) and never read the system prompt, so they stay `control/unknown`.
**Approach:** Extend `resolve_agent_tool_edges` to read the agent's system prompt from the same file and enrich per tool named there.
**References:** 1.2a.2 report, gap 2.

### 1.2a.2c — Upsert enrichment precedence

**Priority:** Low
**Status:** Not started
**Rationale:** `EdgesRepository.upsert` lets a later explicit `"unknown"` overwrite an enriched `data_flow_confirmed`. `flow_kind` is safe (`data` wins).
**Approach:** Change `upsert` to prefer non-`"unknown"` over `"unknown"` on `data_flow_confirmed`, mirroring the `flow_kind` rule.
**References:** 1.2a.2 report, gap 5.

### 1.2a.3 — ORM call sites in capability patterns

**Priority:** High
**Status:** **Done** — covered by 1.2a.3b/c/e/f/g (see below).

### 1.2a.4 — Constant collection rule

**Priority:** Medium
**Status:** **Done** (2026-10-20) — see 1.2a.3g for the composite classifier; the constant-collection case is part of the same change.

### 1.2a.5 — Class-body sink analyzer

**Priority:** Medium
**Status:** **Done** (2026-10-20)
**Result:** `analyze_sink_class` inspects every direct method plus class-level statements. FinBot `sink_data_flow` 81/2/31 → 101/2/11. dvmcp 4/4/4 → 6/4/2. mcploitable 4/1/3 → 5/1/2.

### 1.2a.3b — repo.<verb>_<noun> handling

**Priority:** Medium
**Status:** **Done** (2026-10-20) — see the previous report for numbers.

### 1.2a.3c — db.commit()-only sinks

**Priority:** Medium
**Status:** **Done** (2026-10-20) — see the previous report for numbers.

### 1.2a.3d — Test-file sinks

**Priority:** Low
**Status:** **Done** (2026-10-20) — prompt note in voter, solo, judge.

### 1.2a.3e — Leading-underscore qualifier normalization

**Priority:** Medium
**Status:** **Done** (2026-10-20) — qualifier segments strip leading underscores; `cur.execute` / `cur.executemany` added.

### 1.2a.3f — Function resolution by (name, line_start)

**Priority:** Low
**Status:** **Done** (2026-10-20) — `_index_functions` / `_index_classes` keyed by `(name, line_start)`.

### 1.2a.3g — Attribute, f-string, and composite argument classification

**Priority:** Medium
**Status:** **Done** (2026-10-20) — see the previous report.

### 1.2a.3h — Cursor fetch reads

**Priority:** Low
**Status:** **Done** (2026-10-20) — `cur.fetchone` / `fetchall` / `fetchmany` and `cursor.*` in `CAPABILITY_ORM_READS_DB`.

### 1.2a.3i — Adapter attribute capability patterns

**Priority:** Low
**Status:** **Done** (2026-10-20) — `client.post`, `llm.invoke`, `adapter.execute`, etc. `client.get` deliberately not added.

### 1.2a.3j — Parameter attribute arguments

**Priority:** Medium
**Status:** **Done** (2026-10-20) — `param.X` / `local.X` → `parameter_match`. Opt-in gate `parameter_attributes=False` by default; only `analyze_sink_body` opts in.

### 1.2a.3k — Dev script discovery

**Priority:** Low
**Status:** Not started
**Rationale:** `scripts/test_websocket.py` (FinBot) contributes a sink `push_event` and a chain edge — low-value noise.
**Approach:** `is_dev_code: true` node attribute, or discovery exclusion for `scripts/`.

### 1.3 — Capability-based taint, combined with non-linear chains

**Priority:** High
**Status:** **Done** (2026-10-22). The whole chain-group half landed: 1.3a (read side), 1.3a.1/1.3a.1.2/1.3a.1.3 (write side + dynamic invocation), 1.3b (enumerator), 1.3c/1.3c.1 (prioritization), 1.3d (lethal trifecta attribute), 1.3e (LLM evaluation).
**Result:** FinBot: 1000 groups (cap) / 2920 uncapped, 100 selected (score 4, priority low), 5 evaluated by real LLM (4 validated, 5 not exploitable), 100 evaluated by mock (100 validated). Report section renders. `data_edges_only` still 0 — 1.2a.1 is the missing half for real `data` edges.
**Follow-ups:** 1.3c.2 (any-node semantics), 1.3d.1 (corpus with lethal-trifecta node), 1.3e.1 (provenance columns), 1.3e.2 (linear prompt v1.6.0).

### 1.3a — Architecture and data-model review

**Priority:** High
**Status:** Not started. Its original scope (data-model review for taint sources) was absorbed by the incremental 1.3a/1.3a.1/1.3b design passes; revisit if a structural change to `chain_groups` or `untrusted_source` is needed.

### 1.3c.2 — Any-node semantics for impact sink capabilities

**Priority:** Low
**Status:** Not started
**Rationale:** `_score_read_sinks` walks only the last node of a read chain; `_score_read_writes_db` credits any node. Inconsistent on paper; no-op on the four corpora (0 mid-chain hits across 2920 FinBot groups).
**Approach:** Widen `dangerous_sink` / `exfil_or_write` to any-node, or narrow `writes_db` to terminal-only. Prefer widening. Clarify the ADR-0012 table wording.
**References:** 1.3c.1 report, §4.

### 1.3d.1 — Corpus with lethal-trifecta node

**Priority:** Low
**Status:** Not started
**Rationale:** The `lethal_trifecta` attribute is `False` on all four corpora; the scorer's attribute path is exercised by unit tests only. A corpus with a node that carries `reads_untrusted` + secrets + egress would distinguish the attribute path from the fallback end to end.
**Approach:** Find or build a target where one node satisfies the combination. Confirm the +5 fires from the attribute, not the fallback.
**References:** 1.3d report.

### 1.3e.1 — Chain group evaluation follow-ups

**Priority:** Medium
**Status:** Not started
**Rationale:** 1.3e landed the evaluator, prompt v1.5.0, Layer 3 validation, report section, and storage. Five small gaps surfaced; none blocks usage.
**Sub-items:**

1. **`chain_groups.prompt_version` column.** A group verdict cannot be traced to prompt v1.5.0 in storage, unlike `chains.prompt_version`. Schema change; DB recreated per scan, no migration.
2. **`chain_groups.voter_outputs` column.** Per-voter provenance is logged, not stored. Schema change.
3. **`chain_groups.lethal_trifecta` writer.** 1.3d writes the per-node attribute; nothing projects it onto the group row. One line in `_evaluate_one_group`: `group.lethal_trifecta = any(read_chain_node.attributes.get("lethal_trifecta"))`. No schema change.
4. **Group `name` disambiguation.** `f"{source_name}:{w}x{r}"` collides for groups on the same source with the same hop counts. Append a short id suffix.
5. **Pre-1.3d graph fallback for the prompt flag.** `_lethal_trifecta_flag` falls back to `patterns.lethal_trifecta_caps(node.capabilities) != ()` when the attribute is absent.

**References:** 1.3e report, "Flagged, not fixed".

### 1.3e.2 — Linear-chain prompt v1.6.0 (open question)

**Priority:** Low
**Status:** Not started
**Rationale:** The group prompt (v1.5.0) and the linear prompt (v1.4.1) diverge. The group prompt frames our scoring as "a hint, not evidence"; the linear prompt does not. If the linear prompt gains a scoring section, the divergence is fine; otherwise two prompts encode two different instruction sets.
**Approach:** Compare line by line; either lift the group's "OUR SCORING" framing into the linear prompt (v1.6.0) or document the divergence.
**References:** 1.3e report, decision 2.

### 1.4 — Prompt engineering: grounded evaluation

**Priority:** High
**Status:** **Partially done** (P0/P2 v1.2.0 calibration; v1.3.0 flow_kind instructions; v1.5.0 group prompt). Full version blocked by 1.2a.1.
**Remaining:** `uncertain` verdict with concrete criteria; explicit production-vs-test entry point distinction; prompt regression tests.

### 1.5 — Graph densification

**Priority:** Medium
**Status:** Deferred until 1.2a.1 and 1.3 are in place.
**Rationale:** The graph is sparse (FinBot: 283 nodes, 93 edges — ratio 0.33). Adding call-graph edges without taint would worsen false positives.

### 1.6 — Lethal trifecta detection

**Priority:** High
**Status:** **Done** (1.3d, 2026-10-22) — node attribute `lethal_trifecta`. Zero flagged on the four corpora; mechanism ready. 1.3d.1 is the corpus unblocker.

---

## Parsing

### Cross-file tool → sink

**Priority:** High
**Status:** Not started
**Rationale:** `has_access_to` (tool → sink) is built only within a single file.
**Approach:** Extend the call graph to span imports. Populate `flow_kind` and `data_flow_confirmed`.

### `openai_agents.py` detector

**Priority:** High
**Status:** Not started
**Rationale:** Last detector in ADR-0011 implementation order.

### TSAOutput cleanup

**Priority:** Low
**Status:** Not started

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
**Status:** **Done** (1.3b + 1.3e, 2026-10-22). `find_chain_groups` enumerator; `evaluate_chain_groups` LLM evaluation with Layer 3 validation; `chain_groups` rows carry verdict/confidence/reasoning/validated; report section `## Chain groups (Phase 3)`.

### Chain group prioritization (ADR-0012)

**Priority:** High
**Status:** **Done** (1.3c + 1.3c.1, 2026-10-22). Deterministic scoring, ranking, top-N, storage. FinBot: 1000 groups / 2920 uncapped, 100 selected.

### `reads_untrusted` capability

**Priority:** High
**Status:** **Done** (1.3a, 2026-10-21).

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
**Status:** Partially done (node kind only). Edge carries `flow_kind = "structural"`.

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
**Status:** **Done** (1.3a, 2026-10-21).

### Untrusted source detection

**Priority:** High
**Status:** **Done** (1.3a, 2026-10-21).

### `find_chain_groups` operation

**Priority:** High
**Status:** **Done** (1.3b, 2026-10-22). Enumerator live; 1000 groups on FinBot (cap) / 2920 uncapped. Non-linear chains through an untrusted source.

### `writes_to` writer edges

**Priority:** High
**Status:** **Done** (1.3a.1 + 1.3a.1.2 + 1.3a.1.3, 2026-10-22). `writes_to` edge kind; three anchors (parent_server, same file, same directory, literal factory path); project-level literal widening. FinBot: 15 `writes_to`, 38 `uses_tool`.

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