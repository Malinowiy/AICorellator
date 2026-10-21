"""The pipeline order is part of the contract, so it is asserted.

The order encodes three facts that are easy to lose in a refactor:

  - node stages run before the edge stages that index their output;
  - `entry_to_sink` runs *before* the detector merge, because the merge
    invalidates the TSA node ids it links to (see
    `normalizer_stages.entry_to_sink`);
  - `body_attributes` runs last, because the merge replaces nodes and
    the replacement is what the graph keeps.
"""

from __future__ import annotations

from aicorellator.parsing.normalizer_stages import STAGES
from aicorellator.parsing.normalizer_stages.base import NormalizerStage

EXPECTED = (
    "mcp_server_nodes",
    "class_nodes",
    "method_nodes",
    "intra_file_edges",
    "entry_points",
    "llm_endpoints",
    "delegation_edges",
    "entry_to_sink",
    "detector_merge",
    "flow_kind",
    "cross_file_resolution",
    "mcp_entry_synthesis",
    "orphan_entry",
    "body_attributes",
)


def test_stages_are_in_the_documented_order() -> None:
    assert tuple(stage.name for stage in STAGES) == EXPECTED


def test_every_stage_satisfies_the_protocol() -> None:
    """Each stage is `apply(delta, ctx) -> delta` with a name."""
    for stage in STAGES:
        assert isinstance(stage, NormalizerStage)
        assert isinstance(stage.name, str) and stage.name


def test_stage_names_are_unique() -> None:
    names = [stage.name for stage in STAGES]
    assert len(names) == len(set(names))


def test_body_attributes_runs_last() -> None:
    """The merge replaces nodes; the replacement needs the backfill."""
    assert STAGES[-1].name == "body_attributes"


def test_entry_to_sink_precedes_the_detector_merge() -> None:
    """`sinks_by_file` holds TSA ids; the merge invalidates them."""
    names = [stage.name for stage in STAGES]
    assert names.index("entry_to_sink") < names.index("detector_merge")
