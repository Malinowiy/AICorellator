"""MethodNodeStage: nodes, plus the per-file tool/sink indexes."""

from __future__ import annotations

from aicorellator.parsing.models import TSASymbol
from aicorellator.parsing.normalizer_stages import MethodNodeStage

SERVER = "mcp/servers/findrive/server.py"


def test_method_symbols_are_indexed_by_file_and_kind(project) -> None:
    """A tool and a sink in one file land in the two indexes."""
    p = project(
        files={SERVER: ""},
        symbols=[
            TSASymbol(
                name="get_file", kind="method", file_path=SERVER,
                line_start=10, line_end=15, class_name="FinDriveServer",
            ),
            TSASymbol(
                name="delete_file", kind="method", file_path=SERVER,
                line_start=20, line_end=25, class_name="FinDriveServer",
            ),
        ],
    )

    delta = MethodNodeStage().apply(p.delta, p.ctx)

    assert {n.name: n.kind for n in delta.nodes} == {
        "get_file": "tool",
        "delete_file": "sink",
    }
    assert [n.name for n in p.ctx.tools_by_file[SERVER]] == ["get_file"]
    assert [n.name for n in p.ctx.sinks_by_file[SERVER]] == ["delete_file"]
    # Body attributes are BodyAttributeStage's job, not this stage's.
    assert "returns_data" not in p.named("get_file").attributes
    assert "sink_data_flow" not in p.named("delete_file").attributes
