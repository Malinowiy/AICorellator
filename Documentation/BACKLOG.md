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

### 2026-10-21 — Untrusted sources, part 1: node kind, capability, stage (1.3a)

The read side of ADR-0010, end-to-end in Phase 1. Chain groups are **not** part of this item; they remain the rest of 1.3.

- **Detector** (`parsing/untrusted_sources.py`): `find_untrusted_source_reads(module)` returns every call site that reads a known untrusted source, with the enclosing function/method and class. Three families, one capability: RAG corpora, MCP tool descriptions, mailbox reads (`patterns.UNTRUSTED_SOURCE_RULES` → `patterns.untrusted_source_match`, one table, one lookup).
- **Capability `reads_untrusted`** (`capability_extractor`): credited on the **reader** — the function or method whose body makes the call. It reaches the reader's class node through the existing capability fold. It is in `VALID_CAPABILITIES` and in `LETHAL_TRIFECTA_READ_CAPS`, so 1.6 is unblocked on the read side; the combination itself is still not implemented.
- **Node kind `untrusted_source`** (`graph/models.py`, `schema.sql`, `patterns.NODE_KINDS`): one node per read site per file, `id = sha256(source_type + call_target + source_file)`, `name = "<source_type>:<call_target>"`, attributes `source_type`/`call_target`/`source_file`/`line_start`/`reader_kind`/`reader`/`reader_container`. **Empty capabilities by design** — the source is a place, not an actor; `reads_untrusted` belongs on the component that reads.
- **Stage `untrusted_sources`** (`normalizer_stages/untrusted_sources.py`, 6 of 15): emits the nodes and one `has_access_to` edge per read to the reader node. Reader precedence is method/tool node → entry point (via its `handler`) → class node; a reader that resolves to no node still emits the source node, with no edge. `flow_kind` is left to `FlowKindStage` (ADR-0015 decision 3); the edge carries `data_flow_confirmed = "call_result_match"` and is therefore `data`.
- **Placement:** after `entry_points` (a plain route handler has no node of its own — the `entry_point` node is the reader) and before `detector_merge` (the merge's id remap re-points these edges, same rationale as `entry_to_sink`). Order pinned by `tests/test_normalizer_stages/test_stage_order.py` (15 stages).
- **Tests:** 713 passed. New: `tests/test_untrusted_sources.py` (detector + table consistency), `tests/test_normalizer_stages/test_untrusted_sources.py` (stage shape, reader resolution, idempotency), `tests/test_pipeline.py::test_pipeline_produces_untrusted_source_nodes` (extractor → normalize → write → read, with `flow_kind = data`), a schema case for the new kind, and capability-extractor cases for crediting, the class fold, and the sink/read disjointness.
- **Scans (fresh, post-implementation):**

  | Target | nodes | edges | chains | data_edges_only |
  |---|---|---|---|---|
  | finbot-ctf | 384 | 153 | 647 | 0 |
  | NMB | 26 | 26 | 16 | 0 |
  | dvmcp | 33 | 47 | 39 | 24 |
  | mcploitable | 76 | 46 | 19 | 6 |

- **Delta vs baseline (381/152/647, 26/26/16, 33/47/39, 74/46/19):** FinBot +3 nodes, +1 edge; mcploitable +2 nodes, 0 edges; NMB and dvmcp byte-identical. The additions are exactly the 5 source nodes and the 1 edge that has a resolvable reader; no other node or edge changed, and no `flow_kind` flipped.
- **Over-match:** 0. Five `list_tools` call sites across FinBot and mcploitable, all genuine tool-catalogue reads (`finbot/mcp/provider.py:71` — the only one with a reader node, `MCPToolProvider`; two FastAPI route helpers; mcploitable `examples/play.py:70` and `tests/test_servers.py:616`). The RAG and mailbox families fire nowhere on the four corpora. The four reads with no resolved reader are plain helper functions or test functions — the pre-existing "no node for a plain function" gap, not a new one.


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
**Status:** **Done** (2026-10-20)
**Result:** `enrichment.py` with three pure helpers. LangChain: `uses_tool` → `parameter_match` if the tool is named in the system prompt; `uses_model` → `literal_only` / `parameter_match` / `unknown`. FastMCP and MCP Python: `tool_data_flow` on tool nodes (`parameter_match` / `no_args`). Normalizer copies `tool_data_flow` at two waived sites (`provides_tool` loop, `_link_mcp_entries_to_tools`). Shape unchanged on all four corpora. Prompt version stays v1.4.1.
**Results:** dvmcp `data_edges_only` 0 → 24; mcploitable 0 → 6; NMB 0 (uses_model enriched but not on a path); FinBot 0 (path-heuristic servers, no `framework` attribute; `provides_tool` site wired but dormant, unit-tested). **Step 6 caveat closed**: `data_edges_only > 0` for the first time on a real corpus.
**Gaps (follow-ups):**
- FinBot: `_node_from_method` / `_body_attributes` would need to set `tool_data_flow` → new item 1.2a.2a.
- NMB `uses_tool`: `cross_file.py` resolver does not read the system prompt → new item 1.2a.2b.
- LangChain `Tool(...)` instances: no function node, no signature.
- Latent upsert hazard: an explicit `"unknown"` overwrites an enriched `"data"`; no duplicate ids today → new item 1.2a.2c if needed.

### 1.2a.2a — FinBot-style MCP tool enrichment

**Priority:** Medium
**Status:** Not started
**Rationale:** FinBot's MCP servers are path-heuristic (no `framework` attribute) and tool nodes are normalizer-created from TSA symbols. `tool_data_flow` never reaches them, so the `provides_tool` enrichment site is dormant. Closing the gap requires normalizer-side enrichment of tool nodes.
**Approach:** In `_node_from_method` (or `_body_attributes`), set `tool_data_flow` on normalizer-created tool nodes by analysing the TSA method body (`os.remove(param)` etc.). Reuses the same rules as the detector enrichment.
**References:** 1.2a.2 report, gap 1.

### 1.2a.2b — Cross-file `uses_tool` enrichment

**Priority:** Low
**Status:** Not started
**Rationale:** NMB's 9 `uses_tool` edges are built by `cross_file.py` (import-driven) and never read the system prompt, so they stay `control/unknown`.
**Approach:** Extend `resolve_agent_tool_edges` to read the agent's system prompt from the same file and enrich per tool named there. Same helper as LangChain.
**References:** 1.2a.2 report, gap 2.

### 1.2a.2c — Upsert enrichment precedence

**Priority:** Low
**Status:** Not started
**Rationale:** `EdgesRepository.upsert` lets a later explicit `"unknown"` overwrite an enriched `data_flow_confirmed` (only `NULL` falls back). `flow_kind` is safe (`data` wins). No duplicate edge ids today, but the hazard is latent.
**Approach:** Change `upsert` to prefer non-`"unknown"` over `"unknown"` on `data_flow_confirmed`, mirroring the `flow_kind` rule. Verify against the four corpora for shape stability.
**References:** 1.2a.2 report, gap 5.

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

### 1.2a.5 — Class-body sink analyzer

**Priority:** Medium
**Status:** **Done** (2026-10-20)
**Result:** `analyze_sink_class` inspects every direct method of the class plus class-level statements (nested classes excluded; nested functions inside methods count). Class sink = container of callable entry points; the dangerous call receives the caller's data in the method the caller invokes, not in `__init__`.
**Results:** FinBot `sink_data_flow` 81/2/31 → 101/2/11 (20 of 21 class sinks → `parameter_match`). dvmcp 4/4/4 → 6/4/2. mcploitable 4/1/3 → 5/1/2. NMB unchanged (no class sinks). Shape untouched everywhere; function-kind sinks unchanged.
**Remaining `unknown` on FinBot (11):** `VulnerableChatAssistant` (f-string/dict arguments, ADR-0001 name-based limits → 1.2a.3g); `TestLabsGuardrailConfigRepository` (no-argument `delete_config`, 1.2a.3c rule); class nodes with no dangerous call in any method or at class level.

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
**Status:** **Done** (2026-10-20)
**Rationale:** `_conn.commit()`, `_db.commit()`, `_session.commit()`, `_repo.get()` — leading underscore defeats exact-qualifier matching. mcploitable uses `_conn.commit()` throughout and shows no delta on 1.2a.3c because of it. Also `cur.execute(...)` vs `cursor.execute(...)` (abbreviated qualifier).
**Approach:** Normalize a leading `_` off the qualifier segment inside `_matches_prefix` / `_matches_any`. Touches capability extraction, so it must be verified against all four corpora for over-match. Optionally add `cur.execute` / `cur.executemany` / `cur.fetchone` / `cur.fetchall` to the DB statement call patterns.
**References:** 1.2a.3c report, finding 1.

### 1.2a.3f — Function resolution by (name, line_start)

**Priority:** Low
**Status:** **Done** (2026-10-20)
**Result:** `_index_functions` / `_index_classes` keyed by `(name, line_start)`, every definition kept. `_body_attributes` resolves to the definition containing the node's `line_start` (exact, then containing, then closest preceding, then name fallback). Detector nodes (`line_start = None`) take the name fallback, preserving old behaviour. Edge loop uses the same resolver; verified edge-neutral.
**Results:** FinBot 101/2/11 → 100/2/12. One sink changed: `toggle_enabled` @ `:1426` was reading the `:664` body (`parameter_match` artifact), now reads its own parameterless commit-only body (`unknown`, per 1.2a.3c). NMB/dvmcp/mcploitable unchanged.
**Not taken:** "closest following" rule — never fires on the four corpora; defensive only.

### 1.2a.3g — Attribute, f-string, and composite argument classification

**Priority:** Medium
**Status:** **Done** (2026-10-20)
**Result:** `_classify_argument` now recognises: `self.X` assigned from a parameter or local in the same body (fixpoint), f-strings with a parameter-derived component, and Dict/List/Tuple/Set with a parameter-derived element. Composite returns the component's own kind (`local_var_match`, `parameter_match`, ...); the sink analyzer collapses to `parameter_match`. Mixed collections are now `parameter_match` (was `unknown`) — a deliberate loosening of ADR-0015, documented in-code: the body under analysis built the collection and the parameter is inside the value. Unresolved parts still block promotion.
**Results:** dvmcp 6/4/2 → 8/4/0; mcploitable 5/1/2 → 6/1/1; FinBot, NMB unchanged (FinBot has zero `self.X` attribute hits in sink bodies, measured). Shape unchanged on all four.
**Decision 8 (not flipped):** an f-string with only a local (literal-derived) interpolation returns `local_var_match`, consistent with `os.remove(local)`. Parameter-only would be an arbitrary cutoff.
**Deferred:** `param.attr` (`file_data.filename`, `session_context.current_user.id`) is still `unknown`. The 5 arguments in FinBot `create_vendor_file` are the visible false-negative family. New item 1.2a.3j.

### 1.2a.3h — Cursor fetch reads

**Priority:** Low
**Status:** **Done** (2026-10-20)
**Result:** `cur.fetchone` / `fetchall` / `fetchmany` and `cursor.*` added to `CAPABILITY_ORM_READS_DB`. Fetch-only bodies are not sinks (`reads_db` is not in `SINK_CAPABILITIES`); a sink with `execute('INSERT ...')` + `fetchall()` now carries both capabilities.
**Results:** dvmcp 7 → 11 `reads_db` nodes; mcploitable 3 → 4. FinBot, NMB unchanged. Over-match 0 (19 genuine call sites, 0 false positives).
**Not taken:** `c.fetchall()` (NMB single-letter qualifier — too broad); chained calls (`connection.execute(...).fetchone()`, receiver is a Call — out of scope).

### 1.2a.3i — Adapter attribute capability patterns

**Priority:** Low
**Status:** **Done** (2026-10-20)
**Result:** Two-segment adapter patterns added: `CAPABILITY_EXECUTES_CODE` (`shell.run`, `adapter.execute`, `adapter.spawn`, ...), `CAPABILITY_HTTP_EGRESS` (`client.post`, `client.put`, `client.patch`, `client.delete`, `adapter.post`, `session.post`, `session.request`, ...), `CAPABILITY_CALLS_LLM` (`llm.invoke`, `llm.chat`, `llm.complete`, `llm.generate`, `model.invoke`, `model.chat`, `model.complete`). Bare verbs, `self.run` / `self.post` not added. `client.get` deliberately not added (GET is a read; `requests.get` / `httpx.get` cover the idiom; pinned in 3 tests). Shell adapters not put in `CAPABILITY_EXECUTES_SHELL_EXPLICIT` (shell semantics unknowable; `executes_code` already sink).
**Results:** FinBot 379/151/646 → 381/152/647, `sink_data_flow` 101/2/11 → 104/2/11 (`GuardrailHookService`, `invoke`, `push_event`). NMB / dvmcp / mcploitable unchanged. Over-match 0 (2 `client.post` call sites, both true positives). Kind flip `GuardrailHookService`: `guardrail → sink` (ADR-0009 capability override; body POSTs to webhook; `guards` edge emission is 1.2a.5x).

### 1.2a.3j — Parameter attribute arguments

**Priority:** Medium
**Status:** **Done** (2026-10-20)
**Result:** `param.X` and `local.X` (attribute on a parameter or body-local) → `parameter_match`, any depth as long as the base name is a parameter or local. Module-level base (`obj.X`) stays `unknown`. `local.X` is `parameter_match` (consistent with `self.q = <local>` from 1.2a.3g; value-blind over-approximation for literal-derived locals, ADR-0001). Opt-in gate `parameter_attributes=False` by default; only `analyze_sink_body` opts in — `_classify_argument` is shared with the edge analyzer, and the ungated variant would change edge values. Measured ungated: byte-identical on all four corpora today, so the gate costs nothing empirically.
**Results:** FinBot 100/2/12 → 101/2/11 (`create_vendor_file` 5 args → `parameter_match`). mcploitable 6/1/1 → 7/1/0 (`main`: `open(args.log, 'w')` with `args` from `ap.parse_args()`). NMB, dvmcp unchanged. Over-match 0 (12 args newly classified, 0 module-level base).
**Not taken:** `self.q.name` (base is `self`, not the derived attribute — third-level indirection); `fetch().path` (base is a Call — no call tracking, ADR-0001).

### 1.2a.3k — Dev script discovery

**Priority:** Low
**Status:** Not started
**Rationale:** `scripts/test_websocket.py` (FinBot) contributes a sink `push_event` and a chain edge. This is not a false positive — the script really does POST — but it is low-value noise from a non-production directory. 1.2a.3d covers `tests/` via a prompt note; `scripts/` is not test, but dev tooling.
**Approach:** Either an `is_dev_code: true` node attribute (mirroring the test-file note) or a discovery exclusion for `scripts/`. The attribute is the softer option; the exclusion is cleaner if scripts are never production. Decide on the corpus evidence.
**References:** 1.2a.3i report, review item 3.

### 1.3 — Capability-based taint, combined with non-linear chains

**Priority:** High
**Status:** **Partially unblocked.** The read side of `reads_untrusted` landed in 1.3a (2026-10-21): the capability, the `untrusted_source` node kind, and the detector + normalizer stage. The chain-group half (write side, `find_chain_groups`, prioritization) is not started, and 1.2a.1/1.2a.2 remain open for real `data` edges.
**Rationale:** Even with `flow_kind`, a chain is only exploitable if untrusted data enters somewhere and reaches a dangerous sink. Non-linear chains (chain groups, ADR-0010) require untrusted sources.
**References:** ADR-0009, ADR-0010, ADR-0012.

### 1.3a — Architecture and data-model review

**Priority:** High
**Status:** Blocked by 1.3
**Rationale:** Introducing capability-based taint and non-linear chains changes the data model further (taint sources, chain groups table usage).
**Deliverables:** Data-model review, Python model review, query layer review, normalizer review, prompt contract review, ADR updates, C4 updates, test plan.

### 1.3c.2 — Any-node semantics for impact sink capabilities

**Priority:** Low
**Status:** Not started
**Rationale:** `_score_read_sinks` walks only the last node of a read chain; `_score_read_writes_db` credits any node. The three impact rows are inconsistent. On FinBot the inconsistency is invisible (0 mid-chain hits across 2920 groups), but the code paths differ and the ADR's "reading chain reaches" wording is ambiguous.
**Approach:** Either widen `dangerous_sink` / `exfil_or_write` to any-node (matches `writes_db`), or narrow `writes_db` to terminal-only (matches the other two). Prefer widening: a dangerous call mid-chain is still dangerous. No-op on the four corpora; correctness fix. Also clarify the ADR-0012 table wording.
**References:** 1.3c.1 report, §4.

### 1.3d.1 — Corpus with lethal-trifecta node

**Priority:** Low
**Status:** Not started
**Rationale:** The `lethal_trifecta` attribute is `False` on all four validation corpora; the scorer's attribute path is exercised by unit tests only. A corpus with a node that carries `reads_untrusted` + secrets + egress would distinguish "read the flag" from "walk the capabilities" end to end.
**Approach:** Find or build a target where one node (e.g. a RAG retriever with secrets and an outbound HTTP client) satisfies the combination. Run 1.3c.1 + 1.3d on it; confirm the +5 fires from the attribute, not the fallback.
**References:** 1.3d report, observation.

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
**Status:** Not started. Its input on the read side now exists (`reads_untrusted`, 1.3a, 2026-10-21); the combination code and the node-level flag do not.

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
**Status:** **Done** (1.3a, 2026-10-21) — credited on the reader from three rule families; in `VALID_CAPABILITIES` and `LETHAL_TRIFECTA_READ_CAPS`. The lethal-trifecta combination itself is 1.6.

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
**Status:** **Done** (1.3a, 2026-10-21) — one node per read site per file, empty capabilities by design, `has_access_to` edge to the reader. The write side and chain groups are not part of this.

### Untrusted source detection

**Priority:** High
**Status:** **Done** (1.3a, 2026-10-21) — `parsing/untrusted_sources.py` over `patterns.UNTRUSTED_SOURCE_RULES`; stage 6 of 15. Deliberately not a source in 1.3a: web scrapes (already `http_egress`), user uploads (no call shape), `session.list_tools` (ORM overlap).

### `find_chain_groups` operation

**Priority:** High
**Status:** Not started. Next in 1.3 — needs a writer side for the source nodes, which 1.3a does not build.

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