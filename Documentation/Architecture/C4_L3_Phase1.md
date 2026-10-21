# AICorellator — C4 Level 3: Components (Phase 1)

**View:** Component (C4 L3) · **Phase:** 1 — AST + Framework Detection + Capabilities · **Date:** 2026-10-09 · **Last reviewed:** 2026-10-21 · **Status:** Accepted

---

## Scope

Phase 1 covers **graph population from source code and configs**. It takes a target directory, discovers files, parses them with tree-sitter-analyzer, extracts capabilities, runs framework detectors, resolves entry points and LLM endpoints, and normalizes everything into a graph delta written to the Graph Store.

No enrichment (Phase 2). No analysis (Phase 3). No reporting (Phase 3).

The phase has three classification layers (ADR-0009, ADR-0011):

- **Level 1** — framework detectors, per-file, framework-specific.
- **Level 2** — capability extraction, framework-agnostic.
- **Level 3** — name-based heuristics, the original FinBot-tuned rules.

Every edge produced in Phase 1 carries `flow_kind` — `control`, `data`, or `structural` (ADR-0015). Cross-file edges and MCP entry synthesis are resolved by the normalizer after detector merge (ADR-0013).

**Architectural repair (v0.2.0-dev).** The parsing layer was refactored in four steps: `FlowDecision` type (one flow-decision contract, one `flow_kind_for` mapping), `flow/` split (one module per analyzer question), `patterns_registry.py` (matching semantics separated from data), and `normalizer_stages/` (17 explicitly ordered `NormalizerStage` objects). The component descriptions below reflect the post-repair layout.

---

## Diagram

``mermaid
flowchart TB
    subgraph PH1["Phase 1 — AST + Framework Detection + Capabilities"]
        ING[Ingestion<br/>accepts target path]
        DISC[File Discovery<br/>walk, filter, hash]
        CAP[Capability Extractor<br/>AST-derived capabilities]
        TSA[TSA Worker<br/>tree-sitter-analyzer]
        DET[Framework Detectors<br/>langchain, langgraph,<br/>fastmcp, mcp_python]
        RUN[Detectors Runner<br/>orchestrates detectors]
        EP[Entry Points Detector<br/>routes, agent calls,<br/>call graph]
        LLMX[LLM Extractor<br/>config + env parsing]
        FLOW["flow/ package<br/>arguments, edge_analyzer,<br/>sink_analyzer, tool_analyzer"]
        NORM["AST Normalizer<br/>17 stages (normalizer_stages/)"]
        WRITE[Graph Writer]
        FLAG[Flag Setter<br/>phase1_done]
    end

    SRC[Target Sources] --> ING
    ING --> DISC
    DISC --> TSA
    DISC --> CAP
    DISC --> DET
    DISC --> EP
    DISC --> LLMX
    DET --> RUN
    RUN --> NORM
    TSA --> NORM
    CAP --> NORM
    EP --> NORM
    LLMX --> NORM
    NORM --> FLOW
    NORM --> WRITE
    WRITE --> GS[(Graph Store)]
    WRITE --> FLAG
    FLAG --> STATE[(State)]
``

---

## Components

### 1. Ingestion

**Responsibility:** Accepts a target path — folder, repo root, unpacked image, or mounted FS. Normalizes it into an absolute root path. Validates that the path exists and is readable.

**Interface:**

- `ingest(path, kind) -> Target`
- `Target.root: Path`
- `Target.kind: str` — `folder` | `repo` | `image` | `mount`

**Phase 1 note:** only `folder`, `repo`, and `mount` are supported. Image unpacking is a Phase 3 agent responsibility.

---

### 2. File Discovery

**Responsibility:** Walks the target tree, applies include/exclude rules, computes a content hash per file. Produces a manifest of files to parse.

**Interface:**

- `discover(target: Target) -> FileManifest`
- `FileManifest.files: list[FileEntry]`
- `FileEntry.path: str` — relative to target root, POSIX separators
- `FileEntry.abs_path: Path`
- `FileEntry.language: str | None`
- `FileEntry.content_hash: str`

**Exclude rules:** `.git/`, `node_modules/`, `__pycache__/`, `venv/`, `.venv/`, `dist/`, `build/`, and other VCS and build artifacts. Hidden files are skipped except a small allowlist (`.env`, `.gitignore`, `.dockerignore`).

**Symlinks are not followed.** This avoids loops and escapes from the target root.

**Language detection:** by extension. Languages supported by TSA are enumerated in the patterns registry.

---

### 3. TSA Worker

