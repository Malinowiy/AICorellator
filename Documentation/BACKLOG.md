# AICorellator — Backlog

Single registry of deferred, in-progress, and planned work. Complements the ADRs (which record *decisions*) with a working list of *what to do next*.

**Last updated:** 2026-10-30
**Revision:** restructured around ADR-0016, ADR-0017, ADR-0019, ADR-0020, ADR-0021. Phase B in progress: B.1, B.2, B.3a–B.3c, B.3b-core, B.3b-fs, B.3b-s3, B.3b-db, B.3b-user_upload-core (with `-module`, `-module-followup`, `-route-detectors-followup`), B.3b-user_upload-fastapi / -streamlit / -django / -flask, B.3b-mcp-catalogue, B.4a, B.4b, B.10a, B.10b done (2026-10-30). Outstanding in B.3: B.3b-llm_fallback, B.3b-fs-followup, B.3b-user_upload-attribution.

---

## 0. Direction and priorities

### 0.1. The diagnosis this revision is built on

The previous revision (2026-10-25) was built on ADR-0016 and ADR-0017: the AST layer is control-only, `observed_by` is on every node and edge, `data_edges_only = []` on an AST scan is the correct answer. Three ADRs have been accepted since and shape the current direction:

1. **ADR-0019 — guardrail emission and evaluation weighting.** `guards` is emitted from source-level evidence (call site, `@guardrail(...)` decorator) and reaches the LLM as a `guardrails` payload section, not as an enumeration filter. B.4a and B.4b implement it; end-to-end is blocked by B.4-gap (no producer of `guardrail` nodes after ADR-0016).
2. **ADR-0020 — capability inheritance from a scanned base class.** A subclass of a scanned base inherits the base's own capabilities, one level, cycle-safe. B.10b implements it; the FinBot-shaped hierarchy (a `BaseAgent` that calls `self.llm_client.chat(...)`, subclasses that call `_run_agent_loop`) now produces an `agent` node for the subclass, structurally.
3. **ADR-0021 — store as a graph entity, with probabilistic write/read links.** A `store` is a place write and read call sites point at; `writes_to` / `reads_from` are structural edges with an `identity_confidence` computed from signals (family, literal, code context). Two revisions: §1 (family = technology, `user_upload` is a source type), §8 (base 0.5 for a context-based writer). B.3a (mechanism), B.3b-core (family interface, registry, resolver), B.3b-fs / B.3b-s3 / B.3b-db (the three families), B.3b-user_upload-core (upload detector interface, context, writer logic) with the four detectors (B.3b-user_upload-fastapi / -streamlit / -django / -flask), and B.3c / B.3b-mcp-catalogue (the B.2 and MCP writers migrate to the store) implement it.

Four facts from the 2026-10-30 state:

1. **Phase B is closing the coverage gaps.** B.1 (cross-file `tool → sink`), B.2 (write-side for `rag_corpus` / `email_inbox`), B.4a / B.4b (guardrails), B.10a / B.10b (capability coverage + inheritance), B.3a / B.3b-core / B.3b-fs / B.3b-s3 / B.3b-db / B.3b-user_upload-core / B.3b-user_upload-fastapi / -streamlit / -django / -flask / B.3c (store model) are done.
2. **The store model is the largest new subsystem since Phase A.** A `store` node kind, `writes_to` / `reads_from` as structural edges, `identity_confidence` as a second confidence field, a store-family registry and a store-context registry, and a join in `find_chain_groups` by store id. The **three families** (`fs`, `s3`, `db`) and the **four upload detectors** (FastAPI, Streamlit, Django, Flask) are shipped; **three known gaps** (B.4-gap, B.3b-user_upload-attribution, B.3b-fs prefix-inert) are recorded and not papered over.
3. **The `max_depth` dimension is not yet implemented.** `find_chain_groups(max_depth=1)` is the current enumeration; `max_depth=2+` (three-hop and longer) is a post-release step. Depth is the parameter that makes a multi-store chain (write → store → read → write → store → read) enumerable.
4. **Layer 3 validates existence, semantics, and now (via B.9) will validate evidence presence.** B.9 remains a Medium item.

Plus two coverage gaps that Phase B has not yet closed:

- **`guardrail` node producer (B.4-gap).** No deterministic layer produces `guardrail` nodes after ADR-0016; B.4a's emission is correct and tested but returns zero on the four corpora. The decision (capability rule / framework detector / LLM-only) is B.4-gap.
- **`user_upload` attribution end-to-end (B.3b-user_upload-attribution).** The attribution logic works (unit-tested), but an `fs` read is not one of 1.3a's source types, so the reader has no `untrusted_source` node to stamp with `source_type='user_upload'`. A multi-role `source_type` rule or a producer of `untrusted_source` for `fs` reads in the writer's scope is the decision.

### 0.2. The direction this revision sets

**Fix the foundation, then build; close coverage gaps family by family.**

- **Phase A — done.** ADR-0016 and ADR-0017 implemented; Layer 3 semantic validation (A.4) done.
- **Phase B — in progress.** Cross-file `tool → sink` (B.1), write-side for `rag_corpus` / `email_inbox` (B.2), guardrail emission and weighting (B.4a, B.4b), capability coverage and inheritance (B.10a, B.10b), store model (B.3a, B.3b-core, B.3b-fs, B.3b-s3, B.3b-db, B.3b-user_upload-core, B.3b-user_upload-fastapi / -streamlit / -django / -flask, B.3c) are done. Remaining in B.3: B.3b-llm_fallback (LLM identity fallback) and the two open followups (B.3b-fs-followup, B.3b-user_upload-attribution). Remaining in the phase: B.4-gap, B.5 (judge determinism), B.9 (evidence presence).
- **Phase C — next.** Layer 2 runtime for Python. The only path to `data_edges_only > 0` on a real corpus.
- **Phase D — then.** Resume the roadmap: `openai_agents` detector, prompt engineering on a graph where the data exists, LLM classifier fine-tune.

**What is explicitly *not* planned in this cycle:** TUI (Icebox), new eval modes, multi-host, standalone executables, SCIP, schema migrations, non-Python runtime, `max_depth > 1` (post-release).

### 0.3. Top 10 by impact

