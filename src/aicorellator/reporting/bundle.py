"""Report bundle writer.

Creates a directory with report.md, chains.json and
chain_groups.json. The `current` directory is the live report; archived
reports go into timestamped directories.

Layout under the workspace:
    <workspace.root>/reports/current/
        report.md
        chains.json
        chain_groups.json
    <workspace.root>/reports/20261010_203000/
        report.md
        chains.json
        chain_groups.json
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ..graph.models import Chain, ChainGroupRecord
from ..graph.store import GraphStore
from ..workspace import Workspace
from .markdown import group_chain_paths, render_report


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

    # chain_groups.json — the group half of the bundle (1.3e). Written
    # even when there are no groups: an empty array is the honest answer
    # to "what groups does this workspace have", and a consumer does not
    # have to test for a missing file to learn that.
    groups = store.chain_groups.list(status="active")
    paths = group_chain_paths(store, groups)
    groups_data = [_group_to_dict(store, g, paths) for g in groups]
    (current / "chain_groups.json").write_text(
        json.dumps(groups_data, indent=2, ensure_ascii=False),
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


def _group_to_dict(
    store: GraphStore,
    group: ChainGroupRecord,
    paths: dict[str, list[str]],
) -> dict:
    """Serialize a chain group.

    The row's own columns plus what makes it readable: the anchor as a
    node reference and each chain as its node references, the way
    `_chain_to_dict` names the nodes of a chain. `write_chain_ids` and
    `read_chain_ids` are kept next to the resolved chains — a consumer
    that keys on ids (or that wants to check a path against them) does not
    have to re-parse the nodes list.

    Two of those columns used to be dropped, which made the file silent
    about facts the row carries (B.6): `lethal_trifecta` — the projection
    of 1.3d's per-node flag onto the read side, `False` on a row no
    evaluation has reached — and `validated_kind`, A.4's label for the
    Layer 3 check the verdict travelled with. Both keys are always
    emitted, so a consumer never has to tell "false" from "not asked";
    the label is `null` until a verdict exists, because a row 1.3c wrote
    has not been through the check at all (see the comment at the
    assignment). B.7's `prompt_version` and `voter_outputs` are not
    here: they are that item's schema change, not this row's shape.

    A chain whose path is not in `paths` (see `group_chain_paths`) is
    emitted with `nodes: []` rather than dropped: the id is still the
    durable reference.
    """
    source = store.nodes.get(group.untrusted_source_id)
    if source is None:
        source_ref: dict = {"id": group.untrusted_source_id}
    else:
        source_ref = {
            "id": source.id,
            "kind": source.kind,
            "name": source.name,
            "source_type": source.attributes.get("source_type"),
        }

    return {
        "id": group.id,
        "name": group.name,
        "untrusted_source": source_ref,
        "write_chains": _group_chains_to_dict(
            store, group.write_chain_ids, paths
        ),
        "read_chains": _group_chains_to_dict(
            store, group.read_chain_ids, paths
        ),
        "write_chain_ids": group.write_chain_ids,
        "read_chain_ids": group.read_chain_ids,
        "score": group.score,
        "score_components": group.score_components,
        "priority": group.priority,
        # B.6: the projection of 1.3d's per-node flag onto the read side,
        # and A.4's label for the Layer 3 check the verdict travelled
        # with. Both are the row's, which is why they sit beside the
        # verdict rather than inside the chains.
        "lethal_trifecta": group.lethal_trifecta,
        "verdict": group.verdict,
        "confidence": group.confidence,
        "validated": group.validated,
        # The label travels with the verdict (the repository's rule): a
        # row with no verdict has not been through Layer 3, so there is
        # no level to name. `from_row` reads a NULL label back as
        # "existence" — the honest answer for a *chain* row, which exists
        # only once it was evaluated; a group row 1.3c wrote is not one
        # of those, and "existence" there would read as a check that ran.
        # So the file mirrors the stored column, NULL included.
        "validated_kind": (
            group.validated_kind if group.verdict is not None else None
        ),
        "reasoning": group.reasoning,
        "status": group.status,
    }


def _group_chains_to_dict(
    store: GraphStore,
    chain_ids: list[str],
    paths: dict[str, list[str]],
) -> list[dict]:
    """One side of a group as `[{id, nodes}]`, nodes named when resolved."""
    chains = []
    for chain_id in chain_ids:
        nodes = []
        for node_id in paths.get(chain_id, []):
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
        chains.append({"id": chain_id, "nodes": nodes})
    return chains