**Responsibility:** Runs `tree-sitter-analyzer` on each supported file. Produces a language-agnostic index of classes, methods, and functions.

**Interface:**

- `run(manifest: FileManifest, root: Path) -> TSAOutput`
- `TSAOutput.symbols: list[TSASymbol]`
- `TSAOutput.precision: str` — always `syntactic`
- `TSAOutput.files_analyzed: int`
- `TSAOutput.files_failed: int`

**Invocation:** `uvx --python 3.14 --from tree-sitter-analyzer` as a local subprocess. No venv, no Node, no compiler required. Python 3.14 is pinned because `tree-sitter-analyzer` 2.2.0 depends on `cachebox` 5.2.3, a PyO3 extension that does not yet support Python 3.15.

**Output format:** JSON. Parsed into `TSASymbol` records carrying name, kind, file path, line range, and optional enclosing class.

**What TSA provides and does not provide.** The backlog investigation (item 1.1) confirmed that `tree-sitter-analyzer --structure --format json` provides symbols only — classes, methods, fields, imports, annotations, line ranges. It does **not** provide call edges, type information, or argument information. `TSAOutput.call_edges` will never be populated from TSA; call graph and flow analysis are our own AST-level work.

**SCIP is deferred.** ADR-0002 originally proposed an optional SCIP worker as a precision layer. It is not implemented. The `precision` attribute exists on edges, but no producer writes `compiler-verified` yet.

---

### 4. Capability Extractor

**Responsibility:** Derives capabilities from the AST (ADR-0009). For each symbol in a file, scans its body for known call patterns and records the capabilities exercised.

**Interface:**

- `extract(file_path: Path, relative_path: str) -> FileCapabilities`
- `FileCapabilities.file_path: str`
- `FileCapabilities.symbols: list[SymbolCapabilities]`
- `FileCapabilities.module_capabilities: set[str]`

**Pattern registry.** The set of recognized call patterns is data, not code: `patterns.py` declares 17 `CapabilityRule` objects (217 `CallPattern` entries, each rule with a required `reason` string), and `patterns_registry.py` owns the matching semantics (`CallPattern.matches`, `matches_any`, the underscore and wildcard rules, the two validation invariants). Wildcards are not a concept in the matcher — `repo.get*` is `CallPattern("repo", "get", verb_family=True)`.

**Capabilities emitted:** `executes_code`, `executes_shell`, `reads_filesystem`, `writes_filesystem`, `reads_db`, `writes_db`, `http_egress`, `reads_env`, `reads_secrets`, `calls_llm`, `invokes_mcp_tool`, `reads_untrusted`.

`reads_untrusted` is credited from three rule families — RAG corpora (vector store / retriever / loader returns), MCP tool descriptions (`list_tools` on a client, `list_tools` on a server), and mailbox reads. It is credited on the **reader** (the function or method whose body makes the call), never on the `untrusted_source` node the read points at: the source is a place, not an actor.

**Not emitted:** `http_ingress` (entry points are detected by kind, not capability).

**Design:** call-site based, not dataflow. False positives are accepted; they are better than no signal. The LLM in Phase 3 explains and prioritizes. Every credit is logged at DEBUG with the rule's `reason`: `capability <x> from <call>: <reason>`.

**Nested functions are scanned.** Functions defined inside factories are found by name, so `@tool`-decorated closures receive their capabilities.

---

### 5. Framework Detectors

**Responsibility:** Recognize a specific AI framework in one file and extract nodes and edges from that framework's conventions (ADR-0011).

**Interface:**

- `detect(file_path, tree, source, host_id) -> DetectionResult`
- `DetectionResult.nodes: list[Node]`
- `DetectionResult.edges: list[Edge]`
- `DetectionResult.framework: str`
- `DetectionResult.detected_version: str | None`

**Implemented detectors:** LangChain, LangGraph, FastMCP, MCP Python SDK.

**Per-file contract.** A detector receives one file and returns nodes and edges from that file alone. No import resolution, no global state, safe for parallel execution.

**What detectors emit:**

