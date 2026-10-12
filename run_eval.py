"""First real Phase 3 run: evaluate candidate chains on FinBot."""

import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from aicorellator import GraphStore
from aicorellator.analysis.config_loader import load_config
from aicorellator.analysis.evaluator import evaluate_chains
from aicorellator.workspace import open_workspace


parser = argparse.ArgumentParser()
parser.add_argument(
    "--mode",
    default="fast",
    choices=["fast", "solo", "full", "custom"],
    help="ensemble mode",
)
parser.add_argument(
    "--limit",
    type=int,
    default=10,
    help="number of chains to evaluate",
)
args = parser.parse_args()

parser.add_argument(
    "--workdir",
    default=None,
    help="workspace directory (default: ./aicorellator_data/)",
)
args = parser.parse_args()

ws = open_workspace(root=args.workdir)
config = load_config(ws.models_config_path, mode=args.mode)

print(f"Mode: {config.mode}")
print(f"Voters: {len(config.voters)}")
for v in config.voters:
    print(f"  - {v.name} (weight={v.weight}, timeout={v.timeout})")
print(f"Judge: {config.judge.name if config.judge else 'none'}")
print(f"Parallel: {config.is_parallel_safe}")
print()

with GraphStore(ws.db_path) as store:
    candidates = store.query.find_chains(max_hops=10)
    print(f"Total candidates: {len(candidates)}")

    sample = candidates[: args.limit]
    print(f"Evaluating {len(sample)} chains...")
    print()

    stats = evaluate_chains(store, sample, config)

    print()
    print("=== Debug ===")
   # store.commit()
    print(f"chains after explicit commit: {store.chains.count()}")
    print(f"chains active: {store.chains.count(status='active')}")

    print()
    print("=== Stats ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    print()
    print("=== Evaluated chains ===")
    evaluated = store.chains.list(status="active", limit=args.limit)
    for c in evaluated:
        print(
            f"  {c.verdict:20} conf={c.confidence:.2f} "
            f"validated={c.validated} hops={c.length}"
        )
        print(f"    {c.reasoning[:300]}")
        print()