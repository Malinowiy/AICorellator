# AICorellator — Backlog

Single registry of deferred, in-progress, and planned work. Complements the ADRs (which record *decisions*) with a working list of *what to do next*.

**Last updated:** 2026-10-29
**Revision:** restructured around ADR-0016, ADR-0017, ADR-0019, ADR-0020, ADR-0021. Phase B in progress (B.1, B.2, B.3a, B.3b-core, B.3b-fs, B.3b-user_upload-core, B.3b-user_upload-fastapi, B.4a, B.4b, B.10a, B.10b done). See §0 for the direction and the Top-10 view.

---

## 0. Direction and priorities

### 0.1. The diagnosis this revision is built on

The previous revision (2026-10-25) was built on ADR-0016 and ADR-0017: the AST layer is control-only, `observed_by` is on every node and edge, `data_edges_only = []` on an AST scan is the correct answer. Three ADRs have been accepted since and shape the current direction:

1. **ADR-0019 — guardrail emission and evaluation weighting.** `guards` is emitted from source-level evidence (call site, `@guardrail(...)` decorator) and reaches the LLM as a `guardrails` payload section, not as an enumeration filter. B.4a and B.4b implement it; end-to-end is blocked by B.4-gap (no producer of `guardrail` nodes after ADR-0016).
2. **ADR-0020 — capability inheritance from a scanned base class.** A subclass of a scanned base inherits the base's own capabilities, one level, cycle-safe. B.10b implements it; the FinBot-shaped hierarchy (a `BaseAgent` that calls `self.llm_client.chat(...)`, subclasses that call `_run_agent_loop`) now produces an `agent` node for the subclass, structurally.
3. **ADR-0021 — store as a graph entity, with probabilistic write/read links.** A `store` is a place write and read call sites point at; `writes_to` / `reads_from` are structural edges with an `identity_confidence` computed from signals (family, literal, code context). Two revisions: §1 (family = technology, `user_upload` is a source type), §8 (base 0.5 for a context-based writer). B.3a (mechanism), B.3b-core (family interface, registry, resolver), B.3b-fs (first family), B.3b-user_upload-core (upload detector interface, context, writer logic), B.3b-user_upload-fastapi (first detector) implement it.

Four facts from the 2026-10-29 state:

1. **Phase B is closing the coverage gaps.** B.1 (cross-file `tool → sink`), B.2 (write-side for `rag_corpus` / `email_inbox`), B.4a / B.4b (guardrails), B.10a / B.10b (capability coverage + inheritance), B.3a / B.3b-core / B.3b-fs / B.3b-user_upload-core / B.3b-user_upload-fastapi (store model) are done.
2. **The store model is the largest new subsystem since Phase A.** A `store` node kind, `writes_to` / `reads_from` as structural edges, `identity_confidence` as a second confidence field, a store-family registry and a store-context registry, and a join in `find_chain_groups` by store id. The **first real family** (`fs`) is shipped; the **first real upload detector** (FastAPI) is shipped; **three known gaps** (B.4-gap, B.3b-user_upload-attribution, B.3b-fs prefix-inert) are recorded and not papered over.
3. **The `max_depth` dimension is not yet implemented.** `find_chain_groups(max_depth=1)` is the current enumeration; `max_depth=2+` (three-hop and longer) is a post-release step. Depth is the parameter that makes a multi-store chain (write → store → read → write → store → read) enumerable.
4. **Layer 3 validates existence, semantics, and now (via B.9) will validate evidence presence.** B.9 remains a Medium item.

Plus two coverage gaps that Phase B has not yet closed:

- **`guardrail` node producer (B.4-gap).** No deterministic layer produces `guardrail` nodes after ADR-0016; B.4a's emission is correct and tested but returns zero on the four corpora. The decision (capability rule / framework detector / LLM-only) is B.4-gap.
- **`user_upload` attribution end-to-end (B.3b-user_upload-attribution).** The attribution logic works (unit-tested), but an `fs` read is not one of 1.3a's source types, so the reader has no `untrusted_source` node to stamp with `source_type='user_upload'`. A multi-role `source_type` rule or a producer of `untrusted_source` for `fs` reads in the writer's scope is the decision.

### 0.2. The direction this revision sets

**Fix the foundation, then build; close coverage gaps family by family.**

- **Phase A — done.** ADR-0016 and ADR-0017 implemented; Layer 3 semantic validation (A.4) done.
- **Phase B — in progress.** Cross-file `tool → sink` (B.1), write-side for `rag_corpus` / `email_inbox` (B.2), guardrail emission and weighting (B.4a, B.4b), capability coverage and inheritance (B.10a, B.10b), store model (B.3a, B.3b-core, B.3b-fs, B.3b-user_upload-core, B.3b-user_upload-fastapi) are done. Remaining: Streamlit / Django / Flask upload detectors (B.3b-user_upload-streamlit / -django / -flask), B.3b-s3, B.3b-db, B.3b-llm_fallback, B.3c (migration of B.2 to the store model), B.5 (judge determinism), B.9 (evidence presence).
- **Phase C — next.** Layer 2 runtime for Python. The only path to `data_edges_only > 0` on a real corpus.
- **Phase D — then.** Resume the roadmap: `openai_agents` detector, prompt engineering on a graph where the data exists, LLM classifier fine-tune.

**What is explicitly *not* planned in this cycle:** TUI (Icebox), new eval modes, multi-host, standalone executables, SCIP, schema migrations, non-Python runtime, `max_depth > 1` (post-release).

### 0.3. Top 10 by impact

| # | Item | Priority | Why it is here |
|---|---|---|---|
| 1 | **Cross-file `tool → sink`** (B.1) | **High** | **Done.** Enables real paths through imports. |
| 2 | **Guardrails affect evaluation** (B.4a + B.4b) | **High** | **Done.** Guardrails enter the payload, not the traversal. End-to-end blocked by B.4-gap. |
| 3 | **Store model** (B.3a, B.3b-core, B.3b-fs, B.3b-user_upload-core) | **High** | **Done.** A `store` node, `writes_to` / `reads_from` with `identity_confidence`, a family registry, a context registry. |
| 4 | **Upload detector (FastAPI)** (B.3b-user_upload-fastapi) | **High** | **Done.** First real `UploadDetector`. |
| 5 | **Write-side for `rag_corpus` / `email_inbox`** (B.2) | **Medium** | **Done.** Both families now have a writer. Migrates to store in B.3c. |
| 6 | **`guardrail` node producer** (B.4-gap) | **High** | B.4a and B.4b are correct but return zero until a producer exists. A decision, not a fix. |
| 7 | **Upload detectors for Streamlit / Django / Flask** (B.3b-user_upload-streamlit / -django / -flask) | **Medium** | Three more detectors on the same interface. **Next.** |
| 8 | **`s3` / `db` families** (B.3b-s3, B.3b-db) | **Medium** | Two more store families. Parallel or sequential; Cline's and your choice. |
| 9 | **Store migration of B.2** (B.3c) | **Medium** | `writes_to` targets `store`, not `untrusted_source`, for every family. |
| 10 | **Judge determinism** (B.5) | **Medium** | Verdicts in `chain_groups` are not reproducible. |

