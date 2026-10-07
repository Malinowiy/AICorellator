# scripts/run_judge.py
import json
import sys
from pathlib import Path

import yaml

from aicorellator.models import Node, Edge, Provenance
from aicorellator.analysis.serializer import serialize_graph
from aicorellator.analysis.llm_judge import judge

# --- Locate latest scan ---
scans_dir = Path.home() / ".aicorellator" / "scans"
if not scans_dir.is_dir():
    sys.exit(f"error: {scans_dir} not found - run a scan first")
scan_dirs = sorted(p for p in scans_dir.iterdir() if p.is_dir())
if not scan_dirs:
    sys.exit(f"error: no scans in {scans_dir}")
scan_dir = scan_dirs[-1]

# --- Load question template ---
question_path = Path(__file__).resolve().parents[1] / "questions" / "q1_exfil.yaml"
if not question_path.is_file():
    sys.exit(f"error: question file not found: {question_path}")
with question_path.open() as f:
    question = yaml.safe_load(f)

# --- Load nodes ---
nodes = []
with (scan_dir / "nodes.jsonl").open() as f:
    for line in f:
        d = json.loads(line)
        d["provenance"] = [Provenance(**p) for p in d["provenance"]]
        nodes.append(Node(**d))

# --- Load edges ---
edges = []
with (scan_dir / "edges.jsonl").open() as f:
    for line in f:
        d = json.loads(line)
        d["provenance"] = [Provenance(**p) for p in d["provenance"]]
        edges.append(Edge(**d))

# --- Serialize per question slice ---
slice_cfg = question.get("slice", {})
graph_text = serialize_graph(
    nodes, edges,
    include_kinds=slice_cfg.get("include_nodes"),
    include_edge_kinds=slice_cfg.get("include_edges"),
    max_nodes=slice_cfg.get("max_nodes", 200),
)

print(graph_text)
print("---")

# --- Run judge ---
result = judge(graph_text, question["prompt"], edges)

if result.get("parse_error"):
    print(f"[judge] WARNING: {result['parse_error']}", file=sys.stderr)

print(json.dumps(result, indent=2, ensure_ascii=False))