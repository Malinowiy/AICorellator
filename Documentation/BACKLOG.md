# AICorellator — Backlog

Single registry of deferred, in-progress, and planned work. Complements the ADRs (which record *decisions*) with a working list of *what to do next*.

**Last updated:** 2026-10-23
**Revision:** restructured around *quality over coverage*. See §0 for the reasoning and the Top-10 view.

---

## 0. Direction and priorities

### 0.1. The diagnosis this revision is built on

Coverage grew faster than the quality of what is covered. Three facts from the 2026-10-22 state force the reprioritization:

1. **`data_flow_confirmed = "unknown"` on 100% of edges across all four corpora.** `data_edges_only = 0` everywhere. ADR-0015's central promise — that the LLM can distinguish "X calls Y" from "data from X reaches Y" — is **not realized on real data**. 1.2a.1 is the fix; 1.2a.2 was tried and did not close it.
2. **`lethal_trifecta` is `False` on every node in every corpus.** The strongest scoring signal in ADR-0012 (+5, more than any other single component) has **never fired on real data**. The mechanism is tested on synthetic nodes only.
3. **Layer 3 validates existence, not semantics.** `is_chain_valid` checks that nodes and edges exist; it does not check direction, order, or roles. A voter that returns `exploitable: true` with `evidence: []` still produces `validated: true`.

Plus two coverage holes that were invisible in the old backlog:

- **Write-side detection works only for `mcp_tool_description`.** `rag_corpus`, `email_inbox`, and `user_upload` have no writer rule. Half of ADR-0010 does not function.
- **`cross-file tool → sink` is not implemented.** `has_access_to` is same-file only, so flow analysis (1.2a.1, 1.2a.3g, `data_edges_only`) applies to a **minority** of real chains.

### 0.2. The direction this revision sets

**Quality over coverage.** No new detectors, no new source families, no new eval modes until the existing signals are trustworthy on real corpora. Concretely:

- **Phase A (now):** make `data` edges real (1.2a.1 + cross-file `tool → sink`), make Layer 3 semantic, find a corpus where `lethal_trifecta` fires.
- **Phase B (next):** close the write-side gap (`rag_corpus`, `email_inbox`, `user_upload`), fix `untrusted_source` identity, stabilize the judge.
- **Phase C (then):** guardrails become load-bearing, `openai_agents` detector lands, 1.4 prompt engineering resumes on a graph where the data exists.

**What is explicitly *not* planned in this cycle:** TUI (demoted to Icebox), new eval modes, new source families, Phase 2 runtime (kept in Icebox, made honest about being Icebox), standalone executables (kept Icebox), SCIP (kept Icebox).

### 0.3. Top 10 by impact

| # | Item | Priority | Why it is here |
|---|---|---|---|
| 1 | **1.2a.1** — Analyzer asks the right question | **Critical** | `data_edges_only = 0` on all corpora; ADR-0015 is marketing without it |
| 2 | **ADR-0016** — `data_flow_confirmed` quality | **Critical** | Plan B for 1.2a.1; if 1.2a.1 does not close the gap, the semantics must change |
| 3 | **Cross-file tool → sink** | **High** | Without it, 1.2a.1 applies to a minority of chains |
| 4 | **Layer 3 semantic validation** | **High** | `validated: true` currently does not mean "correct" |
| 5 | **1.3d.1** — Corpus with lethal-trifecta node | **High** | Strongest signal (+5) never fires; must be verified before trusting 1.3c/1.3e |
| 6 | **Guardrails affect enumeration** | **High** | Guardrails are nodes but not blockers; 1.4 will otherwise tell the LLM about protections that do not exist |
| 7 | **Write-side for rag_corpus / email_inbox / user_upload** | **Medium** | Half of ADR-0010 does not function |
| 8 | **Evidence presence affects validation** | **Medium** | Empty evidence currently produces `validated: true` |
| 9 | **`untrusted_source` identity review** | **Medium** | Multi-file corpora are silently split; single-file reads are silently merged |
| 10 | **Judge determinism** | **Medium** | Verdicts stored in `chain_groups` are not reproducible |

Everything below is the same content, organized by area, with the same priority/status conventions.

---

## Priority legend

- **Critical** — blocks MVP or a core promise. Nothing was in this category before this revision; two items are now.
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

### 2026-10-22 — Chain groups, end to end (1.3b, 1.3c, 1.3c.1, 1.3d, 1.3e)

The chain-group half of ADR-0010, plus ADR-0012 prioritization and ADR-0009 lethal-trifecta enrichment.