- **LangChain**: `agent` nodes from `AgentExecutor`, `initialize_agent`, `create_agent`; `tool` nodes from `@tool`, `Tool(...)`, `StructuredTool.from_function(...)`; `llm_endpoint` nodes from `ChatOpenAI`, `ChatAnthropic`, `ChatOllama`, and others. Tool bodies are scanned for capabilities. Within-file `uses_tool` and `uses_model` edges when the agent's `tools=` or `model=` argument can be resolved in the same file.
- **LangGraph**: graph nodes from `graph.add_node(...)`, graph edges from `graph.add_edge(...)` and `graph.add_conditional_edges(...)`, entry points from `graph.set_entry_point(...)`. Node kind is inferred from the node name.
- **FastMCP**: `mcp_server` nodes from `FastMCP(...)`; `tool` nodes from `@<var>.tool(...)` decorators, with `parent_server` set. Tool bodies are scanned for capabilities.
- **MCP Python SDK**: `mcp_server` nodes from `Server(...)`; `tool` nodes from `@<var>.call_tool()` decorators, with `parent_server` set. Other server-bound decorators (`list_resources`, `read_resource`) are recognized but not emitted as tools.

**Cross-file edges are not the detector's job.** They are resolved by the normalizer (ADR-0013).

**Registry:** `framework_detectors/registry.py` maps names to detector classes. Adding a detector is one line.

**Version support:** each detector declares a supported version range. If the detected framework version is outside the range, a warning is logged and detection proceeds at the user's risk.

**Flow-kind enrichment.** Detectors return `FlowDecision` objects from the enrichment helpers (`enrichment.py`). LangChain `uses_tool` is `parameter_match` when the tool is named in the agent's system prompt, else `unknown`. LangChain `uses_model` is `literal_only` for a literal or resolved module assignment, `parameter_match` for a function parameter, else `unknown`. FastMCP and MCP Python set `tool_data_flow` on tool nodes (`parameter_match` / `no_args`), which the normalizer copies onto the MCP edges it builds (1.2a.2). Detector edges carry `flow_kind = flow_kind_for(decision)`.

**Note on MCP edges.** The `provides_tool` edge from an MCP server to a tool is built by the normalizer, not by the detector (only path-heuristic servers get a `provides_tool` edge; detector-owned servers get a virtual entry + `delegates_to`). The detector's contribution is the `tool_data_flow` attribute on the tool node; the normalizer copies it at the two waived enrichment sites.

---

### 6. Detectors Runner

**Responsibility:** Orchestrates all enabled framework detectors across a file manifest.

**Interface:**

- `run(manifest, enabled, host_id) -> DetectionResult`

**Behavior:** for each Python file, parses the AST once, then invokes each enabled detector on that AST. Merges all returned nodes and edges into a single `DetectionResult`.

**Error handling:** a detector that raises on a file is logged and skipped. Other detectors continue.

**Enabled names:** user-supplied via CLI flag. If none supplied, all registered detectors run.

---

### 7. Entry Points Detector

**Responsibility:** Finds HTTP routes, WebSocket handlers, agent calls inside route bodies, MCP usages (`create_mcp_server` calls), CLI entry points (`if __name__ == "__main__":`), and Streamlit entry points (`st.chat_input`, `st.button`, `st.form_submit_button`, `st.file_uploader`). Builds a per-file call graph.

**Interface:**

- `run(manifest, known_agents) -> EntryPointOutput`
- `EntryPointOutput.entry_points: list[EntryPoint]`
- `EntryPointOutput.agent_calls: list[AgentCall]`
- `EntryPointOutput.mcp_usages: list[MCPUsage]`
- `EntryPointOutput.call_graph: dict[str, set[str]]`
- `EntryPointOutput.imports: dict[str, list[str]]`

**Route detection:** decorators whose attribute is an HTTP method or `websocket`. Handler name, path, method, and line are recorded.

**CLI entry detection:** `if __name__ == "__main__":` at module level. Marked `method = "CLI"`, `entry_kind = "cli"`.

**Streamlit entry detection:** calls to `st.chat_input`, `st.button`, `st.form_submit_button`, `st.file_uploader` at module level, when the file imports `streamlit`. Marked `method = "STREAMLIT"`, `entry_kind = "streamlit"`.

**Agent call detection:** four heuristics — delegation suffix (`delegate_to_*`), runner-function mapping (`run_*_agent`), extra agent classes, and direct class calls. Calls inside agent modules are flagged as agent-to-agent.

**MCP usage detection:** calls to `create_mcp_server("name", ...)` record the enclosing handler and the server name.

**Call graph:** for every `FunctionDef`/`AsyncFunctionDef` in the file (route handler or not), the set of names it calls via `ast.Name` targets. Every function is recorded, including functions with no calls. Used by the normalizer to resolve entry-point-to-sink reachability (ADR-0013, cross-file reachability resolver).

**Imports:** per-file list of dotted module names. Used by the cross-file reachability resolver for CLI and Streamlit entries.

