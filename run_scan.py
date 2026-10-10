"""Full Phase 1 scan on FinBot, writing into the workspace."""

from aicorellator import GraphStore
from aicorellator.ingestion import ingest, discover
from aicorellator.parsing import run_tsa, normalize, write_graph, set_phase1_done
from aicorellator.parsing.entry_points import run as run_entry_points
from aicorellator.parsing.llm_extractor import run as run_llm_extractor
from aicorellator.reporting import write_bundle
from aicorellator.workspace import open_workspace


ws = open_workspace(clean=True)      # чистим workspace
print(f"Workspace: {ws.root}")

target = ingest('/Users/malinowiy/Documents/FinBot_CTF/finbot-ctf/finbot')
manifest = discover(target)
tsa = run_tsa(manifest, target.root)
ep = run_entry_points(manifest)
llm = run_llm_extractor(manifest)

print(f"Entry points: {len(ep.entry_points)}")
print(f"Agent calls:  {len(ep.agent_calls)}")
print()

delta = normalize(manifest, tsa, ep, llm)

with GraphStore(ws.db_path) as store:
    write_graph(store, delta)
    set_phase1_done(store)
    print(f"Wrote {len(delta.nodes)} nodes, {len(delta.edges)} edges")

with GraphStore(ws.db_path) as store:
    report_path = write_bundle(store, ws)
    print(f"Report: {report_path}")
    print(f"  nodes: {store.nodes.count()}")
    print(f"  edges: {store.edges.count()}")
    print(f"  chains: {len(store.query.find_chains())}")