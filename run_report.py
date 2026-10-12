"""Regenerate the report from the current workspace. No rescanning."""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from aicorellator import GraphStore
from aicorellator.reporting import write_bundle
from aicorellator.workspace import open_workspace


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the report from the current workspace.",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="workspace directory (default: ./aicorellator_data/)",
    )
    args = parser.parse_args()

    ws = open_workspace(root=args.workdir)
    print(f"Workspace: {ws.root}")

    with GraphStore(ws.db_path) as store:
        print(f"Nodes:  {store.nodes.count()}")
        print(f"Edges:  {store.edges.count()}")
        print(f"Chains: {store.chains.count(status='active')}")

        path = write_bundle(store, ws)
        print(f"Report: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())