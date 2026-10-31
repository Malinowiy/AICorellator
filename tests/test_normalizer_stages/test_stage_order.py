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
  - `body_attributes` runs last but three, because the merge replaces
    nodes and the replacement is what the graph keeps;
  - `lethal_trifecta` runs last but two: it shares the "after the merge"
    constraint and has no other, and it reads capabilities rather than
    source, which is why it is not a branch inside `body_attributes`
    (see `normalizer_stages.lethal_trifecta`).
  - `guardrail_edges` runs last: it shares the same "after the merge"
    constraint — the merge is what puts a `guardrail` node in the delta —
    and no other, and it emits `structural` edges only, so `flow_kind`'s
    position is not a constraint on it either way (see
    `normalizer_stages.guardrail_edges`).
  - `store_resolver` runs before it, and after the merge for the same
    reason: it resolves both ends of a store link — the component a
    write call sits in, and the `untrusted_source` a read belongs to —
    against the delta, so the node set has to be final. It shares the
    merge constraint with `guardrail_edges` and nothing else, and
    neither reads what the other writes (see
    `normalizer_stages.store_resolver`).
  - `guardrail_capability` (B.4-gap-1) runs at 2, before the two node
    stages that read the capability map it writes. It is the only
    constraint on it: the stage reads `ctx.capabilities` and the flat
    call graph rather than the delta, and it emits no node or edge (see
    `normalizer_stages.guardrail_capability`).
"""

from __future__ import annotations

from aicorellator.parsing.normalizer_stages import STAGES
from aicorellator.parsing.normalizer_stages.base import NormalizerStage

EXPECTED = (
    "mcp_server_nodes",
    "guardrail_capability",
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
    "store_resolver",
    "guardrail_edges",
)


def test_stages_are_in_the_documented_order() -> None:
    assert tuple(stage.name for stage in STAGES) == EXPECTED


def test_stage_count_is_twenty_one() -> None:
    """The count is quoted in the per-stage docstrings (`N of 21`)."""
    assert len(STAGES) == 21


def test_every_stage_satisfies_the_protocol() -> None:
    """Each stage is `apply(delta, ctx) -> delta` with a name."""
    for stage in STAGES:
        assert isinstance(stage, NormalizerStage)
        assert isinstance(stage.name, str) and stage.name


def test_stage_names_are_unique() -> None:
    names = [stage.name for stage in STAGES]
    assert len(names) == len(set(names))


def test_guardrail_edges_runs_last() -> None:
    """Last of the pipeline: the node set is final there, and B.4a reads
    the delta and the call graph rather than anything a later stage would
    produce."""
    assert STAGES[-1].name == "guardrail_edges"


def test_guardrail_capability_precedes_the_node_stages() -> None:
    """B.4-gap-1's only ordering constraint.

    The stage writes a capability into `ctx.capabilities`, and
    `class_nodes` / `method_nodes` are what read that map and turn it into
    nodes. It reads capabilities and the call graph rather than the
    delta, so nothing else constrains it — but it must be before both.
    """
    names = [stage.name for stage in STAGES]
    assert (
        names.index("mcp_server_nodes")
        < names.index("guardrail_capability")
        < names.index("class_nodes")
        < names.index("method_nodes")
    )


def test_guardrail_capability_precedes_the_guardrail_edges() -> None:
    """The producer runs before the emitter (B.4a reads the node set)."""
    names = [stage.name for stage in STAGES]
    assert (
        names.index("guardrail_capability")
        < names.index("detector_merge")
        < names.index("guardrail_edges")
    )


def test_lethal_trifecta_precedes_guardrail_edges() -> None:
    """Both run after the merge; neither reads what the other writes."""
    names = [stage.name for stage in STAGES]
    assert (
        names.index("detector_merge")
        < names.index("lethal_trifecta")
        < names.index("guardrail_edges")
    )


def test_guardrail_edges_follows_the_detector_merge() -> None:
    """A framework detector or the LLM classifier can produce the
    `guardrail` node the resolver links from, and the merge is what puts
    it in the delta (`normalizer_stages.guardrail_edges`)."""
    names = [stage.name for stage in STAGES]
    assert names.index("detector_merge") < names.index("guardrail_edges")


def test_store_resolver_follows_the_merge_and_precedes_guardrails() -> None:
    """Both need the final node set, and neither needs the other.

    `store_resolver` resolves a write call to the component node it sits
    in and a read site to its `untrusted_source`; the merge (stage 10)
    re-points both, so it cannot run earlier. `guardrail_edges` documents
    itself as the last slot, and the store resolver has no claim on it,
    so it runs before it (`normalizer_stages.store_resolver`).
    """
    names = [stage.name for stage in STAGES]
    assert (
        names.index("detector_merge")
        < names.index("store_resolver")
        < names.index("guardrail_edges")
    )


def test_store_resolver_runs_after_the_read_sites() -> None:
    """`reads_from` needs the `untrusted_source` nodes (1.3a, stage 6)."""
    names = [stage.name for stage in STAGES]
    assert names.index("untrusted_sources") < names.index("store_resolver")


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
