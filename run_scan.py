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
from aicorellator.parsing import (
    make_context,
    normalize,
    run_classifier,
    run_tsa,
    set_phase1_done,
    write_graph,
)
from aicorellator.parsing.capability_extractor import extract as extract_capabilities
from aicorellator.parsing.capability_inheritance import apply_inheritance
from aicorellator.parsing.detectors_runner import run as run_detectors
from aicorellator.parsing.entry_points import run as run_entry_points
from aicorellator.parsing.framework_detectors.registry import all_names
from aicorellator.parsing.llm_classifier import (
    CACHE_FILENAME,
    ClassifierConfigError,
    ClassifierError,
)
from aicorellator.parsing.llm_extractor import run as run_llm_extractor
from aicorellator.reporting import write_bundle
from aicorellator.scan_config import ScanConfigError, load_scan_config
from aicorellator.store.llm_fallback import FallbackConfigError, FallbackError
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
        "--config",
        default=None,
        help="path to the config file "
             "(default: <workdir>/config/config.yaml)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="wipe the workspace state (db, reports, cache, logs) "
             "before scanning; config/ is kept",
    )
    args = parser.parse_args()

    target_path = Path(args.target).resolve()
    if not target_path.exists():
        print(f"error: target does not exist: {target_path}", file=sys.stderr)
        return 1
    if not target_path.is_dir():
        print(f"error: target is not a directory: {target_path}", file=sys.stderr)
        return 1

    ws = open_workspace(root=args.workdir)
    print(f"Workspace: {ws.root}")
    print(f"Target:    {target_path}")

    # Policy is a file, not flags: the scan-side sections of
    # `<workdir>/config/config.yaml`, unless `--config` points
    # somewhere else (ADR-0016).
    config_path = Path(args.config) if args.config else ws.config_path
    try:
        scan = load_scan_config(config_path)
    except (FileNotFoundError, ScanConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Policy:    {config_path}")

    # State is wiped after the policy is read, so a bad `--config`
    # never costs the workspace its database (`clean()` keeps
    # `config/` either way).
    if args.clean:
        ws.clean()

    enabled_frameworks = scan.resolve_detectors(all_names())
    print(f"Detectors: {', '.join(enabled_frameworks) or '(none)'}")
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
    # One level of base-class inheritance on top of the per-file
    # extraction (B.10b, ADR-0020): a subclass whose own body has no
    # call site still carries what its base's body shows, which is what
    # keeps it in the graph after ADR-0016 removed the name rules. Before
    # the classifier and `normalize()`, so every consumer of
    # `capabilities` reads the same set.
    capabilities = apply_inheritance(capabilities, manifest)
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

    # --- LLM classifier (Layer 1b, opt-in) ---
    # After the deterministic layers and before `normalize()`: it fills the
    # gaps they left, and its nodes are merged into the detector output that
    # `normalize` receives as `framework_detection` (ADR-0016, §Classification
    # layers). `off` by default — the layer is expensive, and a policy that
    # asked for it without configuring a model is an error, not a fallback.
    try:
        llm_layer = run_classifier(
            manifest=manifest,
            tsa_output=tsa,
            capabilities=capabilities,
            framework_detection=detection,
            scan=scan,
            config_path=config_path,
            cache_path=ws.cache_dir / CACHE_FILENAME,
            host_id="localhost",
        )
    except (ClassifierConfigError, ClassifierError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if scan.llm_classifier.mode != "off":
        # Merged into `detection` in place: `DetectorMergeStage` reads only
        # `.nodes` and `.edges`, and an LLM node entering that list is
        # already filtered to the symbols no deterministic layer classified.
        detection.nodes.extend(llm_layer.nodes)
        detection.edges.extend(llm_layer.edges)
        print(f"LLM classifier: {len(llm_layer.nodes)} nodes "
              f"(mode: {scan.llm_classifier.mode})")

    print()
    print(f"TSA:           {tsa.files_analyzed} analyzed, {tsa.files_failed} failed")
    print(f"Entry points:  {len(ep.entry_points)}")
    print(f"Symbol calls:  {len(ep.symbol_calls)}")
    print(f"MCP usages:    {len(ep.mcp_usages)}")
    print(f"LLM endpoints: {len(llm.deduplicated())}")
    print()

    # --- Normalization (merge everything into a graph delta) ---
    # The context carries the policy and the two paths the store-identity
    # fallback needs (`llm_fallback.mode`, `llm_fallback_model`, D.2's
    # cache file — the fallback shares it). `off` is the default, and the
    # stage's third step returns before it walks a family then.
    try:
        delta = normalize(make_context(
            manifest, tsa, ep, llm,
            capabilities=capabilities,
            framework_detection=detection,
            scan_config=scan,
            config_path=config_path,
            cache_path=ws.cache_dir / CACHE_FILENAME,
        ))
    except (FallbackConfigError, FallbackError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

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