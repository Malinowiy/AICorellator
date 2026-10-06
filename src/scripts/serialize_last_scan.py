# scripts/serialize_last_scan.py
import json
from pathlib import Path
from aicorellator.models import Node, Edge, Provenance
from aicorellator.analysis.serializer import serialize_graph

scan_dir = sorted((Path.home() / ".aicorellator" / "scans").iterdir())[-1]

nodes = []
with (scan_dir / "nodes.jsonl").open() as f:
    for line in f:
        d = json.loads(line)
        d["provenance"] = [Provenance(**p) for p in d["provenance"]]
        nodes.append(Node(**d))

edges = []
with (scan_dir / "edges.jsonl").open() as f:
    for line in f:
        d = json.loads(line)
        d["provenance"] = [Provenance(**p) for p in d["provenance"]]
        edges.append(Edge(**d))

# Только узлы и рёбра, релевантные вопросу об эксфильтрации
text = serialize_graph(
    nodes, edges,
    include_kinds=["tool", "sink", "credential", "agent"],
    include_edge_kinds=["has_access_to", "provides_tool", "uses_client"],
)
print(text)