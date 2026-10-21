"""EntryToSinkStage: reachability from an entry point to a sink."""

from __future__ import annotations

from aicorellator.graph.models import Node
from aicorellator.parsing.entry_points import EntryPoint, EntryPointOutput
from aicorellator.parsing.normalizer_stages import EntryToSinkStage


def test_entry_point_reaches_sink_through_a_helper(project) -> None:
    """The handler calls a helper, the helper calls the sink."""
    p = project(
        files={"app.py": ""},
        entry_points=EntryPointOutput(
            entry_points=[EntryPoint(
                method="GET", path="/purge", handler="purge",
                file_path="app.py", line=10,
            )],
            call_graph={
                "purge": {"_purge_all"},
                "_purge_all": {"delete_file"},
            },
        ),
    )
    p.ctx.entry_node_by_handler = {"purge": "entry:1"}
    p.ctx.sinks_by_file = {"app.py": [
        Node(
            id="sink:1", kind="sink", name="delete_file",
            host_id="localhost", attributes={"source_file": "app.py"},
        ),
    ]}

    delta = EntryToSinkStage().apply(p.delta, p.ctx)

    assert [(e.kind, e.source_id, e.target_id) for e in delta.edges] == [
        ("has_access_to", "entry:1", "sink:1"),
    ]
    # Reachability, not dataflow (ADR-0015).
    assert delta.edges[0].flow_kind == "control"
    assert delta.edges[0].data_flow_confirmed == "unknown"
