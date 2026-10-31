**v0.1 — prototype (2026-10-22).** Phase 1 (graph construction) works. Phase 3 (LLM evaluation) works. Phase 2 (runtime enrichment) is an interface without implementations.

The prototype has been validated against seven real targets:

| Target | Nodes | Edges | Chains |
|---|---|---|---|
| finbot-ctf | 283 | 93 | 182 |
| NMB-DAMN-Vulnerable-Support-Agent | 26 | 26 | 16 |
| dvmcp | 30 | 32 | 24 |
| mcploitable | 56 | 33 | 5 |
| AI-Vulnerabilities-Playground | 37 | 2 | 2 |
| damn-vulnerable-llm-agent | 9 | 6 | 1 |
| ai-goat | 10 | 7 | 7 |

**2026-10-30**
## 1. Main table — all eight scans

`chains` = `find_chains()` enumeration printed by `run_scan.py` (the `chains` **table** is empty after a scan — only `run_eval.py` writes rows; baseline `report.md` says "Candidate chains 182 / Evaluated chains 0", so this is apples-to-apples). `groups` = `run_eval.py --groups --limit 0` ("Groups enumerated").

| Corpus | Nodes | Edges | Chains | Groups | Stores | Guards | README baseline (n/e/c) | Diff (n/e/c) |
|---|---|---|---|---|---|---|---|---|
| finbot-ctf | **372** | **212** | **22** | 0 | 7 | **0** | 283/93/182 | **+89 / +119 / −160** |
| NMB-DAMN-Vulnerable-Support-Agent | **29** | **30** | **16** | 0 | 3 | **0** | 26/26/16 | +3 / +4 / 0 |
| dvmcp | **49** | **56** | **40** | 0 | 4 | **0** | 30/32/24 | +19 / +24 / +16 |
| mcploitable | **79** | **47** | **19** | 0 | 3 | **0** | 56/33/5 | +23 / +14 / +14 |
| ai-goat | **11** | **8** | **7** | 0 | 1 | **0** | 10/7/7 | +1 / +1 / 0 |
| AI-Vulnerabilities-Playground | **44** | **6** | **5** | 0 | 1 | **0** | 37/2/2 | +7 / +4 / +3 |
| damn-vulnerable-llm-agent | **12** | **8** | **1** | 0 | 3 | **0** | 9/6/1 | +3 / +2 / 0 |
| **self (AICorellator)** | **34** | **3** | **1** | 0 | **11** | **0** | — (5c2fa46: "11 stores, 0 store edges") | **confirmed exactly** |
| history | — | — | — | — | — | — | — | **skipped: PDF only** |

### Nodes by kind

| Corpus | entry_point | sink | tool | agent | store | mcp_server | untrusted_source | llm_endpoint | guardrail |
|---|---|---|---|---|---|---|---|---|---|
| finbot-ctf | 184 | 117 | 45 | 9 | 7 | 5 | 3 | 2 | 0 |
| NMB | 10 | 3 | 11 | 1 | 3 | 0 | 0 | 1 | 0 |
| dvmcp | 17 | 12 | 14 | 0 | 4 | 2 | 0 | 0 | 0 |
| mcploitable | 25 | 8 | 34 | 0 | 3 | 7 | 2 | 0 | 0 |
| ai-goat | 1 | 9 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| Playground | 31 | 9 | 0 | 0 | 1 | 0 | 1 | 1 | **1** |
| damn | 1 | 2 | 2 | 2 | 3 | 0 | 0 | 2 | 0 |
| self | 5 | 13 | 0 | 2 | 11 | 0 | 0 | 3 | 0 |

### Edges by kind

| Corpus | has_access_to | uses_tool | provides_tool | delegates_to | reads_from | writes_to | uses_model | guards |
|---|---|---|---|---|---|---|---|---|
| finbot-ctf | 104 | 44 | 44 | **0** | 15 | 5 | 0 | **0** |
| NMB | 6 | 9 | 0 | 10 | 0 | 4 | 1 | 0 |
| dvmcp | 34 | 0 | 0 | 14 | 0 | 8 | 0 | 0 |
| mcploitable | 13 | 0 | 0 | 33 | 0 | 1 | 0 | 0 |
| ai-goat | 7 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| Playground | 5 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| damn | 1 | 4 | 0 | 0 | 0 | 2 | 1 | 0 |
| self | 1 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |

### `identity_confidence` on `writes_to` / `reads_from` (min / median / max, n)

| Corpus | n | min | median | max | distribution |
|---|---|---|---|---|---|
| finbot-ctf | 20 | 0.8 | 0.8 | 0.8 | `{0.8}` |
| NMB | 4 | **0.608** | 0.608 | 0.748 | `{0.608 ×3, 0.748}` |
| dvmcp | 8 | 0.776 | 0.776 | 0.776 | `{0.776}` |
| mcploitable | 1 | 0.776 | 0.776 | 0.776 | `{0.776}` |
| ai-goat | 1 | 0.72 | 0.72 | 0.72 | `{0.72}` |
| Playground | 1 | 0.776 | 0.776 | 0.776 | `{0.776}` |
| damn | 2 | 0.776 | 0.776 | 0.776 | `{0.776}` |
| self | 0 | — | — | — | **no store edges** |

`identity_source`: **no `writes_to` / `reads_from` edge on any corpus carries the attribute** → zero LLM-fallback links, as configured (`llm_fallback.mode: off`). All store *nodes* are `confidence = 1.0`; the graded number lives on the link. Only NMB has a link below the `store.confidence_threshold: 0.7` (3 × 0.608 → "weak", excluded from default group enumeration).
