"""Phase 3 evaluation run: candidate chains, or selected chain groups.

    python run_eval.py --mode fast --limit 5
        evaluate the first 5 linear candidate chains

    python run_eval.py --groups --mode fast --limit 5
        enumerate, score and select chain groups (1.3c), then evaluate
        the first 5 of them (1.3e)
"""

import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from aicorellator import GraphStore
from aicorellator.analysis.config_loader import load_config
from aicorellator.analysis.evaluator import (
    evaluate_chain_groups,
    evaluate_chains,
)
from aicorellator.graph.query import MAX_GROUPS_SCORED
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
    help="number of chains (or chain groups with --groups) to evaluate",
)
parser.add_argument(
    "--workdir",
    default=None,
    help="workspace directory (default: ./aicorellator_data/)",
)
parser.add_argument(
    "--groups",
    action="store_true",
    help=(
        "evaluate chain groups instead of linear chains: enumerate "
        "find_chain_groups(), score and select with "
        "prioritize_chain_groups(), then evaluate the selected groups "
        "(1.3e, ADR-0010)"
    ),
)
parser.add_argument(
    "--max-groups",
    type=int,
    default=MAX_GROUPS_SCORED,
    help=(
        "enumerator cap for --groups. It must be >= the scoring cap, or "
        "the enumerator — not the ranker — decides what can be selected: "
        "the enumerator truncates in id order before scoring sees the "
        "groups (ADR-0012; flagged in the 1.3c report). The default "
        f"({MAX_GROUPS_SCORED}) matches the scoring cap."
    ),
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
    if args.groups:
        # The enumerator cap is raised on purpose: scoring only sees the
        # groups enumeration hands it.
        groups = store.query.find_chain_groups(max_groups=args.max_groups)
        print(f"Groups enumerated: {len(groups)}")

        selected = store.query.prioritize_chain_groups(groups)
        print(f"Groups selected for evaluation: {len(selected)}")

        # Store the selection before evaluating it: the rows are 1.3c's,
        # the verdicts are 1.3e's, and a run with a --limit smaller than
        # the selection should still record what was chosen.
        for group in selected:
            store.chain_groups.upsert(group.to_record())

        sample = selected[: args.limit]
        print(f"Evaluating {len(sample)} chain groups...")
        print()

        stats = evaluate_chain_groups(store, sample, config)

        print()
        print("=== Stats ===")
        for key, value in stats.items():
            print(f"  {key}: {value}")

        print()
        print(f"chain_groups rows: {store.chain_groups.count()}")
        print()
        print("=== Evaluated chain groups ===")
        for group in sample:
            record = store.chain_groups.get(group.group_id)
            if record is None:
                continue
            print(
                f"  {str(record.verdict):20} conf={record.confidence:.2f} "
                f"validated={record.validated} score={record.score:g} "
                f"priority={record.priority}"
            )
            print(f"    {record.reasoning[:300] if record.reasoning else ''}")
            print()
    else:
        candidates = store.query.find_chains(max_hops=10)
        print(f"Total candidates: {len(candidates)}")

        sample = candidates[: args.limit]
        print(f"Evaluating {len(sample)} chains...")
        print()

        stats = evaluate_chains(store, sample, config)

        print()
        print("=== Debug ===")
        print(f"chains after explicit commit: {store.chains.count()}")
        print(f"chains active: {store.chains.count(status='active')}")

        print()
        print("=== Stats ===")
        for key, value in stats.items():
            print(f"  {key}: {value}")

        print()
        print("=== Evaluated chains ===")
        evaluated = store.chains.list(status="active", limit=args.limit)
        for chain in evaluated:
            print(
                f"  {chain.verdict:20} conf={chain.confidence:.2f} "
                f"validated={chain.validated} hops={chain.length}"
            )
            print(f"    {chain.reasoning[:300]}")
            print()