- **1.3a.1** — writer-side edges (`writes_to`, control/unknown).
- **1.3a.1.2** — dynamic MCP invocation + literal factory paths.
- **1.3a.1.3** — project-level literal factory anchor. FinBot: 15 `writes_to`, 38 `uses_tool`, 1000 groups (cap) / 2920 uncapped.
- **1.3b** — `find_chain_groups`: write chains × read chains around every `untrusted_source`.
- **1.3c** — `prioritize_chain_groups`: ADR-0012 deterministic scoring, ranking, top-N, storage.
- **1.3c.1** — ADR-0012 revision: `writes_db` +3, `min_score_to_evaluate` 5 → 3.
- **1.3d** — `lethal_trifecta` node attribute (stage 18). **Zero flagged on the four corpora** — mechanism ready, corpus unblocker is 1.3d.1.
- **1.3e** — `evaluate_chain_groups`: LLM ensemble, prompt v1.5.0, Layer 3 validation, report section, `chain_groups.json` in bundle.
- **Tests:** 905 passed.
- **FinBot (real LLM, `qwen3.6-35b-a3b` in fast role):** 5 evaluated, 4 validated, 5 `not_exploitable`, 0 `exploitable`. **The honest result** — no indirect prompt injection in FinBot; the model correctly said so.
- **FinBot (mock, 100-group pass):** 100 evaluated, 100 validated, 0.07 s.

### 2026-10-21 — Untrusted sources, part 1: node kind, capability, stage (1.3a)

- **Detector** (`parsing/untrusted_sources.py`): three families, one capability.
- **Capability `reads_untrusted`**: credited on the reader, not on the source.
- **Node kind `untrusted_source`**: one per read site per file.
- **Stage `untrusted_sources`**: emits nodes + `source → reader` edges.
- **Scans:** FinBot 384/153/647, NMB 26/26/16, dvmcp 33/47/39, mcploitable 76/46/19.

### 2026-10-20 — ADR-0015 implementation (backlog item 1.2a)

- ADR-0015 accepted. `flow_kind` on every edge (control | data | structural). `data_flow_confirmed` on `has_access_to`.
- Structural edges excluded from `find_chains`. `provides_tool` is **control**.
- **Known limitation:** `data_flow_confirmed = "unknown"` on all edges across all four corpora. `data_edges_only = 0` everywhere.

### 2026-10-20 — ADR-0015 design and documentation

- ADR-0015 written and accepted.
- ADR-0009, ADR-0013, ADR-0007 updated to reference `flow_kind`.
- C4 L2, L3 Phase 1, L3 Phase 3 updated.

### 2026-10-14 — TSA investigation (item 1.1)

- TSA `--structure --format json` provides symbols only. No call edges, no type information, no argument information.

### 2026-10-14 — P0/P2 review fixes (commit 2263fa7)

- `exploitable` must be a real JSON boolean; non-boolean rejected.
- Per-judge `temperature`/`max_tokens` overrides honored.
- Node/edge serialization includes capabilities and precision; evidence validated against the candidate chain.
- `PROMPT_VERSION` v1.1.0 → v1.2.0.

### Earlier

- CLI and Streamlit entry points (commit fc266b0).
- Project backlog created (commit 8c78d15).
- README user manual (commit 49f0cd9).
- PolyForm Noncommercial 1.0.0 license (commit dd6da36).

---

## Phase A — Make the existing signals trustworthy

**The core promise of this phase:** after Phase A, `data_edges_only` returns more than zero on at least one real corpus, Layer 3 rejects at least one semantically wrong chain that `is_chain_valid` currently accepts, and `lethal_trifecta` fires on at least one real node.

### 1.2a.1 — Analyzer asks the right question

**Priority:** **Critical** (was High)
**Status:** Not started
**Rationale:** The generic Flow Analyzer asks "does this tool call a function named like the sink?". On real code, the sink node is the **definition** of the dangerous operation, not a call site. The interesting question for FinBot-style layouts is: "how does data reach the dangerous call **inside the sink method's own body**?" (`delete_file(file_id)` → `self.db.delete(f)` → `parameter_match`).

Without this, **`data_edges_only = 0` on all four corpora** — the single most quoted limitation in the project. ADR-0015's flow semantics exist in the schema, the prompt, and the tests, but not in the graph.

**Approach:** Add a second analyzer pass that inspects the body of each sink function and asks whether tool arguments reach the dangerous call inside it. Keep the existing `has_access_to` analyzer as-is.

