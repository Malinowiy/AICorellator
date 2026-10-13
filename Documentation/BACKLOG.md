# AICorellator — Backlog

Single registry of deferred, in-progress, and planned work. Complements the ADRs (which record *decisions*) with a working list of *what to do next*.

**Last updated:** 2026-10-13

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

## Parsing

### Cross-file tool → sink

**Priority:** High
**Status:** Not started
**Rationale:** Currently `has_access_to` (tool → sink) is built only within a single file (the B2 resolver, `_link_entry_points_to_sinks`). In mcploitable and dvmcp, 8–12 sink nodes have no incoming edges because the tools that reach them live in other files. This is the same blind spot as agent-to-tool, solved by Resolver 1.
**References:** ADR-0013 (Deferred, item 6), `resolve_agent_tool_edges` (Resolver 1).
**Approach:** Extend the call graph to span imports. Reuse the import table already collected in `entry_points._collect_module_imports`. BFS across module boundaries, not just within one file.

### `openai_agents.py` detector

**Priority:** High
**Status:** Not started
**Rationale:** Last detector in the ADR-0011 implementation order. The OpenAI Agents SDK is a rising framework; targets using it currently produce an empty graph.
**References:** ADR-0011 (Implementation Order, item 6).
**Approach:** Follow the FastMCP / LangChain detector pattern. Per-file. Detect `Agent`, `Runner`, `handoff`, `guardrail` patterns. Capabilities via `_caps_for_function`. Cross-file and entry synthesis are inherited from the normalizer.

### Additional TSA modules for non-Python languages

**Priority:** Low
**Status:** Deferred until a non-Python target appears
**Rationale:** All current validation targets are Python. Adding Go, Java, Rust, or TypeScript support has no consumer yet. The B2 resolver and detectors are Python-only today; extending them is significant work with no payoff until a real target requires it.
**References:** ADR-0002 (TSA choice), ADR-0001 (AST approach).
**Approach:** When a real non-Python target appears, add per-language detection modules. Do not speculatively build.

### Relative imports in cross-file resolution

**Priority:** Low
**Status:** Not started
**Rationale:** Relative imports (`from . import x`, `from .tools import y`) are skipped by the import collector because resolving them requires a package root, which the manifest does not provide. Rare in agent code; accepted limitation (ADR-0013).
**Approach:** Infer package root from `__init__.py` presence along the path. Extend `_collect_module_imports`.

### Per-name resolution in cross-file tool edges

**Priority:** Medium
**Status:** Not started
**Rationale:** Resolver 1 links an agent to *all* tools in an imported module, not just the tools in the imported name (`tools=USER_TOOLS` → all tools, not just the contents of `USER_TOOLS`). Accepted false-positive trade-off (ADR-0009), but tighter resolution would reduce Phase 3 load.
**References:** ADR-0013 (Deferred, item 2).
**Approach:** Follow the imported name to its assignment in the source module, resolve the list literal, link only to those tools.

---

## Analysis

### Chain groups (ADR-0010)

**Priority:** High
**Status:** Not started
**Rationale:** Indirect prompt injection requires chain groups, not linear chains. The `chain_groups` table exists in the schema; nothing populates it. This is the main missing piece for the project's second threat model.
**References:** ADR-0010, ADR-0012.
**Approach:** Add `untrusted_source` node kind (see Untrusted Sources), implement `find_chain_groups(untrusted_sources, sinks)`, populate `chain_groups`.

### Lethal trifecta detection

**Priority:** High
**Status:** Not started
**Rationale:** ADR-0009 defines the capability combination (`reads_untrusted` + `reads_secrets`/`reads_env` + `http_egress`/`writes_filesystem`/`executes_shell`). The sets exist in `patterns.py` (`LETHAL_TRIFECTA_READ_CAPS`, `LETHAL_TRIFECTA_SECRET_CAPS`, `LETHAL_TRIFECTA_EGRESS_CAPS`) but nothing combines them into a node-level flag. This is a deterministic, high-signal indicator.
**References:** ADR-0009 (Lethal Trifecta Detection).
**Approach:** On node write, compute a `lethal_trifecta` attribute. Chains passing through such nodes get `priority: high`. Requires `reads_untrusted` (see Untrusted Sources).

### Chain group prioritization (ADR-0012)

**Priority:** High
**Status:** Not started
**Rationale:** Naive `find_chain_groups` is O(n²) in the LLM evaluation cost. Deterministic scoring selects the top-N for LLM evaluation. Goes together with chain groups.
**References:** ADR-0012.
**Approach:** Cheap scoring (capabilities + structure), rank, top-N to LLM.

### Dynamic chain selection

