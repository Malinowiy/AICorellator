"""The pipeline order is part of the contract, so it is asserted.

The order encodes four facts that are easy to lose in a refactor:

  - node stages run before the edge stages that index their output;
  - `entry_to_sink` runs *before* the detector merge, because the merge
    invalidates the TSA node ids it links to (see
    `normalizer_stages.entry_to_sink`);
  - `untrusted_sources` runs after `entry_points` (a read inside a route
    handler resolves to the entry point node) and before the merge, for
    the same id reason as `entry_to_sink` (see
    `normalizer_stages.untrusted_sources`);
  - `untrusted_source_writers` runs *after* the merge, because the
    framework-detected `mcp_server` nodes it matches against only exist
    once `detector_merge` has run, and before `flow_kind`, so the one
    derivation classifies the edge it emits (see
    `normalizer_stages.untrusted_source_writers`);
  - `dynamic_mcp_invocation` runs after `untrusted_source_writers` (it
    walks the `writes_to` edges that stage emits) and after `flow_kind`,
    with the resolver group that passes `CONTROL_FLOW` (see
    `normalizer_stages.dynamic_mcp_invocation`);
  - `body_attributes` runs last but one, because the merge replaces
    nodes and the replacement is what the graph keeps;
  - `lethal_trifecta` runs last: it shares the "after the merge"
    constraint and has no other, and it reads capabilities rather than
    source, which is why it is not a branch inside `body_attributes`
    (see `normalizer_stages.lethal_trifecta`).
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
    "untrusted_sources",
    "llm_endpoints",
    "delegation_edges",
    "entry_to_sink",
    "detector_merge",
    "untrusted_source_writers",
    "flow_kind",
    "dynamic_mcp_invocation",
    "cross_file_resolution",
    "mcp_entry_synthesis",
    "orphan_entry",
    "body_attributes",
    "lethal_trifecta",
)


def test_stages_are_in_the_documented_order() -> None:
    assert tuple(stage.name for stage in STAGES) == EXPECTED


def test_stage_count_is_eighteen() -> None:
    """The count is quoted in the per-stage docstrings (`N of 18`)."""
    assert len(STAGES) == 18


def test_every_stage_satisfies_the_protocol() -> None:
    """Each stage is `apply(delta, ctx) -> delta` with a name."""
    for stage in STAGES:
        assert isinstance(stage, NormalizerStage)
        assert isinstance(stage.name, str) and stage.name


def test_stage_names_are_unique() -> None:
    names = [stage.name for stage in STAGES]
    assert len(names) == len(set(names))


def test_lethal_trifecta_runs_last() -> None:
    """Last of the pipeline: the node set is final there, and 1.3d reads
    capabilities rather than source, so it has no constraint that would
    place it earlier."""
    assert STAGES[-1].name == "lethal_trifecta"


def test_body_attributes_precedes_lethal_trifecta() -> None:
    """Both run after the merge; neither reads what the other writes."""
    names = [stage.name for stage in STAGES]
    assert (
        names.index("detector_merge")
        < names.index("body_attributes")
        < names.index("lethal_trifecta")
    )


def test_entry_to_sink_precedes_the_detector_merge() -> None:
    """`sinks_by_file` holds TSA ids; the merge invalidates them."""
    names = [stage.name for stage in STAGES]
    assert names.index("entry_to_sink") < names.index("detector_merge")


def test_writer_stage_follows_the_merge_and_precedes_flow_kind() -> None:
    """Framework-detected servers exist only after the merge; the one
    `flow_kind` derivation has to see the edge this stage emits."""
    names = [stage.name for stage in STAGES]
    assert (
        names.index("detector_merge")
        < names.index("untrusted_source_writers")
        < names.index("flow_kind")
    )


def test_dynamic_invocation_precedes_cross_file_resolution() -> None:
    """Both emit `uses_tool`, and each skips an id the delta already has.

    `dynamic_mcp_invocation` walks `writes_to` (stage 11) and runs with
    the post-`flow_kind` resolvers; `cross_file_resolution` is the
    resolver that resolves the same edge kind across modules, so it must
    be able to see — and skip — what this stage emitted.
    """
    names = [stage.name for stage in STAGES]
    assert (
        names.index("untrusted_source_writers")
        < names.index("flow_kind")
        < names.index("dynamic_mcp_invocation")
        < names.index("cross_file_resolution")
    )