---

## Priority legend

- **Critical** — blocks the current phase. Nothing else starts until these land.
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

### 2026-10-29 — B.3b-user_upload-fastapi: first upload detector

- **New `store/context/upload/detectors/fastapi.py`.** `FastAPIUploadDetector(UploadDetector)`, `name='fastapi'`. Three shapes: `UploadFile` / `fastapi.UploadFile`; `Subscript` with base in `UPLOAD_CONTAINERS` and slice `UploadFile`; `File(...)` default with `bytes` or no annotation. `Form(...)` beats `UploadFile`. `bytes` alone is not an upload. Positional-only, positional, keyword-only parameters; `*args`/`**kwargs` never.
- **Route check** by handler name against `entry_points`, plus a decorator confirmation that tightens hand-built deltas.
- **Registration** by literal name in `detectors/__init__.py`; `upload/__init__.py` imports `detectors`.
- **Docstring truth-fixes** in 4 files.
- **Tests:** +58 (test_store_upload_fastapi). 1570 passed.
- **E2E:** `/tmp/b3bfs_target` gains one edge: `writes_to entry_point→store 0.872` (in addition to the resolver's `writes_to sink→store 0.776`).
- **Attribution inert end-to-end** for a stronger reason than core recorded: an `fs` read is not an `untrusted_source` node at all. Recorded in B.3b-user_upload-attribution.

### 2026-10-29 — B.3b-user_upload-core: upload detector interface, context, writer logic, attribution

- **New `store/context/upload/`** — base, registry, context, writer, attribution.
- **`UploadDetector` interface** (mirrors framework detectors), `UploadSite`.
- **`UserUploadContext.identify`** calls registered detectors, merges, dedups.
- **Writer logic** — entry-point → stores reachable via call graph, base 0.5 (context signal, ADR-0021 §8 revision 2), literal + same-module + context-in-body.
- **Attribution** — `source_type='user_upload'` on `untrusted_source` in the writer's scope, no overwrite.
- **`store_resolver` stage** gains the writer/attribution step, merge-by-id for the one collision case.
- **Tests:** +74. 1512 passed.
- **Known:** attribution inert end-to-end; `fs` write sites re-derived in the stage; docstrings stale in `registry.py`, `store_resolver.py`.

### 2026-10-29 — ADR-0021 §8 revision 2: base 0.5 for a context-based writer

- One clause in §8: an entry-point handler that a `UploadDetector` identifies as accepting an upload is a context-based writer; base confidence 0.5, not 0.3. Combine as before.
- Recorded in the header, in a "Revision note 2", and in the commit.

### 2026-10-29 — B.3b-fs: `fs` family

- **New `store/families/fs.py`.** `FsFamily`, `detect_writes` / `detect_reads` for `open`, `aiofiles.open`, `Path.write_*` / `Path.read_*` / `Path.open(mode)`. Write modes `w/a/x/+`; read modes `r` or absent; `r+` is both.
- **Literal extraction:** `Constant` → full hint; `BinOp` / `JoinedStr` prefix; `Name` / `Attribute` → `None`.
- **`literal_match`** gains the containment branch (`startswith`, both ways, +0.3); exact +0.6, one-side +0.2.
- **Registration** by import (`store/families/__init__.py` → `register_family("fs", FsFamily)`).
- **Tests:** +55. 1438 passed.
- **Known:** prefix containment is inert through the resolver (`_attached_keys` attaches only to the exact key). Backlog: B.3b-fs-followup. Docstrings in `registry.py`, `resolver.py` stale.

### 2026-10-29 — B.3b-core: store family interface, registry, resolver

- **New `store/` package** (7 files): interface, registry, confidence, resolver, families, context.
- **`Family` interface**, `WriteSite`, `ReadSite`, `ModuleFacts`, `Context`.
- **`StoreResolver.resolve(delta, ctx)`** — calls families, pairs, computes `identity_confidence`.
- **`literal_match`, `code_context`, `combine_signals`** (ADR-0021 §8 signals).
- **Seam:** normalizer stage 19 (`StoreResolverStage`), before `guardrail_edges` — the correct seam, not `run_scan.py`.
- **Tests:** +74. 1383 passed.

### 2026-10-29 — B.3a: store node, reads_from, identity_confidence, store join

- **`store`** in `VALID_NODE_KINDS` and `nodes.kind`; **`reads_from`** in `VALID_EDGE_KINDS` and `edges.kind`; **`Edge.identity_confidence`** (`REAL`, `NULL` on all kinds except `writes_to` / `reads_from`).
- **`upsert`** combines `identity_confidence` by `1 − (1−a)(1−b)` (cap 0.99) for those two kinds; `confidence` stays `max`.
- **`STRUCTURAL_EDGE_KINDS`** grows to `{"guards", "writes_to", "reads_from"}`; `CONTROL_EDGE_KINDS` loses `writes_to`.
- **`find_chain_groups(include_weak=False, store_confidence_threshold=0.7)`** joins by store id.
- **`scan_config.store.confidence_threshold`** (default 0.7); `WEAK_JOIN_FLOOR = 0.3`.
- **Tests:** +31. 1309 passed.
- **Known:** transitional `writes_to` flow_kind (B.2 producer still emits control; B.3c retargets); collision with B.2's old path (B.3c resolves); `include_weak` API-only (report not wired).

### 2026-10-29 — ADR-0021 revision 1: family = technology, source type = semantics

- §1 corrected: a store family is the technology (`fs`, `s3`, `db`), not the semantic role. `user_upload` is a source type, not a family.
- §12 extended: store families are modules with a `detect_writes` / `detect_reads` interface, registered in a `registry.py` — the framework-detector shape.
- Revision note at the end; header revised.

### 2026-10-29 — ADR-0021: store as a graph entity, with probabilistic write/read links

- `store` node kind; `writes_to` targets a store, not an `untrusted_source`; `reads_from` (`store → untrusted_source`) is a new structural edge.
- `Edge.identity_confidence` — existence vs identity.
- `find_chain_groups` joins by store id; weak groups (`0.3 ≤ join confidence < 0.7`) are in the graph, `weak: true`, not in default enumeration.
- `store_confidence_threshold` in `config.yaml` (default 0.7).

### 2026-10-29 — B.2: write-side for `rag_corpus` and `email_inbox`

- Two new capabilities (`writes_rag_corpus`, `writes_email_inbox`) and two writer rules (closed pattern sets, reasons).
- `WRITER_RULES` table next to `UNTRUSTED_SOURCE_RULES`; `untrusted_writer_match`.
- Anchor rule: writer is the node the delta holds for the symbol that credited the capability; visible from the source by same-file or shared-import.
- **Tests:** +32. 1278 passed.
- **Known:** `from pkg import module` is not resolved by `_module_matches_file` (shared with B.1). A write site whose symbol has no node gets no edge (deliberate). A read whose reader has no node yields the source but no `source → reader` edge (pre-existing 1.3a).

### 2026-10-29 — B.10b: capability inheritance from a scanned base class

- **New `parsing/capability_inheritance.py`.** `apply_inheritance(capabilities, manifest)` — a second pass after per-file extraction.
- **§1** one level, **§2** base in scanned tree only, **§3** override does not block, **§4** transitive / method-level / multiple inheritance not done, **§5** cycle safety by construction.
- `run_scan.py` calls it once, before `run_classifier`.
- **Tests:** +20. 1246 passed.
- **Known:** `BaseAgent` in `EXCLUDED_CLASSES` never becomes a node; its capabilities reach `InvoiceAgent`. Docstrings stale in `flow_kind.py` / `ast_normalizer.py` (fixed in follow-up doc-debt).

### 2026-10-29 — ADR-0020: capability inheritance from a scanned base class

- `caps(cls) = caps(cls's own body) ∪ caps(cls's direct base)`, one level, cycle-safe, base resolved in the scanned tree.
- Override does not block; false positive on kind accepted.
- Extends ADR-0009 (marker in ADR-0009).

### 2026-10-29 — B.10a: `CALLS_LLM_RULE` — qualifier families for wrapped LLM clients

- `CallPattern.qualifier_prefixes` — a family replaces the literal qualifier (`llm`, `model` as prefixes). `self.llm_client.chat(...)` now credits `calls_llm`.
- Text form `{llm,model}.invoke`; prefix = exact or underscore boundary.
- **Tests:** +29. 1226 passed.
- **Known:** `model.generate` newly credited (unavoidable; one family per verb). `classification.py`'s `calls_llm` alone → `agent` (not `llm_endpoint`) — brief was wrong; code is consistent with ADR-0009.

### 2026-10-29 — Doc-debt after B.4a: `flow_kind.py` and `ast_normalizer.py` docstrings

- Two docstrings that described pre-B.4a state corrected.
- Follow-up: `flow_kind.py` inline comment, `ast_normalizer.py` edge-list header, `ast_normalizer.py` "fifteen → nineteen stages".

### 2026-10-29 — B.4b: guardrails in the evaluation payload

- Payload gains a `guardrails` section (root for a chain, partition `{write, read, anchor}` for a group).
- `PROMPT_VERSION` v1.5.0 → v1.6.0; one rule block per system prompt (voter, solo, group, judge); group form splits by side.
- **Tests:** +19. 1197 passed.
- **Known:** corpus regression (C2) not run (external); follow-up on `SOLO` step 4 "bypassable" (a question the new rule says the payload cannot answer).

### 2026-10-29 — B.4a: guardrail edges (`guards`) emission

- New stage 19 (`guardrail_edges`), `guardrail → component` from a call site or a `@guardrail(...)` decorator.
- `flow_kind="structural"`, `observed_by=["ast"]`, `data_flow_confirmed=NULL`.
- **Tests:** +11. 1179 passed.
- **Known:** `guards = 0` on all four corpora — no producer of `guardrail` nodes after ADR-0016 (B.4-gap).

### 2026-10-29 — ADR-0019: guardrail emission and evaluation weighting

- Emission from source-level evidence only; `guardrail → component`; co-location not emitted.
- Influence via weighting (payload), not exclusion or marking; `find_chains` unchanged.
- Extends ADR-0007 (marker in ADR-0007).

### 2026-10-29 — B.1: cross-file `tool → sink` through imports

- `cross_file_resolution` (stage 14) gains `has_access_to` from a tool to a sink in an imported module, reached through the call graph.
- Sinks and tools read from `delta.nodes`, not from `ctx.sinks_by_file` (stage 14 runs after the merge).
- `flow_kind="control"`, `observed_by=["ast"]`, `data_flow_confirmed=NULL`.
- **Tests:** +6. 1166 passed.
- **Known:** `from pkg import module` is not resolved by `_module_matches_file` (B.1-followup, shared with B.2).

### 2026-10-27 — A.4: Layer 3 semantic validation

validate_chain(nodes, edges) -> (ok, validated_kind, notes); existence → connected → typed → untrusted-anchor. is_chain_valid stays as the boolean wrapper.
Schema: chains.validated_kind (NULL | 'existence' | 'semantic'), chains.validation_notes (JSON); chain_groups.validated_kind (same CHECK).
evaluator._evaluate_one stores all three values; reporting/markdown.py prints > **Layer 3:** <note> under the reasoning for rejected chains.
Direction check is defense in depth: find_chains cannot emit direction-broken candidates (DFS records edges leaving the current node); the check protects chains arriving by any other path.
Tests: +46 (new tests/test_semantic_validation/, 42; test_query_layer +2; test_reporting +3; repository round-trip). 1160 passed.
ADR-0006 marked Superseded in part by ADR-0018; ADR-0018 written (separate commit).

### 2026-10-27 — A.3b: `entry_points.py` + `delegation_edges.py` are name-free

- The parked agent-call heuristics deleted; a call site is a raw `SymbolCall`; the resolver decides against existing agent nodes.
- Two decisions settled: module→file match by trailing path segments; one hop reads invoked names.
- Tests re-pointed; `tests/fixtures/agent_delegation/` new.

### 2026-10-25 — A.3a: `patterns.py` and `classification.py` are name-free

The class-A heuristics are gone from both modules. A class name no longer grants a node its kind.

- **`patterns.py`:** the seven dictionaries of ADR-0016 are removed, plus `CLASS_SUFFIX_KINDS`, `EXTRA_LLM_CLASSES` and `DELEGATE_METHOD_PREFIX` — the same artefact as a class suffix, a class name and a method prefix. The `*Agent` / `*Orchestrator` / `*LLMClient` / `*Guardrail` / `*Server` suffix rules go with them, so `KnowledgeServer` no longer becomes an `mcp_server` node.
- **`classification.py`:** `classify_class` and `classify_method` read capabilities and framework provenance only. `provider` on an `llm_endpoint` comes from the detector or the config extractor, never from the class name.
- **Parked, not fixed:** the agent-call heuristics (`delegate_to_*`, `run_*_agent`, `EXTRA_AGENT_CLASSES`, the bare `*Agent` call) now live in `entry_points.py`, marked "on loan, pending BACKLOG A.3b". Entry-point resolution is unchanged by this commit; A.3b replaces it.
- **Fixtures:** classes that were "agents" only by name now need a call that carries `calls_llm` (e.g. `self.llm.invoke(...)`) — the capability is the only remaining trigger.
- **Tests:** dictionary tests replaced by `test_class_a_name_tables_are_gone` / `test_exclusions_remain`; `test_classification.py` rewritten around "capability → kind" and "framework → kind".
- **Corpus check:** unchanged on `realistic_agent`, NMB, dvmcp and mcploitable. FinBot's delta is deferred to A.3b and explained there (its agents carry no capability evidence — B.10).
- **Tests: 985 passed.**

### 2026-10-25 — A.2: one config file, `--clean` keeps `config/`

- `config/config.yaml` replaces `config/scan.yaml` + `config/models.yaml`. Sections: `detectors`, `llm_classifier`, `runtime`, `evaluation`, `llm_classifier_model`, `unclassified`, `logs`, `store`.
- `scan_config.py`: `ScanConfig` + loader read the scan-side sections from the same file; `write_default_config` writes the full template.
- `analysis/config_loader.py`: `load_config` reads `evaluation:`; `default_mode` renamed to `evaluation.mode`.
- `workspace.py`: `config_path` replaces `scan_config_path` and `models_config_path`; `ensure()` creates one template with one warning.
- `run_scan.py`, `run_eval.py`: `--config` defaults to `<workdir>/config/config.yaml`.
- `--clean` keeps `config/`; to reset config too, remove the workspace directory.
- v0.1 is early alpha: legacy `config/models.yaml` and `config/scan.yaml` are ignored, not migrated.
- Tests: 985 passed.

### 2026-10-25 — Schema + `observed_by` + `FlowKindStage` (A.1)

The ADR-0016 foundation. The AST layer is control-only; `observed_by` is on every node and edge.

- **`observed_by`** (`schema.sql`, `graph/models.py`): `JSON NOT NULL DEFAULT '["ast"]'` on `nodes` and `edges`, `CHECK (json_valid(observed_by))`, `Node.observed_by` / `Edge.observed_by: list[str]`. `upsert` merges it as a set union (`repositories/nodes.py`, `repositories/edges.py`).
- **Control-only AST layer:** `FlowKindStage` pins `flow_kind = "control"` on every non-structural edge and neither reads nor writes `data_flow_confirmed`. Every resolver that runs after it constructs control edges too, and `detector_merge` no longer copies `flow_kind` / `data_flow_confirmed` across a merge. The flow analyzers (`flow/`) keep their behavior; nothing on Layer 1a writes their output (ADR-0016 §Neutral 1–2).
- **`find_chains(data_edges_only=True)`** filters on `"runtime" in edge.observed_by`, independent of `flow_kind`. On an AST-only scan the shortlist is `[]` by construction — the correct answer, not a failure.
- **Tests:** AST-layer assertions rewritten from `data_flow_confirmed = "unknown"` to `NULL`; new coverage for the SQL default, the `upsert` union and the `data_edges_only` filter (`test_schema.py`, `test_repositories.py`, `test_query_layer.py`). **913 passed.**

### 2026-10-25 — ADR-0016 and ADR-0017

- **ADR-0016** — control-only AST layer; `observed_by` on every node and edge; classification layers; scan policy in `evaluation: in config/config.yaml`; removal of class-A name heuristics.
- **ADR-0017** — Layer 1 / Layer 2 stitching; key `(file_path, line_start, name)`; no new edge kind (`has_access_to` + `observed_by`); idempotent; multiple runs distinguished by `runtime:<run_id>`.
- **README.md** — rewritten §"How It Works" for users; §"Status" reframed (`data_edges_only = []` is the correct answer, not a failure); §"Roadmap" updated.
- **Documentation/Architecture/OVERVIEW.md** — new; four layers, two attributes, `evaluation: in config/config.yaml`, module layout, Mermaid diagrams.

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

**Closed.** All items done: A.1, A.2, A.3a, A.3b, A.4.

---

## Phase B — Close the coverage gaps

**The core promise of this phase:** coverage gaps that make existing features work — cross-file `tool → sink`, write-side for the source families, guardrails as load-bearing, capability coverage, the store model, judge determinism, evidence presence.

### B.1 — Cross-file `tool → sink`

**Priority:** **High**
**Status:** Done (2026-10-29).
**Rationale:** `has_access_to` (tool → sink) was same-file only.
**Approach:** `cross_file_resolution` (stage 14) gains `has_access_to` from a tool to a sink in an imported module, reached through the call graph; sinks and tools read from `delta.nodes` (stage 14 runs after the merge); `flow_kind="control"`, `observed_by=["ast"]`, `data_flow_confirmed=NULL`.
**Size:** ~1 day.
**References:** ADR-0013 Deferred.

### B.2 — Write-side for `rag_corpus` / `email_inbox`

**Priority:** **Medium**
**Status:** Done (2026-10-29). Migrates to the store model in B.3c.
**Rationale:** 1.3a.1 said RAG corpora and mailboxes had no writer evidence; only `mcp_tool_description` had a write side.
**What changed:** two new capabilities (`writes_rag_corpus`, `writes_email_inbox`) and two writer rules; `WRITER_RULES` table next to `UNTRUSTED_SOURCE_RULES`; `untrusted_writer_match`. Anchor rule: writer is the node the delta holds for the symbol that credited the capability; visible from the source by same-file or shared-import.
**Size:** ~1 day.
**References:** ADR-0010; ADR-0015 decision 5; ADR-0021.

### B.3 — Store model (the largest Phase B step)

**Priority:** **High**
**Status:** In progress.

#### B.3a — Mechanism: store node, reads_from, identity_confidence, store join

**Priority:** **High**
**Status:** Done (2026-10-29).
**Rationale:** ADR-0021 makes the store an entity. B.3a builds the shape, without a family.
**What changed:** `store` node kind; `reads_from` edge kind; `Edge.identity_confidence`; `STRUCTURAL_EDGE_KINDS` grows to `{"guards", "writes_to", "reads_from"}`; `find_chain_groups(include_weak=False, store_confidence_threshold=0.7)` joins by store id; `scan_config.store.confidence_threshold`.
**Size:** ~1 day.
**References:** ADR-0021 §1–§9.

#### B.3b-core — Store family interface, registry, resolver

**Priority:** **High**
**Status:** Done (2026-10-29).
**Rationale:** ADR-0021 (revision 1) makes a store family a module. B.3b-core builds the interface, the registry, the resolver.
**What changed:** new `store/` package (interface, registry, confidence, resolver, families, context); `Family` interface; `WriteSite` / `ReadSite` / `ModuleFacts` / `Context`; `StoreResolver.resolve`; seam is normalizer stage 19 (`StoreResolverStage`).
**Size:** ~1 day.
**References:** ADR-0021 §1, §7, §8, §12 (revision 1).

#### B.3b-fs — `fs` family

**Priority:** **High**
**Status:** Done (2026-10-29).
**Rationale:** First real family. `open`, `aiofiles.open`, `Path.write_*` / `Path.read_*` / `Path.open(mode)` are store call sites.
**What changed:** `store/families/fs.py`; `literal_match` gains the containment branch (+0.3); registration by import.
**Size:** ~1 day.
**References:** ADR-0021 §8.

#### B.3b-user_upload-core — Upload detector interface, context, writer logic, attribution

**Priority:** **High**
**Status:** Done (2026-10-29).
**Rationale:** Upload is a context, not a family (ADR-0021 §1 revision 1). The interface, the registry, the writer logic (entry-point → stores reachable via call graph, base 0.5, ADR-0021 §8 revision 2), the attribution (source_type='user_upload' in the writer's scope).
**What changed:** new `store/context/upload/` — base, registry, context, writer, attribution; `store_resolver` stage gains the writer/attribution step; merge-by-id for the one collision case.
**Size:** ~1 day.
**References:** ADR-0021 §1, §8 (revision 2).

#### B.3b-user_upload-fastapi — First upload detector

**Priority:** **Medium**
**Status:** Done (2026-10-29).
**Rationale:** First `UploadDetector`. `UploadFile` / `list[UploadFile]` / `bytes = File(...)`; `Form(...)` beats; `bytes` alone is not.
**What changed:** `store/context/upload/detectors/fastapi.py`; registration; docstring truth-fixes.
**Size:** ~0.5 day.
**References:** ADR-0011; ADR-0021.

#### B.3b-user_upload-streamlit — Second upload detector

**Priority:** **Medium**
**Status:** **Next.**
**Rationale:** `st.file_uploader` is already an entry point (`STREAMLIT_ENTRY_CALLS`). The detector emits an `UploadSite` for a module-level entry point whose body calls `st.file_uploader`.
**Open question:** module-level entries currently get no writer edge (B.3b-user_upload-core decision #5). The Streamlit step must decide: accept no edge, or extend the interface with a module-level entry shape (a new step). The **retro** before the brief settles this.
**Size:** ~0.5 day.
**References:** B.3b-user_upload-core; `parsing/entry_points.py`.

#### B.3b-user_upload-django — Third upload detector

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** `request.FILES` in a Django view. Same interface.
**Size:** ~0.5 day.
**References:** B.3b-user_upload-core.

#### B.3b-user_upload-flask — Fourth upload detector

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** `request.files` in a Flask view. Same interface.
**Size:** ~0.5 day.
**References:** B.3b-user_upload-core.

#### B.3b-s3 — `s3` family

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** Second family. `s3.put_object` / `s3.get_object`, `boto3` / `s3fs` / `MinIO`. Literal `Bucket` / `Key` extraction; prefix containment.
**Size:** ~1 day.
**References:** ADR-0021 §8; B.3b-fs.

#### B.3b-db — `db` family

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** Third family. SQLAlchemy / asyncpg. Literal `INSERT INTO` / `SELECT FROM` table extraction (simple regex, not a SQL parser).
**Size:** ~1 day.
**References:** ADR-0021 §8.

#### B.3b-llm_fallback — LLM identity fallback

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** When the AST does not find a literal (unresolved identity), the LLM classifier (D.2) proposes an identity hint from the surrounding code. `identity_source='llm'`, confidence 0.2.
**Size:** ~1 day.
**References:** ADR-0021 §7; D.2; `store/context/llm_fallback.py` (new).

#### B.3c — Migrate B.2 to the store model

**Priority:** **Medium**
**Status:** Not started. **Blocked by** B.3b-s3 and B.3b-db (both families must be in the store model), and by B.3b-user_upload-* (the upload context must be in the store model).
**Rationale:** `writes_to` targets `store` for every family, including `rag_corpus`, `email_inbox`, `mcp_catalogue`. `untrusted_source` remains the read site. `reads_from` connects the store to the read site. `find_chain_groups` joins by store id for all families.
**What changes:** B.2's writer rules emit `writes_to → store`; `untrusted_source_writers` and `untrusted_source_writers` retarget; the transitional control flow_kind for `writes_to` becomes structural; the collision between the old path and the store path is removed; `report.md` and `chain_groups.json` gain `weak: true` when relevant.
**Size:** ~2 days.
**References:** ADR-0021 §11.

#### B.3b-fs-followup — Prefix containment through the resolver

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** `literal_match`'s containment branch (+0.3) is correct and tested, but inert end-to-end: `_attached_keys` attaches a site only to the exact key it equals, so a write hint `/uploads/` and a read hint `/uploads/x` produce two stores, not one. Making containment live means changing `_attached_keys` (a resolver change, not a family change), and the two stores should become one store with two edges — a rule, not a one-liner.
**Size:** ~1 day.
**References:** B.3b-fs; ADR-0021 §8.

#### B.3b-user_upload-attribution — `source_type='user_upload'` end-to-end

**Priority:** **High** (blocks B.3b-user_upload-* from producing a `user_upload` chain group)
**Status:** Not started.
**Rationale:** The attribution logic works (unit-tested), but end-to-end it is inert: a filesystem read (`open('/uploads/x', 'rb')`) is not one of 1.3a's source types, so the reader has no `untrusted_source` node to stamp. The `user_upload` read side has no producer yet — the same shape as B.4-gap.
**Two options:**
- **(a)** Upload context creates an `untrusted_source` node for an `fs` read in the writer's scope. New producer, new identity rule.
- **(b)** Multi-role `source_type` on an existing `untrusted_source` node (a source can be both `user_upload` and `rag_corpus` in different scopes). ADR-0022 territory.
**Decision needed before any implementation:** (a) or (b), with a corpus or fixture that motivates it.
**Size:** ~1 day.
**References:** B.3b-user_upload-core; ADR-0021 §10; ADR-0022 (not yet written).

### B.4 — Guardrails affect evaluation

**Priority:** **High**
**Status:** In progress.

#### B.4a — `guards` edge emission

**Priority:** **High**
**Status:** Done (2026-10-29).
**Rationale:** `guards` was registered, structural, and nothing emitted it.
**What changed:** new stage 19 (`guardrail_edges`); `guardrail → component` from a call site or a `@guardrail(...)` decorator; `flow_kind="structural"`, `observed_by=["ast"]`, `data_flow_confirmed=NULL`.
**Known:** `guards = 0` on all four corpora (B.4-gap).
**References:** ADR-0019 §1.

#### B.4b — Guardrails in the evaluation payload

**Priority:** **High**
**Status:** Done (2026-10-29).
**Rationale:** A guardrail on or near a path lowers the model's confidence; it does not exclude or mark.
**What changed:** payload `guardrails` section (root for a chain, partition for a group); `PROMPT_VERSION` v1.5.0 → v1.6.0; one rule block per system prompt.
**References:** ADR-0019 §2–§3.

#### B.4-gap — No producer of `guardrail` nodes after ADR-0016

**Priority:** **High** (blocks B.4 end-to-end)
**Status:** Not started.
**Found in:** B.4a implementation (2026-10-29).
**Rationale:** B.4a emits `guards` edges from `guardrail` nodes. On all four corpora `guards = 0` — there are no `guardrail` nodes for it to link. ADR-0016 decision 7 removed the class-A name heuristics (path rule, class suffix). `classify_class` / `classify_method` return `sink` / `agent` / `llm_endpoint` / `tool` and nothing else. The LLM classifier (D.2) can, via its `DetectionResult` merged at stage 10 (which is why `guardrail_edges` runs after the merge), but on the four corpora no source produces them.
**Consequence:** B.4a and B.4b are correct and ready; end-to-end guardrail influence does not fire until a producer exists.
**What does not close this gap:** re-adding the path rule or class suffix (class-A heuristics in another shape); LLM classifier as the only source (opt-in, off by default).
**What could close it (decision for a later planning session):**
- Capability-based detection (a class or function whose body calls a known filter / validator API). Needs new `CapabilityRule`s and corpus evidence.
- Framework detector (LangChain callbacks, FastMCP middleware, Guardrails AI, NeMo Guardrails). ADR-0011 route; a new detector (D.1 territory).
- LLM classifier as the only source. B.4 must not depend on it being on.
**Decision needed before any implementation:** which producer, with which corpus evidence. A new ADR records it.

#### B.4b-followup — `SOLO` step 4 and user prompt questions

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** `SOLO_SYSTEM_PROMPT` step 4 ("are they bypassable?") and the voter/solo/group user prompts ("are there guardrails on the path? are they bypassable?") ask a question the new rule says the payload cannot answer. Aligning those questions with ADR-0019 is a prompt iteration, not a code change.
**Size:** ~0.5 day.
**References:** ADR-0019 §2; B.4b.

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

### B.10 — Capability coverage for wrapped LLM clients and inherited capabilities

**Priority:** **High**
**Status:** Done (2026-10-29). Split into B.10a and B.10b.

#### B.10a — Qualifier families

**Status:** Done (2026-10-29). `CallPattern.qualifier_prefixes` (`llm`, `model`); `self.llm_client.chat` credits `calls_llm`; `model.generate` newly credited (unavoidable); `classification.py`'s `calls_llm` alone → `agent`.

#### B.10b — Capability inheritance

**Status:** Done (2026-10-29). `parsing/capability_inheritance.py`; `apply_inheritance` runs after per-file extraction, before `run_classifier`. Override does not block; transitive / method-level / multiple inheritance not done (ADR-0020 §4).

#### B.10c — `classification.py`'s `calls_llm` alone → `agent`

**Priority:** **Low**
**Status:** Not started. Documented in B.10a's brief error; the code is consistent with ADR-0009 (`llm_endpoint` is a config node, not a capability-derived class). No code change needed; the note is the whole item.

### B.11 — `max_depth` in `find_chain_groups`

**Priority:** **High** (post-release)
**Status:** Not started.
**Rationale:** `find_chain_groups(max_depth=1)` is the current enumeration; `max_depth=2+` (three-hop and longer) makes a multi-store chain (write → store → read → write → store → read) enumerable. Depth is a **parameter**, default 1; `max_depth=2` after the release, when the corpora show that depth 2 is real.
**Size:** ~1–2 days.
**References:** ADR-0021 §6; B.3a; B.3d (planned).

### B.12 — Sorting by depth before LLM evaluation

**Priority:** **Medium** (post-release)
**Status:** Not started. **Blocked by** B.11.
**Rationale:** When `max_depth=2`, the top-N sent to the LLM must weigh depth: a depth-1 group is cheaper to evaluate and simpler; a depth-2 group is more expensive but potentially more powerful. The prioritization (ADR-0012) is the consumer.
**Size:** ~1 day.
**References:** ADR-0012; B.11.

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
- `runtime/python/runner.py` — runs `runtime.test_command` from `evaluation: in config/config.yaml`, collects observations, invokes the stitcher.
- `evaluation: in config/config.yaml`: `runtime.mode: off | python`, `runtime.test_command`.
- `run_scan.py`: after writing the AST graph, if `runtime.mode == "python"`, run the runtime module and stitch.

**Size:** ~7–10 days.

**Risks:** the largest single piece of work. `sys.settrace` slows execution by 10–100×; tests must be small. The stitcher's decorator window (`MAX_DECORATOR_LINES = 5`) is a heuristic; functions with more decorators are logged, not stitched.

**References:** ADR-0017 §Decision.

### C.2 — Layer 2 log rotation on `--clean`

**Priority:** **Medium**
**Status:** Not started
**Depends on:** `evaluation: in config/config.yaml` (Phase A.2)
**Rationale:** `--clean` currently deletes `logs/` along with the rest of the state. Once `evaluation: in config/config.yaml` exists, log rotation becomes possible: current log → `aicorellator.log.<UTC ts>`, archives beyond `logs.keep_archives` (default 5) pruned. The `run_scan.py` flow must load `evaluation: in config/config.yaml` before `clean()`, which is a small change on top of A.2.

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

**Status:** Done (2026-10-27).

- `parsing/llm_classifier/` — 6 modules, 117 tests. `mode: off` default (literal opt-in; ADR-0016). Cache in `cache/llm_classifier.sqlite3` keyed on `(content_hash, prompt_version, model_id)`.
- Three provenance limiters: (1) `part_of_parent` → no node; (2) method inside an agent-class → no `agent` node, capability to parent; (3) `tool` inside `mcp_server` without a decorator → node created with `unverified_tool: true`.
- Limiters 1–2 only merge into LLM-created parents; deterministic parents are not enriched (post-classification stage, not D.2).
- `required` keeps only gaps (deterministic wins).
- `ask` = warning, not interactive. `interactive.py` not built.
- `PROMPT_VERSION = "v1.0.0"` in `llm_classifier/prompt.py`, byte-identical to `tools/bench_classifier.py`.

### D.3 — Prompt engineering

**Priority:** **Medium**
**Status:** Partially done (P0/P2 v1.2.0; v1.3.0 `flow_kind`; v1.4.0 sink_data_flow/returns_data; v1.4.1 test-file nodes; v1.5.0 group prompt; v1.6.0 guardrails). Full version blocked by Phase C.
**Rationale:** The prompt currently says "a chain with data edges is stronger evidence" — and on AST corpora such chains do not exist. The prompt teaches the model to ignore a signal that is never present. After Phase C, data edges exist, and the prompt can be grounded.

**Remaining work:**
- `observed_by` instructions: `["ast"]` vs `["ast", "runtime"]`.
- `uncertain` verdict with concrete criteria.
- Guardrail prompt follow-up (B.4b-followup).
- Prompt regression tests.

**Size:** ~1 day.

**References:** ADR-0016 §Positive 6; ADR-0019.

### D.3a — Fine-tune a compact AST classifier (LoRA on Qwen3-4B)

**Priority:** Medium (after D.2 + bench_classifier + 200–500 labeled symbols)
**Status:** Not started
**Rationale:** A general-purpose 4B model + few-shot is the first cut
(D.2). Fine-tuning is the second cut, after the benchmark shows *where*
the base model fails on real corpora. Training on a task that is not yet
precisely specified is training on noise.

**Prerequisites:**
1. D.2 works with a stock model + few-shot; `bench_classifier.py` shows
   the error profile on FinBot, NMB, dvmcp, mcploitable.
2. 200–500 labeled symbols. Labeling: manual for 50–100, consensus of
   three large models for the rest (disagreements resolved manually).
3. Prompt is frozen (the one D.2 ships) — fine-tuning is on the *task*,
   not on a prompt variant.

**Approach:**
- LoRA on Qwen3-4B-Instruct (or the winner of the benchmark), rank 16,
  alpha 32, target modules = attention q/k/v/o + MLP gate/up/down.
- Training data: `(symbol_source, file_path, imports) -> {kind,
  capabilities}`. ~1000 examples (200–500 labeled, plus augmentation
  from the corpora).
- Eval: held-out 20% of labeled symbols; report `kind_agreement` and
  `caps_agreement` per corpus. Compare against the base model + few-shot.
- Serving: same local endpoint (LMStudio/Ollama/vLLM), model id like
  `aicorellator/ast-classifier-v1`. `llm_classifier_model.model` in
  config.yaml points at it.

**Blast radius:** none to the codebase — the fine-tuned model is just a
model id. The prompt and the `DetectionResult` shape are unchanged. The
fine-tuned model ships as a GGUF / LoRA adapter alongside the repo, or
is downloaded on first run (decide later; not part of the codebase).

**References:** D.2 (LLM classifier), `bench_classifier.py` results,
B.10 (capability coverage for wrapped LLM clients — fine-tuning can also
learn this, but B.10 is the deterministic fix and should land first).

### Post-classification enrichment (from D.2)

**Priority:** Medium
**Rationale:** D.2 limiters 1–2 can only merge capabilities into
parents the classifier itself created. A deterministic parent
(built by `ClassNodeStage` / `MethodNodeStage`) is not enriched —
writing LLM capabilities into a deterministic classification is a
different operation, and it needs a post-`normalize()` stage. Not
required for D.2; record it so the limitation is visible.

### D.4 — Any-node semantics for impact sink capabilities

**Priority:** **Low**
**Status:** Not started
**Rationale:** `_score_read_sinks` walks only the last node of a read chain; `_score_read_writes_db` credits any node. Inconsistent on paper; no-op on the four corpora. On new corpora with `writes_db` mid-chain, scoring will systematically under-count.

**Approach:** widen `dangerous_sink` / `exfil_or_write` to any-node, or narrow `writes_db` to terminal-only. Prefer widening.

**Size:** ~0.5 day.

**References:** ADR-0012 table.

### D.5 — Guardrails — `bypassed_by` derived edge

**Priority:** **Medium**
**Status:** Not started. **Blocked by** B.4-gap (needs `guardrail` nodes and `guards` edges on a real corpus to test the reverse).

### D.6 — Guardrails — attributes

**Priority:** **High** (part of B.4-gap, in the longer term)
**Status:** Not started.
**Rationale:** Nodes exist without attributes. To know whether a guardrail blocks a chain, the graph needs to know what it filters and where it sits.
**Approach:** source-level analysis. `policy` ∈ `prompt_injection`, `pii`, `tool_allowlist`, `rate_limit`, `unknown`. `enforcement_point` ∈ `pre_tool_call`, `post_llm_response`, `pre_agent_step`, `unknown`.

---

## Doc-debt

All in the "do not change" sets of past steps; each is one line or one sentence. A single doc-debt commit clears them.

### `store/registry.py` — "empty in B.3b-core"

**Priority:** **Low**
**Status:** Not started.
**Rationale:** Docstring says the registry is empty in B.3b-core. After B.3b-fs it is not.

### `store/context/upload/registry.py` — "empty in B.3b-user_upload-core"

**Priority:** **Low**
**Status:** Not started.
**Rationale:** Same shape. After B.3b-user_upload-fastapi it is not.

### `store/resolver.py::_link_confidence` — case list

**Priority:** **Low**
**Status:** Not started.
**Rationale:** Docstring enumerates `literal_match`'s cases; it should mention the containment branch.

### `families/base.py` — `B.3b-user_upload` naming

**Priority:** **Low**
**Status:** Not started.
**Rationale:** Docstring names `B.3b-user_upload` as the first context; the actual first context is `B.3b-user_upload-core`.

### `store_resolver.py` — stale sentence + duplicate `__all__`

**Priority:** **Low**
**Status:** Not started.
**Rationale:** Lines 23–26 ("detector registry is empty in this step") are stale after B.3b-user_upload-fastapi. `__all__` appears twice (312 and 315, committed in `3dcec80`). One-line fix each.

### `flow_kind.py` inline comment + `ast_normalizer.py` edge-list header

**Priority:** **Low**
**Status:** Done (2026-10-29) — first doc-debt commit; the follow-up commit touched `flow_kind.py`'s inline comment and `ast_normalizer.py`'s edge-list header. `ast_normalizer.py`'s "fifteen → nineteen stages" also fixed.

### `family/base.py` — `B.3b-user_upload` and `B.3b-core` naming

**Priority:** **Low**
**Status:** Done (2026-10-29) — part of B.3b-user_upload-core's "left alone deliberately" flags; **not** actually fixed. The `B.3b-user_upload` naming in `base.py` is stale.

---

## Known gaps

Three gaps recorded during Phase B, none blocking, none papered over.

### B.4-gap — No producer of `guardrail` nodes

(see §B.4-gap above)

### B.3b-user_upload-attribution — `source_type='user_upload'` end-to-end

(see §B.3b-user_upload-attribution above)

### B.3b-fs prefix-inert — containment branch inert through the resolver

(see §B.3b-fs-followup above)

### B.3b-fs-followup — prefix containment through the resolver

(see §B.3b-fs-followup above)

---

## Open questions

These are not backlog items; they are decisions the next planning session should settle before the backlog is revised again.

### Resolved by ADR-0016 / ADR-0017 / ADR-0019 / ADR-0020 / ADR-0021

1. **Is `data_flow_confirmed` a promise, or enrichment metadata?** → **Resolved.** It is enrichment metadata. The AST layer does not answer the question; `data_flow_confirmed` is `NULL` on AST edges and populated only by Layer 2.
2. **Is `lethal_trifecta` a claim?** → **Resolved.** Yes, and it is valid on the AST layer. It is a node property, computed from capabilities. Runtime strengthens the path evidence around it, not the flag.
3. **Does validated: true mean "correct"?** → **Resolved** by A.4 (2026-10-27). `validated: true` now means "existence and semantic coherence" (connected, typed, anchor-valid); `validated_kind = "semantic"` is that state, `validated_kind = "existence"` is the pre-A.4 meaning kept for read-path compatibility. The reputation change is documented in ADR-0018.
4. **Is Phase 2 (runtime) planned at all?** → **Resolved:** yes, Phase C. The README and ADR-0003 have been updated to say so. ADR-0017 specifies the stitching.
5. **Guardrail influence: exclusion, marking, or weighting?** → **Resolved** by ADR-0019. Weighting; the model lowers confidence; no exclusion, no marking.
6. **Capability inheritance: from base class, one level?** → **Resolved** by ADR-0020. Yes; override does not block; transitive and method-level are separate ADRs.
7. **Store model: node, edges, confidence?** → **Resolved** by ADR-0021. A `store` node, `writes_to` / `reads_from` as structural edges, `identity_confidence` as a second field, a join by store id in `find_chain_groups`.
8. **`user_upload`: family or source type?** → **Resolved** by ADR-0021 §1 (revision 1). Source type, not family. The store is the technology (`fs`, `s3`, `db`); `user_upload` is the semantic role.
9. **Context-based writer base confidence?** → **Resolved** by ADR-0021 §8 (revision 2). 0.5, not 0.3.

### Still open

10. **Should `provides_tool` chains survive `data_edges_only=True`?** → **Still open.** The parameter is named "data edges only" and `provides_tool` is control — by construction, MCP chains vanish on the shortlist. Either the parameter is misnamed, or MCP is not a first-class use case. Decide after Phase C, when runtime produces real data edges and the question can be answered with data.
11. **What is the identity of an `untrusted_source` node when two read sites are the same store?** → **Still open.** B.8 will produce a short ADR or documentation note.
12. **`source_type` for `user_upload` end-to-end: (a) new node, (b) multi-role?** → **Open** (B.3b-user_upload-attribution). A single decision before the attribution can fire.
13. **Prefix containment through the resolver: `_attached_keys` change.** → **Open** (B.3b-fs-followup). The containment branch is inert; making it live is a rule, not a one-liner.
14. **`max_depth` default after release: 1 or 2?** → **Open** (B.11, post-release). Depends on the corpora.

---

## Roadmap at a glance

| Phase | What | Stops at |
|---|---|---|
| A | Implement ADR-0016 and ADR-0017 (schema, `evaluation: in config/config.yaml`, class-A removal, Layer 3 semantic) | v0.2 — the graph is honest |
| B | Close the coverage gaps (cross-file tool → sink, write-side, guardrails, store model, capability coverage, judge determinism, evidence presence) | v0.3 — coverage gaps closed |
| C | Layer 2 runtime (Python) | v1.0 — `data_edges_only > 0` on a real corpus |
| D | Resume the roadmap (`openai_agents`, LLM classifier, prompt engineering) | v1.1 — framework-agnostic, grounded prompt |