**Why Critical, not High:** every downstream feature (2.4 chain prioritization, 1.3e group evaluation, 1.4 prompt engineering) depends on `data` edges being real. If this does not close the gap, ADR-0016 must.

**References:** Step 6 report; ADR-0015 Deferred.

### ADR-0016 — `data_flow_confirmed` quality (plan B)

**Priority:** **Critical** (new)
**Status:** Not started
**Rationale:** 1.2a.1 may not close the gap. Two paths failed before it (1.2a.2 tried detector enrichment; the corpora showed `data_flow_confirmed = "unknown"` throughout). If 1.2a.1 also fails, the architecture must change, not just the analyzer.

This ADR records the options:

- **A** — Accept `unknown` as the honest answer, remove `data_edges_only` from the default path, and change ADR-0015 to say `data_flow_confirmed` is **enrichable metadata**, not **signal**.
- **B** — Replace `data_flow_confirmed` with a **three-valued** model that includes "unanalyzed" as a first-class state, and stop pretending that `control` is a "not confirmed" value for edges the analyzer could not parse.
- **C** — Introduce an **oracle layer** that runs a small LLM or a symbolic interpreter on the sink body and returns `data_flow_confirmed` — an LLM's argument analysis instead of ours.
- **D** — Drop flow semantics entirely from the prompt and let the LLM reason about control-only chains with a clear "no data-flow evidence" disclaimer.

The ADR must be written **before 1.2a.1 is declared done**, so that if 1.2a.1 underdelivers, the fallback is ready.

**Why Critical:** without a documented fallback, the project is one failed analyzer away from being a graph-construction tool that claims to be an attack-path analyzer.

**References:** ADR-0015; the `data_edges_only = 0` observation across four corpora; the P0/P2 review.

### Cross-file tool → sink

**Priority:** **High** (was High — kept, moved to Phase A)
**Status:** Not started
**Rationale:** `has_access_to` (tool → sink) is built only within a single file. Real FinBot-style projects have `tool` in `mcp/servers/findrive/server.py` and `sink` in `mcp/servers/findrive/repositories.py` — **no edge exists**. Without this, 1.2a.1 applies only to the minority of chains where tool and sink share a file.

**Approach:** Extend the call graph to span imports. Populate `flow_kind` and `data_flow_confirmed`. Depends on 1.2a.1's second pass — the flow question must be asked of the *tool body*, and the tool body may be in a different file from the sink definition.

**References:** ADR-0013 Deferred; the C4 L3 Phase 1 "What Is NOT Shown" section.

### Layer 3 semantic validation

**Priority:** **High** (new)
**Status:** Not started
**Rationale:** `is_chain_valid` checks that every node and edge **exists**. It does not check:

- Edge direction (chain `[A, B, C]` with edges `[e_XY, e_YZ]` validates even if `e_XY` goes from `A` to `X`, not to `B`)
- Node order (the first node need not be an entry point; the last need not be a sink)
- Edge-kind consistency (a `provides_tool` edge in the middle of a chain that claims to be a data path)
- That the anchor `untrusted_source` is actually a source

Consequence: a voter that returns `exploitable: true, evidence: []` produces `validated: true`. A hallucinated direction produces `validated: true`. This makes `validated: true` **not mean "correct"** — it means "existing", which is a different and weaker claim.

**Approach:** Add to `QueryLayer`:

- `is_chain_connected(nodes, edges)` — edge `i` links `nodes[i]` to `nodes[i+1]`
- `is_chain_typed(nodes, edges, entry_kind, sink_kind)` — first node is `entry_point`, last is `sink` (or sink-capability, per ADR-0014)
- `is_untrusted_anchor_valid(source_id)` — source node has `source_type` and `reads_untrusted` on the reader

Roll them into `is_chain_valid`. Report which check failed.

**Why High:** without this, "validated" is a marketing claim, not a security property. Every report currently says "Layer 3 validated" — and a reader is entitled to assume that means the chain was checked.

**References:** ADR-0006 (Layer 3); the `is_chain_valid` implementation; the P0/P2 review.

### 1.3d.1 — Corpus with lethal-trifecta node

**Priority:** **High** (was Low)
**Status:** Not started
**Rationale:** `lethal_trifecta` is **False on every node in every corpus**. It is the strongest single weight in ADR-0012 (+5). The mechanism is tested only on synthetic nodes (`tests/test_normalizer_stages/test_lethal_trifecta.py`). We do not know if the strongest scoring component **works** on real data, because no real data exercises it.