**Priority:** Medium/Low
**Status:** Not started
**Rationale:** Built-in prioritization (ADR-0012) is sufficient for MVP. Interactive selection — an analyst picking which chains to evaluate — is a TUI feature, not a backend feature. Defer until TUI matures.
**References:** ADR-0012, TUI section.

### `reads_untrusted` capability

**Priority:** Medium
**Status:** Blocked by untrusted source detection
**Rationale:** Requires knowing what an untrusted source is (RAG corpus, MCP tool description, email inbox, user upload). The capability is defined in patterns but not extracted.
**References:** ADR-0010, ADR-0009.

### Small-model pre-scoring

**Priority:** Low
**Status:** Not started
**Rationale:** Optimization of Phase 3. Score candidates with a small LLM before running the full ensemble. Not needed at current chain counts.
**References:** ADR-0005 (Deferred, item 5).

---

## Guardrails

### `guards` edge emission

**Priority:** High
**Status:** Partially done (node kind only)
**Rationale:** ADR-0007 is marked "Partially Accepted". Guardrail nodes are created by the classifier; the `guards` edge is registered in the schema but no component emits it. Chain semantics do not yet reflect that a guardrail can block a chain.
**References:** ADR-0007 (Implementation Status).
**Approach:** Detect guardrail-to-agent or guardrail-to-tool relationships. Requires source-level analysis of what the guardrail filters.

### Guardrail attributes

**Priority:** High
**Status:** Not started
**Rationale:** `policy` and `enforcement_point` are defined but not populated. Goes together with `guards` edge emission.
**References:** ADR-0007.

### `bypassed_by` derived edge

**Priority:** Medium
**Status:** Blocked by `guards` edge
**Rationale:** Reverse of `guards`. Requires the edge to exist first.
**References:** ADR-0007.

### Guardrail-aware chain evaluation

**Priority:** Medium
**Status:** Not started
**Rationale:** Phase 3 should receive guardrail information and reason about whether a chain is blocked. Currently chains pass through guardrails without adjustment.
**References:** ADR-0007.

---

## Untrusted Sources

### `untrusted_source` node kind

**Priority:** High
**Status:** Not started
**Rationale:** Not in `VALID_NODE_KINDS`. Required for chain groups and for `reads_untrusted`.
**References:** ADR-0010.

### Untrusted source detection

**Priority:** High
**Status:** Not started
**Rationale:** RAG corpora, MCP tool descriptions, email inboxes, user uploads. Each is a distinct pattern; needs its own detection logic.
**References:** ADR-0010.

### `find_chain_groups` operation

**Priority:** High
**Status:** Not started
**Rationale:** The core operation for indirect prompt injection. Enumerates groups that share an `untrusted_source`.
**References:** ADR-0010, ADR-0012.

---

## Reporting

### SARIF output

**Priority:** Low
**Status:** Deferred until a consumer appears
**Rationale:** SARIF is needed for GitHub Security Dashboard integration. No consumer yet. Markdown + JSON is sufficient.
**References:** ADR-0004.

### Report tiers (`--fast` / `--full`)

**Priority:** Low
**Status:** Not started
**Rationale:** ADR-0004 defers this. Single report is generated today.
**References:** ADR-0004.

### Incremental reports

**Priority:** Low
**Status:** Not started
**Rationale:** Diff between two reports. Useful for tracking changes over time, not needed yet.
**References:** ADR-0004.

### Notifications

**Priority:** Low
**Status:** Not started
**Rationale:** Email / webhook on `report_fresh = false`. Needs a consumer.
**References:** ADR-0004.

---

## Runtime (Phase 2)

### DynamicWorker implementations

**Priority:** Icebox
**Status:** Deferred until Phase 2 begins
**Rationale:** Months of work. Requires privileged mode or an MCP proxy. The `DynamicWorker` interface exists in the architecture; no implementation. Not blocking Phase 1 delivery.
**References:** ADR-0001, ADR-0003 (Stage 4).

### MCP-audit-tool integration

**Priority:** Icebox
**Status:** Not started
**Rationale:** Dynamic MCP server scanner. Requires runtime infrastructure (isolated server startup, request emulation, result collection). Separate layer.
**Approach:** Findings become `findings` rows attached to nodes.

### garak integration

**Priority:** Icebox
**Status:** Not started
**Rationale:** LLM vulnerability scanner. Requires a working LLM session. Findings become `findings` rows.

### Enrichment queue + static workers

**Priority:** Icebox
**Status:** Deferred until Phase 2 begins
**Rationale:** AAK, LLM-scanner, MCP-scanner. The schema (`task_state` table, `scan_done` key) exists. No queue.
**References:** ADR-0003 (Stage 3).

