# scripts/serialize_last_scan.py
import json
import sys
from pathlib import Path
from aicorellator.models import Node, Edge, Provenance
from aicorellator.analysis.serializer import serialize_graph

scans_dir = Path.home() / ".aicorellator" / "scans"
if not scans_dir.is_dir():
    sys.exit(f"error: {scans_dir} not found - run a scan first")
scan_dirs = sorted(p for p in scans_dir.iterdir() if p.is_dir())
if not scan_dirs:
    sys.exit(f"error: no scans in {scans_dir}")
scan_dir = scan_dirs[-1]

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

# Only the nodes and edges relevant to the exfiltration question
text = serialize_graph(
    nodes, edges,
    include_kinds=["tool", "sink", "credential", "agent"],
    include_edge_kinds=["has_access_to", "provides_tool", "uses_client"],
)
print(text)