**What "works" means here:** a real target with one node that simultaneously reads untrusted input, can read secrets, and can get data out. The scorer's attribute path (+5) must fire from the attribute, not from the fallback. The read-side of the trifecta (`reads_untrusted`) is common (FinBot); the combination is not.

**Approach:** Find or build such a target. If none can be found in the wild, build a **minimal synthetic corpus** with one such node and document why: the point is to exercise the +5 path end-to-end (stage → attribute → score → prompt flag → verdict). A synthetic target that only exercises the mechanism is honest; a synthetic target that is claimed to be a realistic attack is not.

**Why High:** the project cannot tell its users that `lethal_trifecta` is its strongest signal **and** that the signal has never been tested on real data. One of the two must change.

**References:** 1.3d report; ADR-0009 (`lethal_trifecta_caps`); ADR-0012 (§"Cheap Scoring").

### Guardrails affect enumeration

**Priority:** **High** (new)
**Status:** Not started
**Blocked by:** `guards` edge emission (Guardrails section below)
**Rationale:** ADR-0007 is honest: "Chains do not yet account for guardrails. This is a false-negative risk." Guardrail nodes exist, but they are inert — `find_chains` does not skip a chain blocked by a guardrail, and no chain records that a guardrail was bypassed. 1.4's prompt (v1.6.0 candidate) will tell the LLM about guardrails — and the LLM will see them as nodes without knowing whether they matter.

**Three sub-problems, all required:**

1. **`guards` edge emission** — the edge kind is registered, `flow_kind = "structural"`, and nothing emits it. A guardrail is a declaration; `guards` describes what it protects.
2. **`bypassed_by` derived edge** — the reverse: which agent or tool bypasses which guardrail.
3. **Guardrail-aware `find_chains`** — either chains through a guardrail are marked `blocked_by_guardrail` (soft) or excluded from default enumeration (hard). **This is a new decision**, not covered by ADR-0007. It should be an ADR.

**Why High:** the project exposes guardrails as a graph kind; 1.4's prompt says "check guardrails on the path". If guardrails do not affect the path, the prompt is lying to the model. Either guardrails become load-bearing, or the prompt must stop claiming they are.

**References:** ADR-0007; ADR-0015 (structural edges excluded from `find_chains`); 1.4.

---

## Phase B — Close the coverage gaps that silently break features

### Write-side for `rag_corpus` / `email_inbox` / `user_upload`

**Priority:** **Medium** (new)
**Status:** Not started
**Rationale:** 1.3a.1 explicitly says: "RAG corpora, mailboxes and user uploads have no writer evidence at all". Chain groups (1.3b) require a write chain and a read chain. For three of the four source families, **the write chain does not exist**. ADR-0010 describes four families; only one (`mcp_tool_description`) functions end-to-end.

**What "write-side" means per family:**

- **`rag_corpus`** — a call that adds documents to a vector store (`add_documents`, `upsert`, `add_texts`, `index`). Detector rule in `patterns.py` (`WRITES_TO_RAG_RULE`), new source-type gate in `untrusted_source_writers`.
- **`email_inbox`** — a call that sends an email (`sendmail`, `smtp.send`, `send_message`). Detector rule `WRITES_TO_EMAIL_RULE`.
- **`user_upload`** — a route handler that accepts a file upload (`UploadFile`, `request.files`, `st.file_uploader`). This is a **structural signal**, not a call-site rule — a different detector shape. Deferred to a separate item (below).

**Approach:** three rules, one per family, each with corpus evidence. Do **not** add a rule without a corpus. 1.3a.1 was explicit: "a pattern that fires nowhere is a claim the data does not support".

**References:** 1.3a.1's report ("RAG corpora, mailboxes and user uploads have no writer evidence at all"); ADR-0010; ADR-0015 decision 5.

### `user_upload` source type

**Priority:** **Medium** (new)
**Status:** Not started
**Blocked by:** entry-point detector extension
**Rationale:** The `untrusted_source` rules are call-site based. `user_upload` is not a call shape; it is a **structural signal** — a route handler that writes a file and later reads it. Detecting it requires entry-point analysis (which route handler handles the upload? which directory does it write to? which tool reads from that directory?). This is a cross-component detector, not a `CallPattern`.

**Approach:** entry-point detector gains a `detect_uploads` pass that emits `untrusted_source` nodes with `source_type = "user_upload"` and a `has_access_to` edge to the handler. Then `untrusted_source_writers` gets a rule for this family (the upload handler *is* the writer).

**References:** ADR-0010 (source families); the entry-point detector (`parsing/entry_points.py`).