### File watcher / Docker diff / OSSEC

**Priority:** Icebox
**Status:** Not started
**Rationale:** Runtime change detection. Requires DynamicWorker first.
**References:** ADR-0003 (Stage 4).

---

## Infrastructure

### Schema migrations

**Priority:** Icebox
**Status:** Deferred until the schema stabilizes
**Rationale:** The database is recreated per scan (ADR-0003). No migration has been needed. Migrations become relevant when (a) the schema stabilizes, (b) dynamic scanning is in production, and (c) a schema change is required.
**References:** ADR-0003, C4 L3 Phase 0 (Migration policy).

### SCIP integration

**Priority:** Icebox
**Status:** Deferred until a target requires it
**Rationale:** ADR-0002 proposed SCIP as a conditional precision layer. It is not implemented. All current targets are Python; TSA covers Python. SCIP adds precision for languages and cases where TSA is heuristic (cross-module type resolution). The cost — per-language toolchain, index maintenance, merge logic, matching against TSA — is high. Defer until a real project needs it. Per-language TSA modules (item 3 above) are a lighter alternative for new languages.
**References:** ADR-0002, ADR-0003 (Stage 2).

### Multi-host (Core + Agent)

**Priority:** Icebox
**Status:** Deferred
**Rationale:** Phase 3 topology. Single-host is sufficient for current targets.
**References:** ADR-0003, C4 L2.

### `structural_heuristics.py`

**Priority:** Cancelled
**Status:** Not started, will not start
**Rationale:** ADR-0011 originally proposed a Level 3 structural heuristics module. During implementation, its role was absorbed by `classification.py` (name-based heuristics). The ADR was revised to reflect this. No separate module.
**References:** ADR-0011 (Level 3, note).

---

## TUI

### TUI MVP: Scan / Graph / Chains

**Priority:** Medium
**Status:** Not started
**Rationale:** Operational control of the tool. The Scan screen shows live progress; the Graph screen shows nodes and edges; the Chains screen shows candidate chains. This is the analyst's working interface, more important than CLI ergonomics.
**Approach:** Textual-based. `src/aicorellator/tui/`. Screens as separate modules. Async tasks for long operations.

### TUI: Evaluate / Report / Config

**Priority:** Medium
**Status:** Not started
**Rationale:** After MVP. Evaluate runs the LLM ensemble with live progress; Report shows a preview and exports; Config edits `models.yaml` and detector selection.

### TUI: dynamic chain selection

**Priority:** Medium/Low
**Status:** Not started
**Rationale:** Goes with Dynamic chain selection (Analysis section). Analyst picks chains interactively for evaluation.

---

## CLI

### `cli.py` wrapper

**Priority:** Low
**Status:** Not started
**Rationale:** A thin wrapper that calls `run_scan`, `run_eval`, `run_report`. Convenience, not functionality. `python run_scan.py` already works.
**Approach:** Subcommands `scan`, `eval`, `report`. No logic of its own.

### Console script (`[project.scripts]`)

**Priority:** Low
**Status:** Not started
**Rationale:** Goes with `cli.py`. Enables `aicorellator scan ...` instead of `python run_scan.py ...`.
**References:** `pyproject.toml`.

### Standalone executable (mac + linux + win)

**Priority:** Icebox
**Status:** Not started
**Rationale:** Premature. A standalone binary would need to package Python, tree-sitter-analyzer, `uvx`, and all dependencies (~50–200 MB per platform). No users without Python exist yet. Worth doing when the project ships to end-users who do not run Python.
**Approach:** PyInstaller or Nuitka, plus packaging `uvx` / tree-sitter-analyzer. Three platform builds.

### Docker image

**Priority:** Icebox
**Status:** Not started
**Rationale:** Useful for CI/CD and isolated environments. Not needed yet.
**Approach:** Dockerfile, published to a registry.

### `run_*.py` wrappers

**Priority:** Low
**Status:** Not started
**Rationale:** Either keep as thin wrappers or remove once `cli.py` exists. Compatibility choice.

---

## Done (for reference)

Nothing is moved here automatically; entries stay in their category until closed. When a task is complete, delete its section or mark it done with the closing commit hash. This keeps the file from becoming an append-only archive.

---

## Maintenance

- **Update priorities** when external conditions change (a target appears, a consumer asks for a feature).
- **Update status** when work begins or ends.
- **Add new entries** when a deferred item surfaces during implementation, review, or validation.
- **Remove entries** when they are done or cancelled. The Done section is a courtesy, not a requirement.

The ADRs remain the authoritative record of *why* a decision was made. This file records *what to do next*.