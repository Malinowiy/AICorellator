# False Positive / True Positive Analysis — FinBot

**Date:** 2026-10-13
**Source:** Manual review of the FinBot Phase 3 report (180 chains).
**Summary:** ~160 of 180 chains are false positives. This document records concrete examples and the structural observations that explain why.

---

## Part 1 — False positives

### FP #1 — Chain 28, `POST /invoices`

Path:

    entry_point:POST /invoices
      → agent:OrchestratorAgent
      → agent:InvoiceAgent
      → mcp_server:findrive
      → tool:search_files
      → sink:delete_file

Report verdict: `exploitable`, confidence 0.85.

**Why it is a false positive:**

1. **Read tool ≠ write tool.** `search_files` is a read/search operation. `delete_file` is a separate tool on the same MCP server. The edge `has_access_to` between them means only that both are declared in the same module/server. No edge shows that `search_files` calls `delete_file` or passes arguments to it.

2. **The model contradicts itself on a neighbouring chain.** On Chain 21 (same entry point, structurally identical: `findrive → get_file_count → delete_file`), the model writes: "the tool `get_file_count` has access to the sink, but the tool itself does not perform the deletion; rather, the tool's existence and the sink's proximity suggest the codebase supports it" — and returns `not_exploitable` with confidence 0.85. The model knows the right reasoning; it applies it inconsistently.

3. **No data flow.** The chain has no edge or attribute showing that `invoice_id` (or any attacker-controlled input) reaches an argument of `delete_file`. The model hypothesizes data flow ("if the attacker can manipulate…") and then treats the hypothesis as fact.

**Correct verdict:** `not_exploitable`, confidence ≈ 0.85.

**What failed in the prompt:** no requirement to distinguish capability colocation from invocation. The model sees `has_access_to` and reads it as an execution path.

---

### FP #2 — Chain 2, `CLI:scripts/test_websocket.py`

Path:

    entry_point:CLI:scripts/test_websocket.py
      → sink:check_docker

Report verdict: `exploitable`, confidence 0.90.

**Why it is a false positive:**

1. **Test script, not an entry point.** `test_websocket.py` is part of the test suite, run by a developer locally or in CI. No external attacker reaches it. The model writes: "The entry point is a Python test script, which typically implies an interactive or automated testing context where external input… can directly influence backend logic." That is speculation, not a structural property.

2. **`check_docker` as a sink is a check, not an exploit.** The name suggests it checks Docker availability (`docker ps` or a ping). Without code we cannot claim it accepts external arguments. Even if it does, this is test code, not a production path.

3. **Confidence 0.90 with one hop and zero data-flow information is unjustified.** With this much missing, `uncertain` at confidence ≈ 0.3 is the ceiling.

**Correct verdict:** `not_exploitable`, confidence ≈ 0.9.

**What failed in the prompt:** no classifier for production vs test context; no rule that a CLI script under `scripts/` is not a network entry point.

---

### FP #3 — Chain 11, `POST /invoices/{invoice_id}/reprocess`

Path:

    entry_point:POST /invoices/{invoice_id}/reprocess
      → agent:OrchestratorAgent
      → agent:FraudComplianceAgent
      → mcp_server:findrive
      → tool:search_files
      → sink:delete_file

Report verdict: `not_exploitable`, confidence 0.90 — and this is a false positive in the opposite direction.

**Why it is inconsistent, not correct:**

The model writes: "The verdict 'false' is assigned because without evidence of a specific vulnerability class… we assume the standard defense-in-depth might rely on external context not shown." That is reasonable — but it was not applied to Chain 28 above, which has identical structure. The model gives opposite verdicts on the same pattern. This is sampling noise, not signal.

**Lesson:** the current scheme does not ground the verdict. The same structure produces different answers depending on wording.

---

## Part 2 — True positives

"Candidate true positive" here means: there is a concrete vulnerability hypothesis worth checking in the MCP server's code. Without auditing `systemutils` / `findrive`, these are still hypotheses — but plausible ones, unlike the FPs above.

### TP #1 — Chain 9, `POST /copilot/chat`

Path:

    entry_point:POST /copilot/chat
      → agent:CoPilotAssistant
      → agent:OrchestratorAgent
      → agent:FraudComplianceAgent
      → mcp_server:systemutils
      → tool:rotate_logs
      → sink:execute_script

Report verdict: `exploitable`, confidence 0.90.

**Why this is a serious hypothesis:**

1. **Entry point is a chat interface.** `/copilot/chat` takes free text. Classic prompt-injection vector. Unlike `/invoices`, where the input is structured, here the attacker controls natural language that directly influences tool selection.

2. **Sink is code execution.** `execute_script` with category `code_execution` — not a file operation, code execution. The chain reaches it through `rotate_logs`. The name is suspicious: log rotation is often implemented through shell wrappers (`logrotate`, `gzip`, `mv`). If arguments (path, pattern) are assembled from LLM output without a strict allow-list, that is command injection.

3. **Three agents in a row without a guardrail node.** `CoPilotAssistant → OrchestratorAgent → FraudComplianceAgent`. Each step is an LLM deciding the next action. If any step lacks a strict system prompt limiting its tool set, the attacker can push the chain through prompt injection.