### `untrusted_source` identity review

**Priority:** **Medium** (new)
**Status:** Not started
**Rationale:** Node identity is `(source_type, call_target, source_file)`. Consequence:

- Two `retriever.invoke` calls in **one file** → **one** node, even if they are two different retrievers
- Two `retriever.invoke` calls in **two files** → **two** nodes, even if they are the same store

The comment says: "the corpus path, tool name or mailbox address is not in the call". That is correct: AST-level analysis cannot know. But the **graph does not record this uncertainty**, and downstream consumers (1.3b, 1.3e, the report) treat the node as a **single store**, which it may not be.

**What this item should produce:** a short ADR or a documentation note that names the limitation explicitly and specifies how downstream features should behave when identity is ambiguous. Options:

- **Accept** — one node per read site per file; document that corpora are undersplit.
- **Enrich** — add a `corpus_hint` attribute from literal arguments when available; two nodes with the same `(call_target, corpus_hint)` are still two nodes but the report can say "same store, two read sites".
- **Merge** — same `(source_type, call_target)` across files → one node. This loses the "read site" evidence; not recommended.

**References:** the `untrusted_source_id` implementation; the 1.3a design note.

### Evidence presence affects validation

**Priority:** **Medium** (new)
**Status:** Not started
**Rationale:** `is_chain_valid` does not look at voter evidence. A voter that returns `exploitable: true, evidence: []` produces `validated: true`. The `evidence_valid` field is computed per voter (in `_voter_response_dict`) but does not propagate to `Chain.validated`.

**Approach:** `Chain.validated` becomes a two-part condition:

- Every node and edge exists (current `is_chain_valid`)
- **At least one voter cited at least one edge from the chain** (new)

If the second fails, the chain is stored with `validated: false` and the reason is visible in the report.

**Why Medium, not High:** it is a bug, not a design flaw. But `validated: true` currently claims something the code does not check.

**References:** `evaluator._voter_response_dict`; `QueryLayer.is_chain_valid`.

### Judge determinism

**Priority:** **Medium** (was Deferred)
**Status:** Not started
**Rationale:** The judge runs with `temperature=0.6`. Two runs on the same graph with the same prompt can produce different `verdict` values, which are then persisted in `chains.verdict` and `chain_groups.verdict`. The report is non-deterministic even when the graph is not.

**Approach:** introduce `judge.temperature = 0.0` as the default, with an explicit override in the config for users who want stochasticity. Document the trade-off: deterministic judge is better for auditability, worse for exploring edge cases.

**References:** ADR-0005; the P0/P2 review "Deferred" section.

### `chain_groups.lethal_trifecta` column

**Priority:** **Low** (new)
**Status:** Not started
**Rationale:** `chain_groups.lethal_trifecta` exists in the schema and has **no writer**. It is `False` on every row. The per-node flag exists (1.3d), the per-group aggregate does not. The report does not show it (correctly), but `chain_groups.json` includes it as `false` on every row, which reads as a claim.

**Approach:** one line in `_evaluate_one_group`: `record.lethal_trifecta = any(node.attributes.get("lethal_trifecta") for node in read_chain_nodes)`. No schema change.

**References:** 1.3e report, "Flagged, not fixed"; 1.3d.

### `chain_groups.prompt_version` / `voter_outputs` columns

**Priority:** **Medium** (was Low)
**Status:** Not started
**Rationale:** `chains` has `prompt_version` and `voter_outputs`. `chain_groups` does not. A group verdict cannot be traced to prompt v1.5.0 in storage — only in logs. This is asymmetric: the linear path is auditable, the group path is not.

**Approach:** two columns (`prompt_version TEXT`, `voter_outputs JSON`), populated by `evaluator._evaluate_one_group`. Schema change; DB recreated per scan, no migration.

**References:** ADR-0005; the chain-group evaluator.

---

## Phase C — Resume the roadmap

### `openai_agents.py` detector

**Priority:** **High** (was High — kept)
**Status:** Not started
**Rationale:** Last detector in ADR-0011's implementation order. OpenAI Agents SDK is rising.

**Caveat:** this detector will **inherit** the architectural issues of the LangChain detector — the `_find_agent_edges` best-effort resolution, the `data_flow_confirmed = "unknown"` on `uses_tool`, the enrichment-only-when-prompt-names-the-tool pattern. Those issues are covered by 1.2a.1 and by the enrichment items below. This detector should not ship until 1.2a.1 lands — otherwise it will be built against a model that is about to change.

