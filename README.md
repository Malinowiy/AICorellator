# AICorellator

**AI Infrastructure Attack Surface Correlator** — a correlation tool for AI infrastructure that finds **compromise chains** in systems with AI components.

Not a CVE scanner. Not SAST. Not a runtime IDS. The goal is to build a graph of AI tool interactions (agents, LLMs, MCP servers, tools, sinks), enrich its vertices with scanner data, and **evaluate chain exploitability entirely through LLMs**.

---

## What We Look For

A chain in which untrusted input reaches a dangerous action through AI components:

```mermaid
flowchart LR
    EP[entry_point] --> AG[agent]
    AG --> LLM[llm_endpoint]
    LLM --> MCP[mcp_server]
    MCP --> T[tool]
    T --> S[sink]
```

Each node alone is legitimate. The chain is not.

---

## Key Principles

| Principle | Meaning |
|---|---|
| **Graph is alive** | Single, mutable, no versioning. Nodes are added, enriched, marked `stale`. |
| **SQLite is the store** | No alternatives. Cheaper mutations, faster queries, no separate service. |
| **Scanners run on vertices** | Task queue. Each vertex → task. Workers run asynchronously. |
| **Staleness via `confidence`** | Float, not a boolean flag. A node not confirmed by dynamics loses confidence gradually. |
| **Chain evaluation is LLM-only** | No regex. The LLM receives the graph as text and returns chain + reasoning. |
| **Reports on demand** | Not automatic. A full run takes 20–30 minutes, expensive in electricity. |
| **Multi-host via Agent + Core** | One Agent per node. Three delivery adapters: Swarm, K8s, standalone. |

---

## Architecture

### Pipeline

```mermaid
flowchart TB
    subgraph P1["Phase 1 — Graph Construction"]
        SRC[Sources: code, configs] --> AST[AST parser TSA / SCIP]
        AST --> G[AI tool interaction graph]
    end

    subgraph P2["Phase 2 — Vertex Enrichment (async)"]
        Q[Task queue]
        Q --> W1[AAK: taint, MCP config, IPI]
        Q --> W2[LLM-scanner: model, provider]
        Q --> W3[MCP-scanner: tool schema]
        Q --> WD[Dynamic workers: change-detectors]
        W1 --> EN[Node enrichment: findings + attributes]
        W2 --> EN
        W3 --> EN
        WD --> EN
    end

    subgraph P3["Phase 3 — Chain Discovery & Evaluation (LLM-only)"]
        SER[Serialize graph to text] --> LLM[LLM voting ensemble]
        LLM --> CH[chain + reasoning + confidence + voters]
        CH --> TAG[Tagging: AKC phases + OWASP Agentic]
    end

    subgraph OUT["Reports"]
        CUR[current/] --> ARCH["&lt;timestamp&gt;/"]
    end

    P1 --> P2
    P2 --> P3
    P3 --> OUT
```

**Phase 1 — Graph Construction.**
Sources (code, configs) → AST parser (TSA/SCIP) → AI tool interaction graph. Nodes: `agent`, `llm_endpoint`, `mcp_server`, `tool`, `credential`, `sink`, `entry_point`. Edges: `uses_model`, `provides_tool`, `delegates_to`, `has_access_to`, `reaches_sink`.

**Phase 2 — Vertex Enrichment (async).**
Task queue. Static workers — AAK (findings on files: taint, MCP config, IPI), LLM-scanner (model, provider, endpoint), MCP-scanner (tool schema, capabilities). Dynamic workers (architectural option) — change-detectors that add new nodes/edges. Each result enriches a node (findings + attributes). Counters: `static.active_count`, `dynamic.last_completed_at`.

**Phase 3 — Chain Discovery and Evaluation (LLM-only).**
Graph → serialize to text → LLM voting ensemble → chain + reasoning + confidence + voters. Tagging by AKC phases and OWASP Agentic (ASI01–ASI10). Status: `active` / `potential` / `stale`.

**Reports.**
`current/` — current report bundle dir. On graph mutation: `current/` → `<timestamp>`, `report_fresh = false`. New report — on demand only.

### Multi-host

```mermaid
flowchart TB
    CORE[AICorellator Core<br/>SQLite + graph + LLM analysis + aggregation]

    subgraph A1["Node Agent 1"]
        D1[Local Docker daemon + FS]
    end
    subgraph A2["Node Agent 2"]
        D2[Local Docker daemon + FS]
    end
    subgraph AN["Node Agent N"]
        DN[Local Docker daemon + FS]
    end

    subgraph DEL["Delivery Adapters"]
        SW[Swarm Global Service]
        K8S[K8s DaemonSet]
        SA[Standalone systemd/ssh]
    end

    A1 -->|reports| CORE
    A2 -->|reports| CORE
    AN -->|reports| CORE
    SW -.deploys.-> A1
    K8S -.deploys.-> A2
    SA -.deploys.-> AN
```

One Agent — one node. Three delivery adapters: Docker Swarm Global Service, Kubernetes DaemonSet, standalone systemd/ssh. Core aggregates everything into a single graph keyed by `host_id`.

---

## Development Staging