| # | Item | Priority | Why it is here |
|---|---|---|---|
| 1 | **Cross-file `tool → sink`** (B.1) | **High** | **Done.** Enables real paths through imports. |
| 2 | **Guardrails affect evaluation** (B.4a + B.4b) | **High** | **Done.** Guardrails enter the payload, not the traversal. End-to-end blocked by B.4-gap. |
| 3 | **Store model** (B.3a, B.3b-core, B.3b-*) | **High** | **Done** (2026-10-30). A `store` node, `writes_to` / `reads_from` with `identity_confidence`, a family registry (three families), a context registry (four detectors), and the B.3c / B.3b-mcp-catalogue migration. |
| 4 | **Upload detectors** (B.3b-user_upload-fastapi / -streamlit / -django / -flask) | **High** | **Done** (2026-10-30). Four real `UploadDetector`s on one interface (signature, module-level entry, body). |
| 5 | **Write-side for `rag_corpus` / `email_inbox`** (B.2) | **Medium** | **Done.** Both families have a writer; migrated to the store model in B.3c (2026-10-30). |
| 6 | **`guardrail` node producer** (B.4-gap) | **High** | B.4a and B.4b are correct but return zero until a producer exists. A decision, not a fix. |
| 7 | **Upload detectors for Streamlit / Django / Flask** (B.3b-user_upload-streamlit / -django / -flask) | **Medium** | **Done** (2026-10-30). Three more detectors on the same interface, plus the route-shape followup in `entry_points.py`. |
| 8 | **`s3` / `db` families** (B.3b-s3, B.3b-db) | **Medium** | **Done** (2026-10-30). The registry now ships three (`fs`, `s3`, `db`). |
| 9 | **Store migration of B.2** (B.3c) | **Medium** | **Done** (2026-10-30). `writes_to` targets `store` for every family; the read side is unchanged. |
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

### 2026-10-31 — B.6 + B.7: `chain_groups.lethal_trifecta` writer, bundle shape, and the provenance columns

