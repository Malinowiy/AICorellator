# scripts/run_judge.py
import json
from pathlib import Path
from aicorellator.models import Node, Edge, Provenance
from aicorellator.analysis.serializer import serialize_graph
from aicorellator.analysis.llm_judge import judge

scan_dir = sorted((Path.home() / ".aicorellator" / "scans").iterdir())[-1]

# --- Загрузка узлов ---
nodes = []
with (scan_dir / "nodes.jsonl").open() as f:
    for line in f:
        d = json.loads(line)
        d["provenance"] = [Provenance(**p) for p in d["provenance"]]
        nodes.append(Node(**d))

# --- Загрузка рёбер ---
edges = []
with (scan_dir / "edges.jsonl").open() as f:
    for line in f:
        d = json.loads(line)
        d["provenance"] = [Provenance(**p) for p in d["provenance"]]
        edges.append(Edge(**d))

# --- Сериализация ---
graph_text = serialize_graph(
    nodes, edges,
    include_kinds=["agent", "tool", "sink", "credential"],
    include_edge_kinds=["has_access_to", "provides_tool", "uses_client", "uses_model"],
    max_nodes=30,
)

print(graph_text)
print("---")

PROMPT = """
You are analyzing an AI infrastructure graph for exfiltration risks.

GRAPH:
{graph_text}

TASK: Find chains where a tool can move data to an external sink.
Return JSON: {{"chains": [{{"chain": ["tool_id", "has_access_to", "sink_id"], "reasoning": "...", "confidence": 0.0}}]}}
If none, return {{"chains": []}}.
"""

findings = judge(graph_text, PROMPT, edges)
print(json.dumps(findings, indent=2, ensure_ascii=False))