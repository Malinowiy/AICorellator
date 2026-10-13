"""Run a full Phase 1 scan on a target and generate the initial report."""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from aicorellator import GraphStore
from aicorellator.ingestion import ingest, discover
from aicorellator.parsing import run_tsa, normalize, write_graph, set_phase1_done
from aicorellator.parsing.capability_extractor import extract as extract_capabilities
from aicorellator.parsing.detectors_runner import run as run_detectors
from aicorellator.parsing.entry_points import run as run_entry_points
from aicorellator.parsing.llm_extractor import run as run_llm_extractor
from aicorellator.reporting import write_bundle
from aicorellator.workspace import open_workspace


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a full Phase 1 scan on a target directory.",
    )
    parser.add_argument(
        "--target",
        required=True,
        help="path to the target directory to scan",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="workspace directory (default: ./aicorellator_data/)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="wipe the workspace before scanning",
    )
    parser.add_argument(
        "--frameworks",
        default=None,
        help="comma-separated list of framework detectors "
             "(default: all registered)",
    )
    args = parser.parse_args()

    target_path = Path(args.target).resolve()
    if not target_path.exists():
        print(f"error: target does not exist: {target_path}", file=sys.stderr)
        return 1
    if not target_path.is_dir():
        print(f"error: target is not a directory: {target_path}", file=sys.stderr)
        return 1

    enabled_frameworks: list[str] | None = None
    if args.frameworks:
        enabled_frameworks = [
            name.strip() for name in args.frameworks.split(",") if name.strip()
        ]

    ws = open_workspace(root=args.workdir, clean=args.clean)
    print(f"Workspace: {ws.root}")
    print(f"Target:    {target_path}")
    print()

    # --- Ingestion ---
    target = ingest(target_path)
    manifest = discover(target)
    print(f"Files discovered: {manifest.count()}")
    print(f"  python:         {len(manifest.by_language('python'))}")
    print(f"  yaml:           {len(manifest.by_language('yaml'))}")
    print(f"  toml:           {len(manifest.by_language('toml'))}")
    print()

    # --- Capabilities (AST-based, Python only) ---
    capabilities = {}
    for entry in manifest.files:
        if entry.language != "python":
            continue
        fc = extract_capabilities(entry.abs_path, entry.path)
        capabilities[entry.path] = fc
    print(f"Capabilities: {len(capabilities)} files")

    # --- TSA (AST symbols) ---
    tsa = run_tsa(manifest, target.root)

    # --- Entry points (routes, agent calls, MCP usages) ---
    ep = run_entry_points(manifest)

    # --- LLM extractor (configs) ---
    llm = run_llm_extractor(manifest)

    # --- Framework detectors ---
    detection = run_detectors(
        manifest,
        enabled=enabled_frameworks,
        host_id="localhost",
    )
    print(f"Framework detectors: {len(detection.nodes)} nodes, "
          f"{len(detection.edges)} edges")

    print()
    print(f"TSA:           {tsa.files_analyzed} analyzed, {tsa.files_failed} failed")
    print(f"Entry points:  {len(ep.entry_points)}")
    print(f"Agent calls:   {len(ep.agent_calls)}")
    print(f"MCP usages:    {len(ep.mcp_usages)}")
    print(f"LLM endpoints: {len(llm.deduplicated())}")
    print()

    # --- Normalization (merge everything into a graph delta) ---
    delta = normalize(
        manifest, tsa, ep, llm,
        capabilities=capabilities,
        framework_detection=detection,
    )

    # --- Write to SQLite ---
    with GraphStore(ws.db_path) as store:
        result = write_graph(store, delta)
        set_phase1_done(store)
        print(f"Wrote: {result['nodes_added']} nodes, "
              f"{result['edges_added']} edges")

    # --- Report ---
    with GraphStore(ws.db_path) as store:
        report_path = write_bundle(store, ws)
        print(f"Report: {report_path}")
        print(f"  nodes:  {store.nodes.count()}")
        print(f"  edges:  {store.edges.count()}")
        print(f"  chains: {len(store.query.find_chains())}")

    return 0


if __name__ == "__main__":
    sys.exit(main())