- B.6's writer and the bundle keys land in `_evaluate_one_group` / `_group_to_dict`; B.7 adds the evaluation provenance the `chains` table has had since ADR-0005/0006. The two steps share three files (`evaluator.py`, `chain_groups.py`, `bundle.py`) and are the same shape — row column → bundle key, "not computed" on an unevaluated row — so they land as one commit.
- B.7: `chain_groups` gains `voter_outputs JSON NOT NULL DEFAULT '[]'` and `prompt_version TEXT NOT NULL DEFAULT ''` (placed after `reasoning`, `chains`' order) plus `CHECK (json_valid(voter_outputs))`; a new `schema_version` row 4 records it and **no ALTER is issued** (ADR-0003 — the DB is rebuilt per scan, `run_scan.py --clean`).
- `ChainGroupRecord` gains both fields with defaults (`Chain` untouched); `to_row` / `from_row` and `add` / `upsert` carry them — `upsert` writes them **inside** the `if group.verdict is not None` gate, the same rule as `validated` / `validated_kind` / `lethal_trifecta`.
- The group path writes `prompt_version = PROMPT_VERSION` (still v1.6.0) and `voter_outputs` through `_voter_response_dict(r, group_edge_ids)` — **the group's own edge set**, the set Layer 3 already judges citations by, so `evidence_valid` answers the group's question and not the graph's. `raw` is stripped inside the helper.
- `bundle.py::_group_to_dict` emits `"prompt_version"` and `"voter_outputs"` always; an unevaluated row emits the stored `""` / `[]` — one notch past B.6, because the two new columns have defaults to mirror.
- **Decision (B.6):** `validated_kind` in `chain_groups.json` stays gated on `verdict is not None` because `from_row` coerces a NULL to `"existence"` (a chain-row convenience, not a group-row fact); the bundle mirrors the stored column. B.7 needs no such gate — `from_row` substitutes nothing for the two new columns.
- **Tests:** +4 (the repository round-trip and `add`'s defaults, the evaluator's row write pinned on the group's edge set, the schema's PRAGMA defaults and the `json_valid` CHECK; two B.6 bundle tests renamed for `""` / `[]`). 2335 passed, 0 errors, 0 warnings.
- **Not run:** the four-corpus regression (corpora not on this machine). Storage and bundle shape only.

### 2026-10-31 — B.6: `chain_groups.lethal_trifecta` writer, and the bundle's two row columns

- `_evaluate_one_group` projects the read side's flags onto the record: `any(flag for chain in payload["read_chains"] for flag in chain["lethal_trifecta"].values())` — `_group_payload`'s own map (no second store read), the node set exactly `group.read_chains` with the anchor included (the same set as `graph/query.py::_score_lethal_trifecta`). The **write side is not read**: the attacker's content enters through the read, and a trifecta node upstream of the write is another chain's problem.
- `_group_to_dict` gains `"lethal_trifecta"` and `"validated_kind"`, closing the bundle's half of the same gap. **No separate item is opened for the bundle**: the two keys land in B.6 together, and `prompt_version` / `voter_outputs` wait for B.7's schema change.
- `chain_groups.py`: module docstring only — "no writer yet" removed, the writer named, `upsert` does not refresh the column (fine: the graph is rebuilt per scan, and the next scan's `add` is a fresh INSERT). `add` / `upsert` / `list` / `get` otherwise byte-identical.
- **Tests:** +4 (evaluator_groups +2, reporting +2, one pipeline assertion). 2331 passed, 0 errors, 0 warnings.

### 2026-10-31 — B.4b-observed-by: the guardrail payload carries `observed_by`

- `_guardrail_to_dict` returns `observed_by`, in `_node_to_dict`'s order (`id`, `kind`, `name`, `capabilities`, `observed_by`, `attributes`). Before this, a voter reading two guardrails on one chain saw two identical-looking objects; the field is the only thing that tells the three producers apart (`["ast"]`, `["ast", "framework:<name>"]`, `["ast", "static_llm:<model_id>"]`, ADR-0024 §6).
- `_guardrails_for_group` gains it through `_guardrails_for_chain`; the `{write, read, anchor}` partition is untouched.
- The docstring is **rewritten, not extended**: the confidence rationale stays (it is the chain-node exploitability estimate), the `observed_by` rationale is new.
- **Tests:** +1 (`_add_guardrail` generalised to take `observed_by`, so each test's provenance is an assertion and not an accident of inheritance). 2327 passed.

### 2026-10-31 — ADR-0019 revision 1: `mcp_server` as a guards target, and its payload read

- The decision, not the code. `COMPONENT_KINDS` grows to `{"agent", "tool", "mcp_server"}` with its own evidence rule (an `add_middleware(...)` call site on the server object, or a `@mcp.middleware(...)` decorator on the factory, read from the server's own module AST), and `_guardrails_for_chain` also reads a `guards` edge on a `tool`'s `parent_server`.
- Co-location stays non-evidence; ADR-0007's table is not contradicted — a `mcp_server` is the container its tools live in, and a middleware that runs on the server protects every tool it exposes.
- The code lands as **B.4-gap-5-followup** when the four corpora return; the corpus regression is deferred, not skipped.

### 2026-10-30 — Delete `aicorellator_dump.txt`

- The 85,163-line dump file `dd2bfa5` added by accident (that commit's `--stat` shows 86,367 insertions) is removed from the tree; the deletion entered the mainline through the 2026-10-31 merge, which carries nothing else. No source change, no test.

### 2026-10-30 — B.4-gap-5: FastMCP middleware detector

- The fourth and last guardrail-SDK detector on ADR-0024 §3's plan: a class whose base is `Middleware` (or a dotted equivalent) becomes a `guardrail` node, `observed_by=["ast", "framework:fastmcp_middleware"]`.
- **Node-only.** A middleware is a hook, not a component the guard wraps; `guardrail_edges` has no visible component to link and emits nothing for a middleware node. All `Middleware` subclasses are accepted — an audit middleware is a weak guardrail, and the payload is a signal, not a filter (ADR-0024 §2). The negative capability `validates_input` cannot fire here: a middleware's hook is called by the framework, not from an agent body.
- A middleware registered on a server (`mcp.add_middleware(...)`) is a node and **no** `guards` edge — the pinned behaviour that became ADR-0019 rev 1.
- `parsing/framework_detectors/fastmcp_middleware.py`; registered in `registry.py`.
- **Tests:** +32 (test_framework_fastmcp_middleware 26, test_fastmcp_middleware_detector 6). 2326 passed.

### 2026-10-30 — B.4-gap-4: LangChain callbacks detector

- The third guardrail-SDK detector: a class whose base is `BaseCallbackHandler` (or a dotted equivalent) becomes a `guardrail` node, `observed_by=["ast", "framework:langchain_callback"]`.
- **Node-only**, for B.4-gap-5's reason: a callback is a hook into the LLM / tool lifecycle, and `guardrail_edges` has nothing to link. All subclasses are accepted — the AST cannot tell a validating hook from a logging one, and the capability rule cannot fire either (the framework calls the hook, not an agent body).
- `parsing/framework_detectors/langchain_callback.py`; registered in `registry.py`.
- **Tests:** +37 (test_framework_langchain_callback 32, test_langchain_callback_detector 5). 2294 passed.

### 2026-10-30 — B.4-gap-3: NeMo Guardrails detector

- The second guardrail-SDK detector: `LLMRails(...)` constructions become `guardrail` nodes, `observed_by=["ast", "framework:nemo_guardrails"]`. Fast path `_has_import_prefix(tree, "nemoguardrails")` — four import forms; no import, no detection.
- **Node-only:** an `LLMRails` wraps a generation call at runtime, so the AST has no static component the rails "guard".
- `parsing/framework_detectors/nemo_guardrails.py`; registered in `registry.py`.
- **Tests:** +50 (test_framework_nemo_guardrails 45, test_nemo_guardrails_detector 5). 2257 passed.

### 2026-10-30 — B.4-gap-2: Guardrails AI detector

- The first guardrail-SDK detector on ADR-0024 §3's plan: `Guard(...)`, `Guard.from_pydantic`, `Guard.for_string` and `Guard.for_rail` constructions become `guardrail` nodes, `observed_by=["ast", "framework:guardrails_ai"]`. Fast path `_has_import_prefix(tree, "guardrails")`.
- **Node-only:** a `Guard` wraps an LLM call at runtime, so B.4a's `guardrail_edges` has nothing to link and emits nothing for it; the node joins the guardrail set the way a capability node does.
- `parsing/framework_detectors/guardrails_ai.py`; registered in `registry.py`.
- **Tests:** +45 (test_framework_guardrails_ai 40, test_guardrails_ai_detector 5). 2207 passed.

### 2026-10-30 — B.4-gap-1: capability `validates_input` and the guardrail kind

- ADR-0024 §2's first producer: a **negative** capability `validates_input`, credited on a function or method called from an agent / tool / entry-point body and carrying none of SINK / AGENT / LLM_ENDPOINT / TOOL. `CLASSIFYING_CAPABILITIES` is the one definition of that negative set — producer and classifier both read it.
- `classification.py`: `sink → guardrail → tool → layout`, the guardrail branch gated on `caps & GUARDRAIL_CAPABILITIES and not caps & CLASSIFYING_CAPABILITIES` — the gate that resolves a brief contradiction ("before sink" vs "sink → guardrail → tool"); every pinned outcome holds. `classify_class` unchanged (a class is not a guardrail; the method is).
- New `parsing/normalizer_stages/guardrail_capability.py` (stage 2, between `mcp_server_nodes` and `class_nodes`): writes `validates_input` into `ctx.capabilities` for a method / function whose caller in the call graph has `calls_llm` / `invokes_mcp_tool` / `reads_untrusted`. No node, no edge — the node comes from the classification. The caller kind is capability-based because at stage 2 the agent / tool / entry nodes do not exist yet.
- **Known:** `logger.info` is unreachable as written — `_collect_call_names` records `ast.Name` targets only, so ADR-0024 §2's `logger.info(text)` example is a wording issue, not a rule issue. Both halves are pinned: the accepted false positive (a capability-free helper called by bare name) and the documented miss (an attribute call — the limitation B.4a notes for `self.pii.check(x)`).
- **Tests:** +24 net (test_guardrail_capability 14, test_classification +6, test_patterns +2, test_stage_order +2; the stage docstrings move from "N of 20" to "N of 21"). 2162 passed.

### 2026-10-30 — ADR-0024: the guardrail node producer

- Three producers, one precedence: a **framework detector** (ADR-0011 route, per-SDK module — the SDK's own convention is the strongest evidence); the negative **capability `validates_input`** (second: structural, deterministic, an inference); the **LLM classifier** (D.2, opt-in, off by default — the fallback for projects no detector and no capability covers).
- The rule is **not a name table**; ADR-0016 decision 7 stays removed. The capability rule reads the call graph and the capability map and accepts its false positives (`print`, `logger.info`), because the guardrails payload is a signal the model weighs (ADR-0019 §2), not a filter.
- §3 also names `@guard`, `guard.parse` and `guard.validate`; the detectors ship the **constructions** only.

### 2026-10-30 — B.3b-llm_fallback: LLM fallback for unresolved store identity

- ADR-0023's opt-in fallback: a site with `identity_hint=None` (an `open(path)`, a `Bucket=var`, an f-string with no literal prefix, an unresolved handle) in a writer's scope gets an LLM-proposed `identity_hint`; the fallback creates the store and its `writes_to` / `reads_from` edges with `identity_source='llm'` and `identity_confidence=0.2`, and the store is weak (special floor 0.1, `include_weak=True` to inspect). Deterministic answers win — the fallback fills the gap, it is not the primary producer.
- New `store/llm_fallback/` (config, prompt, cache, runner): its own `PROMPT_VERSION` (`store_identity:v1.0.0`), its own config section (`llm_fallback_model`, temperature 0.0, defaulting to the classifier's model), and D.2's SQLite cache keyed on `(content_hash, prompt_version, model_id)`.
- **Tests:** +139 net (124 new in `test_store_llm_fallback`, 7 stage, 2 query-layer, 2 whole-pipeline, `scan_config` additions). 2138 passed.
- **Not run:** the four-corpus regression (corpora not on this machine); ruff / mypy not installed.

### 2026-10-30 — ADR-0023: LLM fallback for unresolved store identity

- ADR-0021 §5's rule ("unresolved identity → no store, no edge") is correct but silent: the analysis cannot distinguish "no store" from "a store the AST could not name". The decision is an **opt-in** LLM fallback, the last resort after the AST and the resolver's signals.
- Recorded: the model section, the prompt and its own `PROMPT_VERSION`, the shared cache and its key, base confidence 0.2 (`identity_source='llm'`), and where it runs (the store resolver stage, the writer's scope).

### 2026-10-30 — B.5 + B.9: judge determinism and evidence presence

- **B.5:** `scan_config._DEFAULT_CONFIG_YAML`'s judge entry moves from `temperature: 0.6` to `0.0`, with the trade-off stated (deterministic verdicts by default; raise it for a sampling judge; a per-model temperature is an override and is honored as written). `judge.evaluate` and `_parse_model_entry` untouched — a config default, not a function change; `analysis/config_loader.py`'s sample and docstring follow.
- **B.9:** `evaluator._evidence_present(voter_responses, candidate_edges)` — at least one voter cited at least one edge **from the candidate chain**, not from the graph. A pass sets `validated_kind = "evidence"`; a failure sets `validated = False`, `validated_kind = "semantic"` (the last level passed) and one note ("no voter cited an edge from the chain", plus the count of citations outside, or "no voters responded"). A `validate_chain` failure short-circuits — the evidence check is not reached.
- Schema: both `validated_kind` CHECKs gain `'evidence'` (the group path never writes it — B.4b's check is unchanged); `VALID_VALIDATED_KINDS` gains it so the mirror of the CHECK stays true. No migration (ADR-0003).
- **Tests:** +15 net (config 27, judge 8, evaluator 26, semantic_validation 43, evaluator_groups 16). 1999 passed.

### 2026-10-30 — ADR-0018 revision 2: evidence presence as the third `validated_kind` level

- §1/§2 defined `validated` as existence + semantic coherence, `validated_kind ∈ {"existence", "semantic"}`. B.9 adds a third level — **evidence presence** — so `validated` is true when all three pass and `"evidence"` becomes the highest `validated_kind`.
- The read path is unchanged (`NULL → "existence"`, `"semantic" → "semantic"`); a failure is stored as `validated: false`, `validated_kind: "semantic"` plus the note. `chain_groups.validated` stays B.4b's check, which already folds the voters' evidence.
- A revision, not a new ADR: §1 already names "existence" and "semantic". **Risk recorded:** chains stored before B.9 with `validated: true` become retroactively weaker — the DB is recreated per scan, so this is a reputation change, not a migration.

### 2026-10-30 — B.3b-user_upload-core-module-followup-2: module-level writer's reach

- The module-level branch's start set is now the **module body's own `ast.Name` call targets**, not the module's list of top-level functions. That fixes the false positive the mixed fixture pinned: a Streamlit upload site in a file that also holds a FastAPI route handler no longer inherits the route handler's store.
- `_module_body_calls` walks `module.body`, skips `_SCOPE_NODES` (`FunctionDef` / `AsyncFunctionDef` / `ClassDef` / `Lambda`) and collects the `ast.Call` targets that are `ast.Name`; `_reach` unions `_reachable(name, call_graph)` over them. `_top_level_functions` is removed (no other caller). The writer keeps its own `_reachable` — `store/` does not reach into `parsing/`. Module-body writes are unchanged.
- CLI (`entry_kind="cli"`) gets the same rule, pinned on an `if __name__ == "__main__"` fixture; the attribute-call false negative is pinned too.
- **Decision:** the module-level branch is a **call-graph reach from the module body**, not "everything top-level in the file" — the mixed-file false positive is the reason.
- **Tests:** +5 net (writer_module 59, streamlit 42, upload_core 99, normalizer + pipeline 285); the fixtures followed the rule, and the Streamlit E2E test that pinned the false positive is renamed and now asserts `found == []`. 1984 passed.
- **Not run:** the four-corpus regression (corpora not on this machine); ruff / mypy not installed.

### 2026-10-30 — B.3b-s3-followup: handles, `s3fs`, MinIO

- **`store/families/s3.py` gains three shapes the literal receiver set could not see.** `s3_client = boto3.client('s3')` then `s3_client.put_object(...)`; `fs = s3fs.S3FileSystem()` then `fs.open('s3://<bucket>/<key>', mode)`; `mc = Minio(...)` then `mc.put_object(bucket, key, ...)` / `mc.get_object(bucket, key)`.
- **Handle tracking is dataflow-lite, not dataflow.** One `Name = Call` per name per scope whose target is a constructor; the receiver's *name* is looked up, no value is followed (ADR-0001). Module-level handles are visible from any call site in the file, function-local ones only in their own function (a handle assigned in another function is a false negative — the family builds no call graph). Two assignments to one name in one scope: the last in source order wins. The literal receiver set and the handle sets are **additive and independent**, so `client = some_factory(); client.put_object(...)` is still a site by the literal rule.
- **The discriminator between boto3 and MinIO is the argument layout, not the receiver name** — the same `put_object` name belongs to both. boto3 is keyword-only (`Bucket=` / `Key=`, no positional), MinIO is positional `(bucket, key)` or lowercase `bucket=` / `key=`; a call carrying the other spelling is not a site. `s3fs` paths must be `s3://...` and the scheme is stripped for the hint, so `fs.open('s3://docs/x')` and `s3.get_object(Bucket='docs', Key='x')` name one store.
- **Documented false positives / negatives:** `boto3.client('ec2')` (the service argument is not checked), and the unresolvables — a factory handle, a handle from another function, a direct `s3fs.S3FileSystem().open(...)`, and a bare `open('s3://...')` (which is the `fs` family's site).
- **Tests:** +30 (test_s3 +27, test_s3_resolver +3). 1979 passed.
- **Not done, flagged:** the four corpora are not on this machine, so the corpus regression was not re-run.

### 2026-10-30 — Doc-debt ×3: docstrings after B.3b-* and B.3c

- Three doc-debt commits (six stale docstrings, then four, then seven) — docstrings and one C4 table row only. No behaviour, no test, no schema, no ADR.
- `store/context/upload/__init__.py` and `store/context/upload/registry.py`: "two names" → the four the tree ships (`fastapi`, `streamlit`, `django`, `flask`); the "empty until a detector is imported" clause kept verbatim (still B.3b-user_upload-core's contract).
- `store/families/fs.py` / `s3.py` / `db.py` and `store/families/base.py`: `B.3b-user_upload` → `B.3b-user_upload-core`.
- `store/registry.py`: verified already true ("three names: `fs`, `s3`, `db`") — unchanged.
- `store/resolver.py::_link_confidence`: the case list now names the containment branch (`LITERAL_PREFIX`, +0.3, B.3b-fs's row).
- `parsing/normalizer_stages/store_resolver.py`: the duplicate `__all__` removed (one is left, line 313).
- `graph/query.py`: the write-side paragraph and the code comment said "no producer yet" and "B.3c removes the first path". All three `writes_to` producers target a store now, so the source-anchored join still runs and still fires where a `writes_to` lands on the source itself, and the store join is where the groups come from.
- `Documentation/Architecture/C4_L3_Phase1.md`: the stage table is 20 rows 1–20 — `store_resolver` added as 19, `guardrail_edges` renumbered 20, and the "19 stages" counts now say 20.
- **Tests:** 1907 passed, 13 stage-order. Unchanged.
- **Still stale, flagged:** `store/context/__init__.py` ("the detectors the tree ships — `fastapi` … and `streamlit`") and `store_resolver.py`'s "two names today" sentence.

### 2026-10-30 — B.3b-user_upload-route-detectors-followup: Django and Flask route shapes in `entry_points.py`

- `_extract_route` recognizes four shapes: a call on an attribute whose last segment is an HTTP method or `websocket` (`@router.post(...)`, `@app.post(...)`); the same with `route` as the last segment (`@app.route(..., methods=[...])`, method = first name in `methods=`, GET when absent); a bare name Django guard (`@require_POST`); a call on a name taking a literal list (`@require_http_methods([...])`, DRF's `@api_view([...])`).
- A Django view decorated the documented way therefore produces an entry point, and the Django detector's name match finds it.
- A path-less route is named `"<METHOD> <handler>"` — `"POST "` names nothing, and the pattern lives in `urls.py`.
- **Tests:** +23 (test_entry_points +12). No behaviour change for the shapes already handled.

### 2026-10-30 — B.3b-user_upload-flask: fourth upload detector

- **New `store/context/upload/detectors/flask.py`.** Fourth and last detector; `django`'s shape with `request.files` for `request.FILES` (subscript and `.get`).
- Both of Flask's spellings (`@app.post(...)`, `@app.route(..., methods=[...])`) are routes since the route-shape followup.
- **Tests:** +56 (test_store_upload_flask 56, three forced registration edits). 1884 passed.

### 2026-10-30 — B.3b-user_upload-django: third upload detector

- **New `store/context/upload/detectors/django.py`.** Function-level, and the marker is the handler's **body** (`request.FILES["file"]`, `request.FILES.get("file")`) rather than its signature.
- **Tests:** +53 (test_store_upload_django 53, one forced edit). 1828 passed.

### 2026-10-30 — B.3b-mcp-catalogue: `mcp_tool_description` migrates to the store model

- The MCP catalogue writer emits `writes_to → store`, the B.3c shape applied to the third source type; the direct shape is still read for hand-built deltas.
- Two latent fixes found on the way: `dynamic_mcp_invocation` and `evaluator._chain_edges`.
- **Tests:** +8 net (17 mcp tests re-pointed, plus new store-identity tests). 1775 passed.

### 2026-10-30 — B.3c: B.2 migrates to the store model

- `rag_corpus` and `email_inbox` writers emit `writes_to → store`; the store join in `find_chain_groups` produces their groups.
- The read side is **unchanged**: `untrusted_source` remains the read site, `reads_from` connects the store to it, and the source-anchored join still exists (it fires where a `writes_to` lands on the source itself).
- **Tests:** +10 net (test_untrusted_source_writers re-pointed, three query-layer group tests added). 1767 passed. Four stale test names corrected.

### 2026-10-30 — B.3b-db: third family

- **New `store/families/db.py`.** SQLAlchemy / asyncpg call sites; literal table extraction (a simple regex, not a SQL parser).
- **Tests:** +51 (test_db 40, test_db_resolver 11, three forced assertion updates). 1757 passed.

### 2026-10-30 — B.3b-s3: second family

- **New `store/families/s3.py`.** `put_object` / `get_object`, literal `Bucket` / `Key`.
- **Tests:** +40 net (family + resolver). 1706 passed. `test_store_core/conftest.py` autouse-empties the registry, so the family tests get their own conftest — `fs` is split the same way.

### 2026-10-30 — B.3b-user_upload-streamlit: second upload detector

- **New `store/context/upload/detectors/streamlit.py`.** Module-level: `st.file_uploader` in an entry point whose body is the module.
- Asked the question B.3b-user_upload-core's decision #5 left open (a module-level entry got no writer edge) and answered it by extracting the module-level branch into B.3b-user_upload-core-module.
- **Tests:** +41 (test_store_upload_streamlit 41). 1666 passed.

### 2026-10-30 — B.3b-user_upload-core-module + -followup: the module-level entry shape

- The writer gains a module-level branch: `entry_kind` `"cli"` / `"streamlit"` → the module's top-level functions (BFS from each) and the writes directly in the module body. A write in the module's own body is weaker evidence (the module is a file) and carries base **0.4** (`UPLOAD_WRITER_BASE_MODULE`) instead of 0.5.
- Followup: the selector is **`entry_kind`**, not `entry_points`' `<module>` sentinel — the sentinel is on the `EntryPoint` record and the node has no `handler` at all, so a branch keyed on the sentinel could not fire on a real scan.
- **Tests:** +3 net (54 core-module, replacing the gap test). 1624 passed.

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
**Status:** Done (2026-10-29). Migrated to the store model in B.3c (2026-10-30).
**Rationale:** 1.3a.1 said RAG corpora and mailboxes had no writer evidence; only `mcp_tool_description` had a write side.
**What changed:** two new capabilities (`writes_rag_corpus`, `writes_email_inbox`) and two writer rules; `WRITER_RULES` table next to `UNTRUSTED_SOURCE_RULES`; `untrusted_writer_match`. Anchor rule: writer is the node the delta holds for the symbol that credited the capability; visible from the source by same-file or shared-import.
**Size:** ~1 day.
**References:** ADR-0010; ADR-0015 decision 5; ADR-0021.

### B.3 — Store model (the largest Phase B step)

**Priority:** **High**
**Status:** Done (2026-10-30). B.3a, B.3b-core, B.3b-fs (+ `-followup`), B.3b-s3 (+ `-followup`), B.3b-db, B.3b-user_upload-core (+ `-module` / `-module-followup` / `-module-followup-2` / `-route-detectors-followup`), B.3b-user_upload-fastapi / -streamlit / -django / -flask, B.3b-mcp-catalogue, B.3b-user_upload-attribution (+ its two followups), B.3b-llm_fallback, B.3c — all landed.

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

#### B.3b-user_upload-core-module — The module-level entry shape

**Priority:** **Medium**
**Status:** Done (2026-10-30).
**Rationale:** B.3b-user_upload-core's decision #5 left a module-level entry point (a Streamlit script, a CLI script) with no writer edge, so an upload at module level could not produce one.
**What changed:** `store/context/upload/writer.py` gains the module-level branch — the module's top-level functions (BFS from each) and the writes directly in the module body. A write in the module's own body is weaker evidence (the module is a file) and carries `UPLOAD_WRITER_BASE_MODULE` (0.4) instead of 0.5.
**Size:** ~1 day.
**References:** B.3b-user_upload-core; B.3b-user_upload-streamlit.

#### B.3b-user_upload-core-module-followup — The marker is `entry_kind`

**Priority:** **Low**
**Status:** Done (2026-10-30).
**Rationale:** The branch was first keyed on `entry_points`' `<module>` sentinel, which lives on the `EntryPoint` **record**; the **node** a site carries has no `handler` at all, so a branch keyed on the sentinel could not fire on a real scan.
**What changed:** the selector is `entry_kind` (`"cli"` / `"streamlit"`), which `EntryPointStage` writes for every entry it builds; `handler` stays function-only, so `ctx.entry_node_by_handler` is not polluted with a sentinel.
**Size:** ~0.5 day.
**References:** B.3b-user_upload-core-module.

#### B.3b-user_upload-fastapi — First upload detector

**Priority:** **Medium**
**Status:** Done (2026-10-29).
**Rationale:** First `UploadDetector`. `UploadFile` / `list[UploadFile]` / `bytes = File(...)`; `Form(...)` beats; `bytes` alone is not.
**What changed:** `store/context/upload/detectors/fastapi.py`; registration; docstring truth-fixes.
**Size:** ~0.5 day.
**References:** ADR-0011; ADR-0021.

#### B.3b-user_upload-streamlit — Second upload detector

**Priority:** **Medium**
**Status:** Done (2026-10-30).
**Rationale:** `st.file_uploader` is already an entry point (`STREAMLIT_ENTRY_CALLS`). The detector emits an `UploadSite` for a module-level entry point whose body calls `st.file_uploader`.
**What changed:** `store/context/upload/detectors/streamlit.py`; registration.
**Open question (answered):** module-level entries got no writer edge (B.3b-user_upload-core decision #5). The answer was to extend the interface with a module-level entry shape — extracted into its own step, B.3b-user_upload-core-module (with `-followup` making the marker `entry_kind`), so a module-level entry now gets a writer edge.
**Size:** ~0.5 day.
**References:** B.3b-user_upload-core; `parsing/entry_points.py`.

#### B.3b-user_upload-django — Third upload detector

**Priority:** **Medium**
**Status:** Done (2026-10-30).
**Rationale:** `request.FILES` in a Django view. Same interface.
**What changed:** `store/context/upload/detectors/django.py` — function-level, and the marker is the handler's **body** (`request.FILES["file"]`, `request.FILES.get("file")`) rather than its signature. The route check is `entry_points`', which learns Django's own spellings in B.3b-user_upload-route-detectors-followup.
**Size:** ~0.5 day.
**References:** B.3b-user_upload-core.

#### B.3b-user_upload-flask — Fourth upload detector

**Priority:** **Medium**
**Status:** Done (2026-10-30).
**Rationale:** `request.files` in a Flask view. Same interface.
**What changed:** `store/context/upload/detectors/flask.py` — `django`'s shape with `request.files` for `request.FILES`; the fourth and last detector.
**Size:** ~0.5 day.
**References:** B.3b-user_upload-core.

#### B.3b-user_upload-route-detectors-followup — Django and Flask route shapes

**Priority:** **Medium**
**Status:** Done (2026-10-30).
**Rationale:** The Django and Flask detectors match a handler's name against the nodes `EntryPointStage` built, so the stage has to recognize the two frameworks' own spellings — a Django view decorated the documented way was not an entry point at all.
**What changed:** `entry_points._extract_route` recognizes four shapes — a call on an attribute whose last segment is an HTTP method or `websocket` (`@router.post(...)`, `@app.post(...)`); the same with `route` as the last segment (`@app.route(..., methods=[...])`, method = the first name in `methods=`, GET when absent); a bare name Django guard (`@require_POST`, `@require_GET`, ...); a call on a name taking a literal list (`@require_http_methods([...])`, DRF's `@api_view([...])`). A path-less route keeps the `"<METHOD> <handler>"` name.
**Size:** ~0.5 day.
**References:** B.3b-user_upload-django; B.3b-user_upload-flask; ADR-0011.

#### B.3b-s3 — `s3` family

**Priority:** **Medium**
**Status:** Done (2026-10-30).
**Rationale:** Second family. `s3.put_object` / `s3.get_object`, `boto3` / `s3fs` / `MinIO`. Literal `Bucket` / `Key` extraction; prefix containment.
**What changed:** `store/families/s3.py`; registration by import; the family tests carry their own conftest (`test_store_core/conftest.py` autouse-empties the registry — `fs` is split the same way). The containment branch is still inert end-to-end (B.3b-fs-followup).
**Size:** ~1 day.
**References:** ADR-0021 §8; B.3b-fs.

#### B.3b-db — `db` family

**Priority:** **Medium**
**Status:** Done (2026-10-30).
**Rationale:** Third family. SQLAlchemy / asyncpg. Literal `INSERT INTO` / `SELECT FROM` table extraction (simple regex, not a SQL parser).
**What changed:** `store/families/db.py`; registration by import.
**Size:** ~1 day.
**References:** ADR-0021 §8.

#### B.3b-llm_fallback — LLM identity fallback

**Priority:** **Medium**
**Status:** Done (2026-10-30, febd3de).
**Rationale:** When the AST does not find a literal (unresolved identity), an opt-in LLM fallback (ADR-0023) proposes an identity hint from the surrounding code. `identity_source='llm'`, confidence 0.2; the store is weak (special floor 0.1).
**Size:** ~1 day.
**References:** ADR-0021 §7; ADR-0023; `store/llm_fallback/` (new).

#### B.3c — Migrate B.2 to the store model

**Priority:** **Medium**
**Status:** Done (2026-10-30). Was blocked by B.3b-s3, B.3b-db and B.3b-user_upload-*; all three landed first.
**Rationale:** `writes_to` targets `store` for every family, including `rag_corpus`, `email_inbox`, `mcp_catalogue`. `untrusted_source` remains the read site. `reads_from` connects the store to the read site. `find_chain_groups` joins by store id for all families.
**What changed:** B.2's writer rules emit `writes_to → store`; `untrusted_source_writers` retargets; the transitional control flow_kind for `writes_to` becomes structural; the source-anchored join stays (it still fires where a `writes_to` lands on the source itself) alongside the store join; `report.md` and `chain_groups.json` gain `weak: true` when relevant.
**Size:** ~2 days.
**References:** ADR-0021 §11.

#### B.3b-mcp-catalogue — `mcp_tool_description` migrates to the store model

**Priority:** **Medium**
**Status:** Done (2026-10-30).
**Rationale:** B.3c migrated the B.2 writers; `mcp_tool_description` was the third source type still emitting the pre-store shape.
**What changed:** the MCP catalogue writer emits `writes_to → store`; the direct shape is still read for hand-built deltas. Two latent fixes found on the way: `dynamic_mcp_invocation` and `evaluator._chain_edges`.
**Size:** ~1 day.
**References:** ADR-0021 §11; B.3c.

#### B.3b-fs-followup — Prefix containment through the resolver

**Priority:** **Medium**
**Status:** Done (2026-10-30, 5c2fa46).
**Rationale:** `literal_match`'s containment branch (+0.3) is correct and tested, but inert end-to-end: `_attached_keys` attaches a site only to the exact key it equals, so a write hint `/uploads/` and a read hint `/uploads/x` produce two stores, not one. Making containment live meant changing `_attached_keys` (a resolver change, not a family change). **Decided (5c2fa46):** the two stores are **not** merged — `_attached_keys` attaches the exact key(s) plus every key `literal_match` returns `LITERAL_PREFIX` for, so two keys give two stores, two `writes_to`, two `reads_from` and two groups; `LITERAL_ONE_SIDE` attaches nothing, `confidence.py` unchanged.
**Size:** ~1 day.
**References:** B.3b-fs; ADR-0021 §8.

#### B.3b-user_upload-attribution — `source_type='user_upload'` end-to-end

**Priority:** **High** (blocks B.3b-user_upload-* from producing a `user_upload` chain group)
**Status:** Done (2026-10-30, a9ec3a3). Its two followups (the shared-wrapper code-context row, the third-file module wrapper) landed in 5c2fa46.
**Rationale:** The attribution logic works (unit-tested), and end-to-end it now fires: a filesystem read (`open('/uploads/x', 'rb')`) is not one of 1.3a's source types, so ADR-0022 has the attribution pass create the `untrusted_source` node itself (`user_upload:<dotted call>`) in the writer's scope and link `reads_from` store → source.
**Two options:**
- **(a)** Upload context creates an `untrusted_source` node for an `fs` read in the writer's scope. New producer, new identity rule.
- **(b)** Multi-role `source_type` on an existing `untrusted_source` node (a source can be both `user_upload` and `rag_corpus` in different scopes). ADR-0022 territory.
**Decision (2026-10-30):** **(a)** — ADR-0022. The 1.3a producer takes precedence (§3); no overwrite.
**Size:** ~1 day.
**References:** B.3b-user_upload-core; ADR-0021 §10; ADR-0022.

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

#### B.4b-observed-by — the guardrail payload carries `observed_by`

**Priority:** **Medium**
**Status:** Done (2026-10-31, 5d1e3a3).
**Rationale:** ADR-0024 §6 requires the payload's `guardrails` section to name the producer. `observed_by` is the only field that tells `["ast"]`, `["ast", "framework:<name>"]` and `["ast", "static_llm:<model_id>"]` apart in a serialized payload; `_guardrail_to_dict` omitted it, so two guardrails on one chain looked identical.
**What changed:** `_guardrail_to_dict` returns `observed_by` in `_node_to_dict`'s order; `_guardrails_for_group` gains it through `_guardrails_for_chain`, the partition untouched; the docstring is rewritten, not extended.
**References:** ADR-0024 §6; B.4b.

#### B.4-gap — No producer of `guardrail` nodes after ADR-0016

**Priority:** **High** (blocks B.4 end-to-end)
**Status:** Done (2026-10-30, 6c4b028 + 33e0ac6 + c3a0b67 + f3f37ef + dd2bfa5 — the five steps B.4-gap-1..5). The `mcp_server` guards target of ADR-0019 rev 1 is code-pending (B.4-gap-5-followup).
**Found in:** B.4a implementation (2026-10-29).
**Rationale:** B.4a emits `guards` edges from `guardrail` nodes. On all four corpora `guards = 0` — there are no `guardrail` nodes for it to link. ADR-0016 decision 7 removed the class-A name heuristics (path rule, class suffix). `classify_class` / `classify_method` return `sink` / `agent` / `llm_endpoint` / `tool` and nothing else. The LLM classifier (D.2) can, via its `DetectionResult` merged at stage 10 (which is why `guardrail_edges` runs after the merge), but on the four corpora no source produces them.
**Consequence:** B.4a and B.4b are correct and ready; with B.4-gap-1..5 the producer exists, so guardrail influence fires where a `guardrail` node does — verified on synthetic targets, not yet on the four corpora.
**What does not close this gap:** re-adding the path rule or class suffix (class-A heuristics in another shape); LLM classifier as the only source (opt-in, off by default).
**What closed it (the decision, ADR-0024, 2026-10-30 — three producers, one precedence):**
- Capability-based detection (a class or function whose body calls a known filter / validator API). New `CapabilityRule`s (`validates_input`, negative); landed as B.4-gap-1. Corpus evidence still to be produced.
- Framework detector (LangChain callbacks, FastMCP middleware, Guardrails AI, NeMo Guardrails). ADR-0011 route; landed as B.4-gap-2..5.
- LLM classifier as an opt-in fallback, never the only source. B.4 must not depend on it being on. D.2 unchanged.

#### B.4-gap-5-followup — `mcp_server` as a guards target (ADR-0019 rev 1)

**Priority:** **Medium**
**Status:** ADR done (ADR-0019 rev 1, 8aa0b07, 2026-10-31), **code pending corpora**. The ADR is committed; the code brief is separate.
**Rationale:** `guardrail_edges.py::COMPONENT_KINDS` is `{"agent", "tool"}`, and the name-keyed call graph (ADR-0016) does not record `mcp.add_middleware(...)`, so a middleware registered on a server is a `guardrail` node and no `guards` edge (pinned by B.4-gap-5). ADR-0019 rev 1 grows `COMPONENT_KINDS` with `mcp_server` and gives it its own evidence rule (an `add_middleware(...)` call site on the server object, or a `@mcp.middleware(...)` decorator), and `_guardrails_for_chain` also reads a `guards` edge on a `tool`'s `parent_server`.
**Blocker:** the four-corpus regression (corpora not on this machine); the code brief is separate from the ADR.
**References:** ADR-0019 (rev 1); B.4-gap-5.

#### B.4b-followup — `SOLO` step 4 and user prompt questions

**Priority:** **Medium**
**Status:** Not started.
**Rationale:** `SOLO_SYSTEM_PROMPT` step 4 ("are they bypassable?") and the voter/solo/group user prompts ("are there guardrails on the path? are they bypassable?") ask a question the new rule says the payload cannot answer. Aligning those questions with ADR-0019 is a prompt iteration, not a code change.
**Size:** ~0.5 day.
**References:** ADR-0019 §2; B.4b.

### B.5 — Judge determinism

**Priority:** **Medium**
**Status:** Done (2026-10-30, 776b9b9).
**Rationale:** The judge runs with `temperature=0.6`. Two runs on the same graph with the same prompt can produce different `verdict` values, persisted in `chains.verdict` and `chain_groups.verdict`. The report is non-deterministic even when the graph is not.

**Approach:** `judge.temperature = 0.0` as the default, with an explicit override in the config for users who want stochasticity. Document the trade-off.

**Size:** ~0.5 day.

**References:** ADR-0005; the P0/P2 review.

### B.6 — `chain_groups.lethal_trifecta` column

**Priority:** **Low**
**Status:** Done (2026-10-31, 32c8157). The bundle's two row columns (`lethal_trifecta`, `validated_kind`) landed in the same step; no separate item is opened for the bundle, because the keys landed together.
**Decision (B.6):** `validated_kind` in `chain_groups.json` is gated on `verdict is not None` because `from_row` coerces a NULL to `"existence"` (a chain-row convenience, not a group-row fact); the bundle mirrors the stored column.
**Rationale:** `chain_groups.lethal_trifecta` exists in the schema and has no writer. It is `False` on every row. The per-node flag exists (1.3d); the per-group aggregate does not. The report does not show it, but `chain_groups.json` includes it as `false` on every row, which reads as a claim.

**Approach:** one line in `_evaluate_one_group`: `record.lethal_trifecta = any(node.attributes.get("lethal_trifecta") for node in read_chain_nodes)`. No schema change.

**Size:** ~0.5 hour.

**References:** 1.3d; 1.3e report.

### B.7 — `chain_groups.prompt_version` / `voter_outputs` columns

**Priority:** **Medium**
**Status:** Done (2026-10-31, da1478a).
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
**Status:** Done (2026-10-30, 776b9b9).
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

All in the "do not change" sets of past steps; each is one line or one sentence. Three doc-debt commits landed on 2026-10-30 (six stale docstrings, then four, then seven); the items still open are the ones below with a status other than `Done`.

### `store/registry.py` — "empty in B.3b-core"

**Priority:** **Low**
**Status:** Done (2026-10-30) — verified, no change needed. The docstring's "empty until a family is imported" is B.3b-core's **contract** and still true, and the same paragraph already names the three families (`fs`, `s3`, `db`).

### `store/context/upload/registry.py` — "empty in B.3b-user_upload-core"

**Priority:** **Low**
**Status:** Done (2026-10-30). The docstring now names the four the tree ships (`fastapi`, `streamlit`, `django`, `flask`); the "empty until a detector is imported" clause is kept verbatim, because it is the contract.

### `store/resolver.py::_link_confidence` — case list

**Priority:** **Low**
**Status:** Done (2026-10-30). The case list now names the containment branch (`LITERAL_PREFIX`, +0.3, B.3b-fs's row: `startswith` in both directions, nothing normalised).

### `families/base.py` — `B.3b-user_upload` naming

**Priority:** **Low**
**Status:** Done (2026-10-30). The docstring names `B.3b-user_upload-core` as the first concrete context, next to `B.3b-core` for the parameter itself.

### `store_resolver.py` — stale sentence + duplicate `__all__`

**Priority:** **Low**
**Status:** Partially done (2026-10-30). The duplicate `__all__` is gone (one left, line 313). The stale sentence is still there: lines 23–27 say the detector registry holds **two names** (`fastapi`, `streamlit`) where the tree ships four.

### `store/context/__init__.py` — "the detectors the tree ships"

**Priority:** **Low**
**Status:** Not started.
**Rationale:** Lines 9–12 name `fastapi` and `streamlit` as the detectors importing `upload` registers; `django` and `flask` are registered too. Flagged as out of scope in the 2026-10-30 doc-debt pass.

### `flow_kind.py` inline comment + `ast_normalizer.py` edge-list header

**Priority:** **Low**
**Status:** Done (2026-10-29) — first doc-debt commit; the follow-up commit touched `flow_kind.py`'s inline comment and `ast_normalizer.py`'s edge-list header. `ast_normalizer.py`'s "fifteen → nineteen stages" also fixed.

### `store/families/base.py` — `B.3b-user_upload` and `B.3b-core` naming

**Priority:** **Low**
**Status:** Done (2026-10-30) — the same file as the `families/base.py` item above; this entry's title said `family/base.py`, the path is `store/families/base.py`. Actually fixed in the 2026-10-30 doc-debt pass: `B.3b-user_upload` → `B.3b-user_upload-core`, and `B.3b-core` is the correct reference for the parameter itself.

### Doc-debt opened by B.4-gap-1..5, B.5+B.9 and B.6+B.7 (2026-10-31)

**Priority:** **Low**
**Status:** Not started — **open**. A separate brief; the 2026-10-31 commits do not fix these.

- `Documentation/Architecture/C4_L3_Phase1.md` lines 34 (the four-detector list needs `guardrails_ai`), 39 and 301 ("20 stages" → the guardrail stage), 323.
- `parsing/normalizer_stages/body_attributes.py` and `lethal_trifecta.py` — "second to last" no longer names the last stage.
- `graph/query.py:1436` — the `validated_kind` levels ("third level": `evidence`).
- `tests/test_reporting.py:359` — the stale group-row shape.
- `store/context/upload/detectors/streamlit.py:19` — "the module's top-level functions" (superseded by B.3b-user_upload-core-module-followup-2).
- `Documentation/BACKLOG.md` self-references: lines 152 and 451 (`-module`'s old reach), 180, and 6 / 23 / 30 / 37 (the header and §0.1–§0.2).
- `store/families/s3.py:95-101` — the containment branch is live end-to-end now.
- `store/confidence.py:43-49` — same.
- ADR-0024 §2's `logger.info` example — unreachable as written (`_collect_call_names` records `ast.Name` targets only).
- `tests/test_normalizer_stages/__init__.py` — the stage list.
- `B.4-gap-5-followup`'s "Blocker: the four-corpus regression" is also `D.5`'s
  real blocker: `D.5` (guardrails `bypassed_by` derived edge) says "**Blocked by**
  B.4-gap", but `B.4-gap` is Done (B.4-gap-1..5); the real blocker is the same
  corpus run. This line records the correction; `D.5`'s own "Blocked by" text is
  left for the next planning session, when the corpora return.
  
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