### 1.4 — Prompt engineering: grounded evaluation

**Priority:** **Medium** (was High — demoted, blocked by 1.2a.1)
**Status:** Partially done (P0/P2 v1.2.0 calibration; v1.3.0 `flow_kind` instructions; v1.5.0 group prompt). Full version blocked by 1.2a.1 and by ADR-0016.
**Rationale:** Prompt engineering **cannot fix the absence of data edges**. The current prompt says "a chain with data edges is stronger evidence" — but on real corpora such chains do not exist. The prompt is **lying to the model** about what data exists. It teaches the model to ignore a signal that is never present.
**Remaining work (once 1.2a.1 and ADR-0016 resolve):** `uncertain` verdict with concrete criteria; explicit production-vs-test entry point distinction; prompt regression tests.
**Demotion rationale:** it is in the wrong phase. It was High before this revision because it looked like a quality lever. It is not — it is a quality **multiplier**, and the multiplicand is missing.

### 1.3c.2 — Any-node semantics for impact sink capabilities

**Priority:** **Low** (reformulated)
**Status:** Not started
**Rationale:** `_score_read_sinks` walks only the last node of a read chain; `_score_read_writes_db` credits any node. Inconsistent on paper; no-op on the four corpora. But: "no-op on the four corpora" is **false reassurance**. On new corpora with `writes_db` mid-chain, scoring will systematically under-count.

**Approach:** widen `dangerous_sink` / `exfil_or_write` to any-node, or narrow `writes_db` to terminal-only. Prefer widening. Clarify the ADR-0012 table wording.

**What changed in this revision:** the rationale no longer says "no-op"; it says "we do not know if it matters — the corpora do not tell us". A Low priority is appropriate; a claim of irrelevance is not.

### Guardrails — `guards` edge emission

**Priority:** **High** (was High — kept, made explicit as a dependency of "Guardrails affect enumeration")
**Status:** Partially done (node kind only).
**Rationale:** `guards` is registered, `flow_kind = "structural"`, and nothing emits it. Chain enumeration does not treat guardrails as blocks or bypasses.
**Approach:** detect guardrail-to-agent and guardrail-to-tool relationships. Emit `guards` edges with `flow_kind = "structural"`. Depends on the guardrail attributes item below.

### Guardrails — attributes (`policy`, `enforcement_point`)

**Priority:** **High** (was High — kept)
**Status:** Not started
**Rationale:** Nodes exist without attributes. To know whether a guardrail blocks a chain, the graph needs to know **what it filters** and **where it sits**.
**Approach:** source-level analysis of the guardrail's body. `policy` ∈ `prompt_injection`, `pii`, `tool_allowlist`, `rate_limit`, `unknown`. `enforcement_point` ∈ `pre_tool_call`, `post_llm_response`, `pre_agent_step`, `unknown`.

### Guardrails — `bypassed_by` derived edge

**Priority:** **Medium** (was Medium — kept)
**Status:** Blocked by `guards` edge emission.

### Guardrails — guardrail-aware chain evaluation

**Priority:** **Medium** (was Medium — kept)
**Status:** Blocked by "Guardrails affect enumeration".

---

## Deferred (documented, not scheduled)

### 1.5 — Graph densification

**Priority:** **Deferred until 1.2a.1 and Phase A are done.**
**Rationale:** The graph is sparse (FinBot: 283 nodes, 93 edges — ratio 0.33). Adding call-graph edges without flow semantics would worsen false positives.

### 1.3e.2 — Linear-chain prompt v1.6.0

**Priority:** **Low**
**Rationale:** The group prompt (v1.5.0) and the linear prompt (v1.4.1) diverge. Compare line by line; either lift the group's "OUR SCORING" framing into the linear prompt, or document the divergence. **Blocked by ADR-0016.**

### `openai_agents` — enrichment parity

**Priority:** **Low**
**Rationale:** When the detector lands, verify it does not reintroduce the "enrichment only when the prompt names the tool" pattern. Covered by the enrichment items below.

### Enrichment — 1.2a.2a / 1.2a.2b / 1.2a.2c