```mermaid
flowchart TB
    P0["Phase 0 — Data Model<br/>SQLite schema + Python graph API"]
    P1["Phase 1 — AST + LLM<br/>graph population from code and configs"]
    P2["Phase 2 — Scanner Queue<br/>async queue + static workers"]
    P3["Phase 3 — Core + Agent + UI<br/>Core/Agent split, multi-host, minimal UI"]

    P0 --> P1 --> P2 --> P3

    P0 -.output.-> O0[graph/schema.sql + graph/store.py]
    P1 -.output.-> O1[Live graph in SQLite]
    P2 -.output.-> O2[Enriched graph, scan_done = true]
    P3 -.output.-> O3[Multi-host tool with an interface]
```

| Phase | What | Output |
|---|---|---|
| **0** | Data Model — SQLite schema + Python graph API (`nodes`, `edges`, `findings`, `hosts`, `state`, `task_state`; `confidence`, `host_id`, stale logic) | `graph/schema.sql` + `graph/store.py` |
| **1** | AST + LLM — AST parser (TSA → SCIP conditionally) + LLM extractor; graph population from code and configs | Live graph in SQLite |
| **2** | Scanner Queue — async queue + static workers; vertex enrichment with findings | Enriched graph, `scan_done = true` |
| **3** | Core + Agent + UI — Core/Agent split, multi-host, minimal UI, background mode, reports | Multi-host tool with an interface |

---

## Ontology

### Node Kinds

| Kind | Description | Example |
|---|---|---|
| `agent` | AI client or orchestrator | `InvoiceAgent`, `orchestrator` |
| `llm_endpoint` | Running LLM server | `openai:gpt-5-nano`, `ollama:11434` |
| `mcp_server` | MCP server | `findrive`, `systemutils` |
| `tool` | Tool exposed to an agent | `execute_script`, `delete_file` |
| `credential` | Secret visible to a tool | `AWS_SECRET`, `DB_URL` |
| `sink` | Dangerous action | `shell`, `filesystem`, `network` |
| `entry_point` | Entry point | HTTP route, WebSocket, form |

### Edge Kinds

| Kind | Description |
|---|---|
| `uses_model` | Agent uses an LLM endpoint |
| `provides_tool` | Server exposes a tool |
| `delegates_to` | Agent delegates to another agent |
| `has_access_to` | Tool has access to a credential or sink |
| `reaches_sink` | Path reaches a dangerous action |
| `called_by` | Reverse call relation |

### Chain Tagging

```mermaid
flowchart LR
    P1["Phase 1<br/>Semantic Infection<br/>entry_point / untrusted input"]
    P2["Phase 2<br/>Cognitive Compromise<br/>agent / llm_endpoint"]
    P3["Phase 3<br/>Agency Propagation<br/>tool / credential"]
    P4["Phase 4<br/>Systemic Execution<br/>sink"]

    P1 --> P2 --> P3 --> P4
```

**AKC phases (Agentic Kill Chain):**

| Phase | Name | Graph anchor |
|---|---|---|
| 1 | Semantic Infection | `entry_point`, untrusted input |
| 2 | Cognitive Compromise | `agent`, `llm_endpoint` |
| 3 | Agency Propagation | `tool`, `credential` |
| 4 | Systemic Execution | `sink` |

**OWASP Agentic (ASI01–ASI10):**

- ASI02 — Tool Misuse
- ASI05 — Unexpected Code Execution
- ASI07 — Insecure Inter-Agent Communication

---

## Lifecycle Management

### State Flags

| Flag | Set when | Meaning |
|---|---|---|
| `phase1_done` | AST parser finished | Graph built, `report --fast` possible |
| `scan_done` | All static workers finished | Graph enriched, `report --full` possible |
| `report_fresh` | After report generation | `current/` is current |
| `report_fresh = false` | On graph mutation | Graph moved ahead, report is stale |
| `dynamic_enabled` | Architectural option | Dynamic workers enabled |

### Task Counters

| Counter | Behavior |
|---|---|
| `static.active_count` | Drops to 0 → `scan_done = true` |
| `dynamic.last_completed_at` | Does not block `scan_done`, but must complete at least one cycle |

### Reports

```mermaid
stateDiagram-v2
    [*] --> Fresh: report generated
    Fresh --> Stale: graph mutated
    Stale --> Fresh: report regenerated
    Fresh --> Archived: new report generated
    Archived --> [*]
```

- **Generation on demand only.** Slow (20–30 minutes), expensive in resources.
- **Two tiers:** `report --fast` (after Phase 1, partial) and `report --full` (after Phase 2).
- **Archival:** `current/` → `<timestamp>/` on new generation.

### Re-runs

Content-hash of nodes → findings reused if hash matches. Only changed nodes are rescanned. Full rescan is not required.

---

## Current State

**Early alpha.** Development in staging.

| Component | Status |
|---|---|
| Data Model | DONE (Phase 0) |
| AST + LLM | DONE (Phase 1) |
| Scanner Queue | TODO (Phase 2) |
| Core + Agent + UI | TODO (Phase 3) |

---

## License

See `LICENSE`.