**Not in scope:** entry points from factory functions (`get_agent(...)`) not named in the heuristics. This is handled by the orphan-entry resolver (ADR-0013).

---

### 8. LLM Extractor

**Responsibility:** Reads config files and extracts LLM endpoint information. This is the component that closes the "agent to model" gap.

**Interface:**

- `run(manifest) -> LLMExtractorOutput`
- `LLMExtractorOutput.endpoints: list[LLMEndpoint]`
- `LLMExtractorOutput.deduplicated() -> list[LLMEndpoint]`

**Files scanned:** `.env` and variants, Python config files, YAML (including docker-compose), TOML, JSON.

**Detection patterns:** provider keys (`LLM_PROVIDER`), model keys (`LLM_MODEL`, `*_MODEL`), base URL keys (`*_BASE_URL`, `*_ENDPOINT`), and provider-specific keys (`OPENAI_*`, `ANTHROPIC_*`, `OLLAMA_*`).

**Deduplication:** one endpoint per unique `(provider, model, base_url)` triple.

---

### 9. `flow/` package

**Responsibility:** Answers three distinct flow questions. One module per question (split in architectural repair, step 2).

**Modules:**

- **`flow/decision.py`** — `FlowDecision(kind, evidence, source)` value object and `flow_kind_for(decision)` mapping. `VALID_KINDS` and `DATA_KINDS` are the single source of truth; `DATA_FLOW_CONFIRMED_VALUES` and `DATA_FLOW_DATA_VALUES` are aliases kept for backward compatibility. `FlowDecision` validates its `kind` on construction. `flow_kind_for` is the single mapping from `data_flow_confirmed` to `flow_kind` (ADR-0015 decision 3).
- **`flow/arguments.py`** — shared argument primitives: `_classify_argument`, `_is_literal`, `_parameter_names`, `_local_variable_names`, `_self_attribute_names`, `_is_self_attribute`, `_attribute_base_name`, `_classify_components`, evidence helpers. Import only `ast`.
- **`flow/edge_analyzer.py`** — `analyze_has_access_to(tool_func, sink_call) -> FlowDecision` (source `"edge_analyzer"`). Never opts into `parameter_attributes`.
- **`flow/sink_analyzer.py`** — `analyze_sink_body(func) -> FlowDecision` (source `"sink_body"`) and `analyze_sink_class(cls) -> FlowDecision` (source `"sink_class"`). Contains the commit exception (1.2a.3c). The only module that imports `capability_extractor` / `patterns` and the only one that passes `parameter_attributes=True` (declared in its own call sites, not in the shared signature's default).
- **`flow/tool_analyzer.py`** — `analyze_tool_returns(func) -> bool` (different contract: a tool returning data is not a flow decision).

**FlowDecision fields.** `kind` (one of six), `evidence` (a short tuple of names — parameter names, argument descriptors, method names — never values, source text, line numbers, or file paths), `source` (the analyzer or enrichment helper that produced the decision).

**Algorithm (name-based, not value-based):**

For each argument of the dangerous call, classify it:

- A parameter of the enclosing function → `parameter_match`.
- A local variable defined in the body → `local_var_match`.
- The result of another call → `call_result_match`.
- A literal or constant, or a collection of constants → `literal_only`.
- No arguments at all → `no_args`.
- An attribute on `self` assigned from a parameter or local in the same body → `parameter_match` (1.2a.3g).
- An attribute on a parameter or local (`param.attr`, `local.attr`) to any depth → `parameter_match` (1.2a.3j).
- A collection with a parameter-derived element → `parameter_match` (the composite's own kind, 1.2a.3g).
- Anything else (unresolved attribute, call result, f-string with no parameter component) → `unknown`.

The edge takes the strongest result across all arguments (non-linear ranking): any `parameter_match` wins, then any `local_var_match`, then any `call_result_match`, then all-literal `literal_only`, then `no_args`, else `unknown`.

**Mapping to `flow_kind`:** `flow_kind_for(decision)` — `data` for the three data kinds, `control` otherwise.

**Scope.** `analyze_has_access_to` runs on `has_access_to` edges only (same-file tool to sink). `analyze_sink_body` runs on sink-node bodies. `analyze_sink_class` runs on class-kind sink bodies (`__init__`, `__call__`, all direct methods, class-level statements). For `uses_model`, `uses_tool`, `delegates_to`, `data_flow_confirmed` comes from the detector enrichment, not from the generic analyzer.

**Limitations.** No data tracking through function calls (ADR-0001). `self.q.name` (base is `self`, not the derived attribute) is `unknown`. `fetch().path` (base is a Call) is `unknown`. False negatives are accepted; false positives on `call_result_match` and `local.X` from a literal-derived local are accepted (ADR-0009, ADR-0001).

---

### 10. AST Normalizer (17-stage pipeline)

**Responsibility:** The central merge component. Takes all Phase 1 outputs and produces a single `GraphDelta` of nodes and edges.

**Interface (post-repair):**

- `normalize(ctx: NormalizeContext) -> GraphDelta`
- `GraphDelta.nodes: list[Node]`
- `GraphDelta.edges: list[Edge]`
- `make_context(manifest, tsa_output, entry_points=None, llm_output=None, capabilities=None, framework_detection=None, host_id="localhost") -> NormalizeContext`

`normalize` is a 135-line loop over `STAGES`; the work is in 17 `NormalizerStage` objects. `NormalizeContext` holds the inputs, the per-scan caches (`module_cache`, `functions_cache`, `classes_cache`), and the derived indexes (`mcp_servers`, `tools_by_file`, `sinks_by_file`, `entry_node_by_handler`, `agent_node_by_name`, `agent_node_by_file`). No shim for the pre-repair signature.

**The 17 stages, in order:**

| # | Stage | Responsibility |
|---|---|---|
| 1 | `mcp_server_nodes` | `mcp_server` nodes from file paths under `mcp/servers/`. |
| 2 | `class_nodes` | Class nodes (Level 3 classification + Level 2 capabilities). |
| 3 | `method_nodes` | Method nodes; also fills `ctx.tools_by_file`, `ctx.sinks_by_file`. |
| 4 | `intra_file_edges` | `provides_tool` (MCP server → tool), `has_access_to` (tool → sink, same file). Sets `data_flow_confirmed` from the edge analyzer; leaves `flow_kind` at its default. |
| 5 | `entry_points` | Entry point nodes (HTTP, CLI, Streamlit) from the entry points detector. |
| 6 | `untrusted_sources` | `untrusted_source` nodes — one per read site (RAG corpus, MCP tool description, mailbox) — plus `has_access_to` to the reader. Empty capabilities by design; sets `data_flow_confirmed = "call_result_match"` and leaves `flow_kind` to stage 12. **Must run after `entry_points` and before `detector_merge`.** See the invariants below. |
| 7 | `llm_endpoints` | LLM endpoint nodes from the LLM extractor. |
| 8 | `delegation_edges` | `delegates_to` — entry → agent, agent → agent, agent → MCP server. |
| 9 | `entry_to_sink` | `has_access_to` from entry points to sinks reachable through same-file calls. **Must run before `detector_merge`.** See the invariant below. |
| 10 | `detector_merge` | Detector nodes authoritative for kind; capability preservation; id remap; edge re-point; duplicate-id collapse. `flow_kind` and `data_flow_confirmed` preserved across the merge. |
| 11 | `untrusted_source_writers` | `writes_to` — `mcp_server → untrusted_source` (1.3a.1, ADR-0010). The write half of the chain-group channel: a server declares the tool catalogue it produces. Four anchors, unioned: `parent_server`, the server's own file, the `mcp/servers/<name>/` directory, and a literal factory module path in the source's file (1.3a.1.2, FinBot's `_SERVER_INTROSPECTORS`). `mcp_tool_description` sources only; sets `data_flow_confirmed = "unknown"` and leaves `flow_kind` to stage 12. **Must run after `detector_merge`** (framework-detected servers exist only then) **and before `flow_kind`.** See the invariants below. |
| 12 | `flow_kind` | The single place calling `flow_kind_for`. Reads `data_flow_confirmed` from each edge (an explicit `FlowDecision` re-validation), sets `flow_kind`. Structural edges skipped. |
| 13 | `dynamic_mcp_invocation` | `uses_tool` — `agent → tool` for every tool of every server that writes a catalogue the agent reads, and that reads it (1.3a.1.2, ADR-0010). The agent's `call_tool(name)` is a runtime value, so the hop from the catalogue reader to the tools the catalogue describes is a rule, not a resolution: "can invoke" is the candidate attack path, the same trade-off `provides_tool` makes. `mcp_tool_description` sources only; passes `CONTROL_FLOW`. **Must run after `untrusted_source_writers`** (it walks the `writes_to` edges that stage emits) **and after `flow_kind`** (it is a resolver and passes the mapping's output, like the three resolvers below). See the invariants below. |
| 14 | `cross_file_resolution` | `uses_tool` edges from agent modules to tool modules they import (ADR-0013, Resolver 1). |
| 15 | `mcp_entry_synthesis` | Virtual MCP entry point per framework-detected server + `delegates_to` to each tool whose `parent_server` matches (ADR-0013, Resolver 3). Carries the enrichment value from `tool_data_flow` through `flow_for_tool_node`. |
| 16 | `orphan_entry` | `delegates_to` from entry points with no outgoing delegation edge to the single framework agent (ADR-0013, Resolver 2). |
| 17 | `body_attributes` | Backfill `sink_data_flow` and `returns_data` after the detector merge. Runs last because `detector_merge` may replace a TSA node and drop its body attributes; the guard (`"returns_data" not in attributes`) is the same one the pre-repair inline pass used. |

**Key invariants:**

- All IDs are deterministic. Same input produces the same delta.
- Detector nodes are authoritative for kind; capabilities are preserved across the merge.
- Removed nodes do not leave dangling edges.
- MCP entries are marked with `entry_kind = "mcp"`; CLI and Streamlit entries are marked with `entry_kind = "cli"` and `entry_kind = "streamlit"`. HTTP routes remain unmarked.
- **Every edge carries `flow_kind`.** An edge without `flow_kind` is a construction bug, caught by tests.
- **`structural` edges do not participate in `find_chains`.** `guards` describes a defensive control, not a path. `provides_tool` is `control` and is traversed.
- **`entry_to_sink` runs before `detector_merge`.** The resolver reads the TSA-side `sinks_by_file` index and emits `has_access_to` edges to node ids that the merge may remove; running it after the merge produces dangling edges (measured: six on dvmcp) and a `FOREIGN KEY` failure at write time. The merge's id remap re-points those edges as it always did. The stage order is pinned by `tests/test_normalizer_stages/test_stage_order.py`.
- **`untrusted_sources` runs after `entry_points` and before `detector_merge`.** It resolves each read to a reader node (`untrusted_source → has_access_to → reader`). A plain route handler is not classified as a tool, a sink or an agent, so the only node that can carry the edge is the `entry_point` node the previous stage creates — the stage resolves it through the entry point's `handler` attribute. Running before the merge means the merge's id remap re-points these edges too, for the same reason as `entry_to_sink`. A read whose reader resolves to no node still emits the source node, with no edge (observed on the corpora: reads inside plain helper functions and test functions).
- **`untrusted_source_writers` runs after `detector_merge` and before `flow_kind`.** It emits `writes_to` from every `mcp_server` node visible from a `mcp_tool_description` source's file — the precise anchor when the source carries `parent_server`, the server's own `source_file` otherwise, the `mcp/servers/<name>/` directory for a path-heuristic server, which has no file, and a literal factory module path (`...mcp.servers.<name>.server.create_<name>_server`) the source's own file spells out, which is the anchor that reaches a writer in another file. The merge has to have run first: framework-detected server nodes (dvmcp, mcploitable) exist only then, and they are the ones that carry the file the rule matches on. Running before `flow_kind` keeps ADR-0015 decision 3 as the single derivation. The stage creates no nodes, and a source no server is visible from gets no edge (measured: all four corpora, 1.3a.1's report; FinBot gains ten edges from the literal anchor, 1.3a.1.2's report).
- **`dynamic_mcp_invocation` runs after `untrusted_source_writers` and after `flow_kind`.** It emits `agent → uses_tool → tool` for every tool of every server that writes a catalogue the agent both reads (`source → agent`, stage 6) and has the `reads_untrusted` capability for. It needs the `writes_to` edges stage 11 emits, and it is a resolver, so it runs with `cross_file_resolution`, `mcp_entry_synthesis` and `orphan_entry` and passes `CONTROL_FLOW` the same way they do. It reads the delta only, creates no nodes, and skips an edge id the delta already has. On the four corpora it emits nothing: FinBot's catalogue reader reads `finbot/mcp/provider.py`, and the factory-path literals are in the two route files, so the writer edges land on *their* sources — whose reader is a plain helper function with no node — while the reader's own source has no writer (measured: 10 `writes_to`, 0 `uses_tool`, 0 groups, 1.3a.1.2's report).
- **`untrusted_source` nodes carry no capabilities.** `reads_untrusted` is credited by the Capability Extractor on the reader — the component whose body makes the call. The source is a place, not an actor, so a reader-shaped capability on it would double-count the same fact.
- **One `untrusted_source` node per read site per file**, named `<source_type>:<call_target>` and identified by `(source_type, call_target, source_file)`. Two services in different files that call the same API are two nodes; the file is what makes the read site concrete.
- **`GraphDelta` lives in `parsing.ast_normalizer`.** It is a parsing-layer value type; `graph.models` holds `Node` / `Edge`. Stages import it under `TYPE_CHECKING` to avoid an import cycle. Moving it to `graph.models` is a future cleanup.

---

### 11. Cross-File Resolver

**Responsibility:** Resolve edges that cannot be seen from a single file (ADR-0013).

**Implemented in:** `framework_detectors/cross_file.py` (Resolver 1) and `normalizer_stages/` (Resolvers 2, 3, 4, as stages 9, 14, 15, 16 above).

**Function:**

- `resolve_agent_tool_edges(manifest, detector_nodes, host_id) -> list[Edge]` — Resolver 1.

**Algorithm (Resolver 1):** index tools by source file, index framework agents, collect per-file imports, match imported module names to file paths by trailing segments, emit `uses_tool` edges from each agent to every tool in each matched file.

**Flow kind:** `control`, `data_flow_confirmed = "unknown"` (ADR-0015).

**Design:** false positives accepted. An agent that imports a module with twenty tools receives edges to all twenty. Phase 3 filters.

---

### 12. Graph Writer

**Responsibility:** Writes the `GraphDelta` into the Graph Store. Deduplicates by canonical ID.

**Interface:**

- `write(store: GraphStore, delta: GraphDelta, host_id: str) -> dict[str, int]`
- `set_phase1_done(store: GraphStore, host_id: str) -> None`

**Return value:** counts of added and updated nodes and edges.

**Deduplication:** handled by the repository `upsert` methods. Attributes are merged, capabilities are unioned, confidence takes the maximum, precision is upgraded. `flow_kind` and `data_flow_confirmed` are overwritten by the newer edge — they describe the current analysis, not a history. A latent hazard (an explicit `"unknown"` overwriting an enriched `"data"`) exists today but no duplicate edge ids are produced; see backlog 1.2a.2c.

---

### 13. Flag Setter

**Responsibility:** Sets `phase1_done = true` in the State table after a successful write.

**Effect:** the graph becomes available for Phase 3 evaluation and reporting.

---

## Data Flow

| Step | Input | Output |
|---|---|---|
| Ingestion | target path | normalized `Target` |
| File Discovery | `Target` | `FileManifest` |
| Capability Extractor | manifest | per-file `FileCapabilities` |
| TSA Worker | manifest + root | `TSAOutput` (symbols) |
| Framework Detectors | manifest | `DetectionResult` (nodes + edges, with `FlowDecision` from enrichment) |
| Entry Points Detector | manifest | `EntryPointOutput` (routes, agent calls, call graph, imports) |
| LLM Extractor | manifest | `LLMExtractorOutput` |
| `flow/` analyzers | tool body + sink call; sink body; class body | `FlowDecision` (kind, evidence, source) |
| AST Normalizer | `NormalizeContext` | `GraphDelta` with `flow_kind` on every edge |
| Graph Writer | `GraphDelta` | rows in `nodes`, `edges` |
| Flag Setter | — | `phase1_done = true` |

---

## Interfaces to Other Phases

| Direction | Component | Interface |
|---|---|---|
| Phase 1 → Phase 0 | Graph Writer | Nodes and Edges repositories |
| Phase 1 → Phase 2 | Flag Setter | `state.phase1_done` |
| Phase 1 → Phase 3 | — | Graph is available for chain enumeration; every edge carries `flow_kind` |

---

## What Is NOT Shown

- **Enrichment internals** — Phase 2.
- **Analysis internals** — Phase 3.
- **Reporting internals** — Phase 3.
- **Multi-host delivery** — Phase 3.
- **SCIP worker** — deferred (ADR-0002, ADR-0003). TSA does not provide call edges; SCIP would not change flow-kind determination.
- **Precision merge** — deferred with SCIP.
- **Cross-file tool to sink.** Same-file reachability is implemented; cross-file requires call-graph extension across imports. Deferred (backlog).
- **Full dataflow analysis.** Out of scope (ADR-0001). The `flow/` analyzers are name-based, not value-based.
- **Stage-disabling flags.** `--no-cross-file` and similar are a future concern; not part of the pipeline.

---

## Design Notes

1. **TSA is the baseline.** It always runs. SCIP is an optional enrichment on top, deferred. TSA provides symbols only; call graph and flow analysis are our own.
2. **LLM Extractor is the critical gap-closer.** AAK and agent-bom do not connect agents to models. This component does.
3. **Classification is layered.** Framework detectors (Level 1) are authoritative; capability extraction (Level 2) overrides name-based heuristics when it disagrees; name-based heuristics (Level 3) are the fallback.
4. **Canonical IDs are per-kind.** Same identity fields produce the same ID. Re-runs reuse existing nodes.
5. **`content_hash` enables re-run caching.** Unchanged file means unchanged hash, and Phase 2 findings can be reused.
6. **No dynamic behavior.** Phase 1 sees only static artifacts. Dynamic tool registration, runtime endpoints, and env-based configuration are invisible unless they appear in files.
7. **Detectors are per-file.** Cross-file resolution is a normalizer concern. This preserves the detector contract and makes detectors independently testable.
8. **False positives are accepted.** Both capability extraction and cross-file resolution prefer a spurious edge over a missing one. The LLM ensemble in Phase 3 prioritizes and filters.
9. **Capabilities override kind at chain time.** A tool with `executes_code` is a valid chain endpoint, even though its kind is `tool` (ADR-0014).
10. **`uses_tool` is a stored edge kind.** Introduced for cross-file agent-to-tool edges (ADR-0013). `writes_to` (1.3a.1) is the second added kind: the `mcp_server → untrusted_source` write edge of ADR-0010's channel, `control` with `data_flow_confirmed = "unknown"`.
11. **MCP entries are virtual.** They do not correspond to an HTTP route; they model "an MCP client can invoke this server's tools". Marked with `entry_kind = "mcp"`.
12. **Two MCP detectors, one entry synthesis.** FastMCP and the Python SDK are detected separately; the MCP entry synthesis is shared.
13. **Call-graph reachability extends to MCP, CLI, and Streamlit.** The `entry_to_sink` stage operates on any entry point. A chain can start at an MCP tool, a CLI script, or a Streamlit app, and reach a sink through same-file calls.
14. **`flow_kind` on every edge is mandatory.** Every edge created in Phase 1 carries `control`, `data`, or `structural`. Missing `flow_kind` is a construction bug (ADR-0015).
15. **Structural edges do not participate in `find_chains`.** `guards` describes a defensive control, not a path. `provides_tool` is `control` and is traversed.
16. **`provides_tool` is control because the access path is the attack path.** "The agent can reach a server that exposes `delete_file`" puts `delete_file` in the agent's reachable action set. Whether a particular request invokes it is a runtime question. Classifying the edge as a declaration would hide reachable dangerous actions whenever invocation is indirect — dispatched by the MCP framework, selected by the model, or triggered by a prompt — which are exactly the cases the analysis exists to evaluate. `guards` stays `structural`: a guardrail is a defensive control, and no data or control flows from it to the guarded component along that edge. Phase 2 runtime monitoring will separate exposure from invocation (ADR-0015, Deferred).
17. **`data_flow_confirmed` is enrichable.** The generic `flow/` analyzers set it on `has_access_to`. Framework detectors set it on the edges they own (LangChain `uses_tool`/`uses_model`) and record `tool_data_flow` on tool nodes for the normalizer to copy onto the MCP edges it builds (1.2a.2). An explicit `"unknown"` in `EdgesRepository.upsert` currently overwrites an enriched value; no duplicate edge ids exist today (backlog 1.2a.2c).
18. **`FlowDecision` is the one flow contract.** Every analyzer and enrichment helper returns `FlowDecision(kind, evidence, source)`. `flow_kind_for(decision)` is the single mapping to `flow_kind`. No second implementation, no per-caller copy. The decision's `kind` is what `data_flow_confirmed` stores.
19. **Stage pipeline.** `normalize(ctx)` is a loop over 17 explicitly ordered `NormalizerStage` objects. `NormalizeContext` holds inputs, per-scan caches, and derived indexes. Each stage is independently testable; `test_stage_order.py` pins the order, including `untrusted_sources` after `entry_points`, `entry_to_sink` before `detector_merge`, `untrusted_source_writers` between `detector_merge` and `flow_kind`, and `dynamic_mcp_invocation` after `untrusted_source_writers` and `flow_kind`.
20. **Pattern registry.** `patterns.py` is data (17 rules, 217 patterns, reasons, the `INTENTIONAL_OVERLAPS` allowlist); `patterns_registry.py` is matching semantics (`CallPattern.matches`, `matches_any`, the two validation invariants). Wildcards are not a concept in the matcher.

---

## Next

- **Phase 2 L3:** Enrichment (queue, workers, network clients).
- **Phase 3 L3:** Analysis, Reporting.
- **C4 Deployment:** Core + Agent topology.