**Priority:** **Medium** (each — kept from before)
- **1.2a.2a** — FinBot-style MCP tool enrichment (path-heuristic servers, no `framework` attribute).
- **1.2a.2b** — cross-file `uses_tool` enrichment (read the agent's system prompt from the same file).
- **1.2a.2c** — upsert enrichment precedence (a later explicit `"unknown"` currently overwrites an enriched value).

### 1.2a.3k — Dev script discovery

**Priority:** **Low**
**Rationale:** `scripts/test_websocket.py` (FinBot) contributes a sink `push_event` and a chain edge — low-value noise. `is_dev_code: true` node attribute or discovery exclusion.

### 1.2a.3i — Adapter patterns (kept done; monitored)

**Priority:** **Low**
**Status:** Done (2026-10-20).
**Note:** the adapter patterns are a **new source of false positives** (they match `adapter.execute`, `client.post`, etc.). No corpus has exercised them yet at scale.

### 1.3c.1 — Triples and quadruples (`max_chain_group_size > 2`)

**Priority:** **Low**
**Status:** Not started. Scoring and selection code paths for larger groups are not implemented.

### Cross-source groups

**Priority:** **Low**
**Rationale:** ADR-0012 assumes groups around **one** untrusted source. A real multi-step attack may connect two sources (plant in RAG, read from email). Not modeled.

### Authentication model

**Priority:** **Low** (new)
**Rationale:** The `authenticated_entry` scoring signal is pinned at 0 because the graph does not model authentication. To credit it, the graph needs to know which entry points require a session. This is not a call-site rule; it is a **semantic property** of the route handler.

### Shared-entry signal

**Priority:** **Low** (new)
**Rationale:** `shared_entry` is pinned at 0 because the enumerator cannot produce the shape it describes ("write and read chains share an entry point"). Reinterpreting it would invent a signal the ADR does not state. If it becomes important, ADR-0012 needs a new table entry that describes a shape the enumerator **can** produce.

---

## Icebox (deferred indefinitely)

- **Phase 2 — runtime enrichment.** Not planned. The project is a static correlator; the honest description is "static, plus an enrichment interface that has no implementations". Make this explicit in the README if the language still says otherwise.
- **DynamicWorker implementations** (file watcher, Docker diff, OSSEC).
- **Enrichment queue + static workers** (AAK, LLM-scanner, MCP-scanner).
- **Multi-host (Core + Agent).**
- **Schema migrations.** Deferred until the schema stabilizes. The DB is recreated per scan.
- **SCIP integration.**
- **Standalone executable** (mac / linux / win).
- **TUI** (was Medium × 3 — demoted).

---

## Low — when there is time

### Docker image

**Priority:** **Low** (was Icebox — promoted)
**Rationale:** The current entry cost is `pip install -e .` + `uvx` + Python 3.11+. A Docker image removes the Python/uv dependency from the user's machine. It is not a "standalone executable" (which is Icebox) — it is a **runnable artifact** for evaluation.

### CLI wrapper (`cli.py`)

**Priority:** **Low**
**Status:** Not started.

### Console script (`[project.scripts]`)

**Priority:** **Low**
**Status:** Not started.

### TSAOutput cleanup

**Priority:** **Low**
**Status:** Not started.

### Additional TSA modules for non-Python languages

**Priority:** **Low**
**Status:** Deferred until a non-Python target appears.

### Relative imports in cross-file resolution

**Priority:** **Low**
**Status:** Not started.

### Per-name resolution in cross-file tool edges

**Priority:** **Medium** — see "Precision" section below.

---

## Precision — better matching, not more matching

### `1.4` — Grounded evaluation (see Phase C)

### `1.2a.1` — see Phase A

### Enrichment — cross-file `uses_tool` (1.2a.2b)

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** NMB's 9 `uses_tool` edges are built by `cross_file.py` (import-driven) and never read the system prompt, so they stay `control/unknown`.

### Per-name resolution in cross-file tool edges

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** `resolve_agent_tool_edges` links an agent to **every tool in each imported module**. A module with 20 tools produces 20 edges even if the agent uses 3. Accepted false-positive trade-off (ADR-0009), but it inflates chain counts on real projects.

### `1.2a.3b` — Verb families (done; audit for overreach)

**Priority:** **Low**
**Status:** Done (2026-10-20).
**Note:** verb families (`repo.get*` etc.) were added to cover `<verb>_<noun>` method names. They match **any** noun. A `repo.get_or_create` lands in reads rather than writes. Audited on the four corpora: no misfires found. Not re-audited since.

### `1.2a.3d` — Test-file sinks (done; monitor)

**Priority:** **Low**
**Status:** Done (2026-10-20) — prompt note in voter, solo, judge.

### `1.2a.3g` — Attribute, f-string, composite (done; monitor)

**Priority:** **Low**
**Status:** Done (2026-10-20).

### `1.2a.3h` — Cursor fetch reads (done; monitor)

**Priority:** **Low**
**Status:** Done (2026-10-20).

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

### `MAX_CHAINS_PER_ENTRY` — configurable

**Priority:** **Low** (new)
**Rationale:** `markdown.py` caps at 30 chains per entry point. On FinBot, 182 chains → "… and 152 more chains". The **summary count** is truthful; the **detail** is truncated. For security reports the detail matters. Make it configurable or remove the cap and paginate.

### Report honesty

**Priority:** **Medium** (new)
**Rationale:** A `report.md` on a Phase 1 scan shows "Candidate chains (Phase 1 enumeration)" — and 182 candidates look like 182 findings to a reader who does not read the header. **Two fixes:**

1. Rename the section to **"Enumerated paths (not findings)"**.
2. Add a banner: **"This report contains candidate paths, not evaluated findings. Run `python run_eval.py` to evaluate."**

Neither is a code change; both are a **trust** change.

### `run_scan.py` writes a bundle — but ADR-0004 says "report on demand"

**Priority:** **Low** (documentation)
**Rationale:** ADR-0004 says reports are generated on request. `run_scan.py` always writes a bundle at the end. Either ADR-0004 is wrong, or `run_scan.py` should not write. **Recommendation:** keep the bundle write (it is useful for Phase 1 inspection), and update ADR-0004 to say "the initial Phase 1 bundle is written by the scan; evaluated reports are on-demand". A one-line ADR update.

---

## Cancelled / absorbed (do not resurrect)

### `1.3a` — Architecture and data-model review

**Status:** Cancelled (was "Not started; absorbed").
**Rationale:** absorbed by the incremental 1.3a / 1.3a.1 / 1.3b design passes. **Removed from the active list** — the old backlog kept it as "Not started", which created noise. If a structural change to `chain_groups` or `untrusted_source` is needed, open a **new** item with the specific change.

### `1.2a.3` — ORM call sites in capability patterns

**Status:** Cancelled (was "Done — covered by 1.2a.3b/c/e/f/g").
**Rationale:** the sub-items were re-opened as their own items. "Done" on the parent is misleading. **Removed from the active list**; the child items (1.2a.3b, c, e, f, g, h, i, j) remain in "Precision".

### `structural_heuristics.py`

**Status:** Cancelled. Its role was absorbed by `classification.py`.

---

## Maintenance

- **Update priorities** when external conditions change.
- **Update status** when work begins or ends.
- **Add new entries** when a deferred item surfaces.
- **Remove entries** when they are done or cancelled. Keep "Recently done" to one quarter.
- **Do not** add a "Done" entry whose sub-items are still open — mark it "Partially done" and link the open children. The previous revision had three items in "Done" whose children were open; those were the source of confusion.
- **Do not** add a "Low" item whose rationale is "no-op on the four corpora". On a new corpus, "no-op" is a hypothesis, not a fact. If the effect is unknown, say "unknown".
- This file records *what to do next*. The ADRs remain the authoritative record of *why*.

---

## Open questions

These are not backlog items; they are decisions the next planning session should settle before the backlog is revised again.

1. **Is `data_flow_confirmed` a promise, or enrichment metadata?** The answer determines whether 1.2a.1 is **Critical** (it must land) or **Medium** (nice to have). This revision assumes "promise" — hence Critical. Confirm or refute.
2. **Is Phase 2 (runtime) planned at all?** If not, the README and ADR-0003 should stop describing it as a phase and start describing it as an interface. The current "deferred" language creates the impression that it will happen.
3. **Is `lethal_trifecta` a claim?** If yes, 1.3d.1 is High (this revision's position). If no, the +5 weight in ADR-0012 should be demoted to "+5 when the flag is present, and the flag is rarely present" — an honest but weaker claim.
4. **Should `provides_tool` chains survive `data_edges_only=True`?** The parameter is named "data edges only" and `provides_tool` is control — by construction, MCP chains vanish on the shortlist. Either the parameter is misnamed, or MCP is not a first-class use case. Decide.
5. **Is `user_upload` a source family?** ADR-0010 lists four families; the implementation supports one and a half (two read-side, one write-side). The gap should be acknowledged in the ADR, not just in the backlog.
6. **Does `validated: true` mean "correct"?** Currently: "exists". The Layer 3 semantic item changes this. Decide **before** the change: if `validated: true` becomes semantically stronger, existing chains stored with `validated: true` become retroactively weaker under the new rules. The DB is recreated per scan, so this is not a migration — but it is a **reputation** change: what did the tool mean by "validated" yesterday?