4. **No guardrail node in the graph.** Absence of a node is not absence of validation, but combined with a chat interface and an RCE sink, this is a concrete hypothesis: "can a user, through chat, make `FraudComplianceAgent` call `rotate_logs` with controlled arguments?"

**What to check in code:**

- `mcp/servers/systemutils`: how `rotate_logs` builds its command. If `subprocess.run(f"logrotate {path}", shell=True)` — RCE confirmed.
- System prompts of the agents: is there a per-agent tool allow-list?
- Argument validation at the MCP server level.

**Why TP, not FP:** there is an explicit source (free text), an explicit sink (code execution), and no visible barrier between them. This is the configuration where FP and TP differ by the *presence* of data flow, not its absence. The hypothesis is falsifiable.

---

### TP #2 — Chain 13, `POST /invoices`

Path:

    entry_point:POST /invoices
      → agent:OrchestratorAgent
      → agent:FraudComplianceAgent
      → mcp_server:systemutils
      → tool:database_maintenance
      → sink:execute_script

Report verdict: `exploitable`, confidence 0.85.

**Why this is a serious hypothesis:**

1. **`database_maintenance` is a typical shell wrapper.** Maintenance tools in MCP servers are often wrappers over `psql`, `pg_dump`, `mysql`, `redis-cli`. If the database name, dump path, or flags come from LLM output arguments, that is command injection.

2. **Sink is `execute_script`, code execution.** Same as TP #1.

3. **Short chain without a guardrail.** Entry → Orchestrator → FraudCompliance → MCP → tool → sink. No guardrail node, no visible validation step between agent and MCP.

4. **Entry point `/invoices` (POST) takes structured JSON, but fraud compliance usually handles free fields** (description, comment, metadata) that can be a prompt-injection vector into `FraudComplianceAgent`.

**What to check in code:**

- `database_maintenance` in `systemutils`: `subprocess`, `os.system`, or a parameterized call?
- Are arguments passed to the tool from LLM output without an allow-list?

**Why TP:** the chain ends in code execution through a tool whose name implies system operations. Structurally a meaningful hypothesis, not a "read tool next to a delete sink."

---

### TP #3 — Chain 3, `POST /invoices` (borderline)

Path:

    entry_point:POST /invoices
      → agent:OrchestratorAgent
      → agent:FraudComplianceAgent
      → mcp_server:systemutils
      → tool:read_config
      → sink:execute_script

**Why this is interesting:** `read_config` is semantically read-only, but the sink is code execution. Two scenarios:

- **TP scenario A:** `read_config` reads a file and does not execute code. The `execute_script` sink in the graph is colocation (then it is FP).
- **TP scenario B:** `read_config` parses a config and executes a hook/script from it (for example, reads `.env` and `eval`s variables, or reads YAML and calls `exec`). Then it is RCE through config injection.

**Why I list it as borderline:** the FP/TP distinction here is not in graph structure but in tool implementation. No prompt can resolve it without code access. The model should be able to say: "structurally reachable, but a verdict requires auditing `read_config`." Today it either says `exploitable` with an invented scenario, or `not_exploitable` with a hedge.

---

## Part 3 — Structural observations

1. **Capability colocation is read as invocation.** When two tools are declared on the same MCP server, the graph links them with `has_access_to`. The LLM reads this as an execution path. It is not.

2. **No data flow.** The graph has no edge or attribute that says "this input reaches this sink." The LLM hypothesizes data flow and then treats the hypothesis as fact.

3. **No taint source.** A chain can be "reachable" without any untrusted input entering it. The graph does not distinguish production paths from test paths, or attacker-controlled input from structured parameters.

4. **Inconsistent verdicts on identical structure.** Chains 28 and 11 have the same structure and get opposite verdicts. This is sampling noise, not signal — the current scheme does not ground the verdict in structure.

5. **Sparse graph.** FinBot: 283 nodes, 93 edges — ratio 0.33. Many real paths go through intermediate functions that are not classified and have no edges. Adding density *without* flow semantics would worsen the false-positive rate.

6. **The LLM can reason correctly when given the right question.** In FP #1, the model correctly identified colocation vs invocation on Chain 21. The prompt does not force this reasoning consistently.

---

## Implications for the backlog

- **1.2 — Control flow vs data flow.** Split `has_access_to` into `calls` (control) and `data_flows_to` (data). Add `flow_kind` on edges.
- **1.3 — Capability-based taint.** Entry point must have `reads_untrusted` / `http_ingress` / `cli_input`; path must contain `data_flows_to`; sink must have a sink capability.
- **1.4 — Prompt engineering.** Explicit calibration rules; require the LLM to answer structural questions (does data from the entry point reach the sink? what is the flow kind of each edge? is there a guardrail?); `uncertain` with concrete criteria; distinguish production from test context.
- **1.5 — Graph densification.** After 1.3a. Density without taint worsens FP.
- **1.6 — Lethal trifecta.** Cheap once `reads_untrusted` exists.

See `Documentation/BACKLOG.md` for priorities and dependencies.