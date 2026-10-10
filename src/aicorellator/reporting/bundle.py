"""Report bundle writer.

Creates a directory with report.md and chains.json. The `current`
directory is the live report; archived reports go into timestamped
directories.

Layout under the workspace:
    <workspace.root>/reports/current/
        report.md
        chains.json
    <workspace.root>/reports/20261010_203000/
        report.md
        chains.json
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ..graph.models import Chain
from ..graph.store import GraphStore
from ..workspace import Workspace
from .markdown import render_report


def write_bundle(
    store: GraphStore,
    workspace: Workspace,
    host_id: str = "localhost",
    archive_existing: bool = True,
) -> Path:
    """Write the report bundle into <workspace>/reports/current/.

    If archive_existing is True and current/ already exists, it is
    renamed to a timestamped directory before the new current is
    written.

    Returns the path to the current/ directory.
    """
    reports_dir = workspace.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    current = workspace.current_report_dir

    if archive_existing and current.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive = reports_dir / timestamp
        suffix = 1
        while archive.exists():
            archive = reports_dir / f"{timestamp}_{suffix}"
            suffix += 1
        shutil.move(str(current), str(archive))

    current.mkdir(parents=True, exist_ok=True)

    # report.md
    markdown = render_report(store, host_id=host_id)
    (current / "report.md").write_text(markdown, encoding="utf-8")

    # chains.json
    chains = store.chains.list(status="active")
    chains_data = [_chain_to_dict(store, c) for c in chains]
    (current / "chains.json").write_text(
        json.dumps(chains_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return current


def _chain_to_dict(store: GraphStore, chain: Chain) -> dict:
    """Serialize a chain with resolved node names for readability."""
    nodes = []
    for node_id in chain.chain_nodes:
        node = store.nodes.get(node_id)
        if node:
            nodes.append({
                "id": node.id,
                "kind": node.kind,
                "name": node.name,
                "source_file": node.attributes.get("source_file"),
            })
        else:
            nodes.append({"id": node_id})

    return {
        "id": chain.id,
        "length": chain.length,
        "verdict": chain.verdict,
        "confidence": chain.confidence,
        "validated": chain.validated,
        "prompt_version": chain.prompt_version,
        "nodes": nodes,
        "edges": chain.chain_edges,
    }