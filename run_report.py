"""Regenerate the report from the current workspace, without rescanning."""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from aicorellator import GraphStore
from aicorellator.reporting import write_bundle
from aicorellator.workspace import open_workspace


ws = open_workspace()          # ← без clean! Сохраняем chains.
print(f"Workspace: {ws.root}")

with GraphStore(ws.db_path) as store:
    print(f"Nodes: {store.nodes.count()}")
    print(f"Edges: {store.edges.count()}")
    print(f"Chains: {store.chains.count(status='active')}")

    report_path = write_bundle(store, ws)
    print(f"Report: {report_path}")