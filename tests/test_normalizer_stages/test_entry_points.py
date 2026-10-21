"""EntryPointStage: entry_point nodes and the handler index."""

from __future__ import annotations

from aicorellator.parsing.entry_points import EntryPoint, EntryPointOutput
from aicorellator.parsing.normalizer_stages import EntryPointStage


def test_entry_point_nodes_and_handler_index(project) -> None:
    """An HTTP route and a CLI script get different ids and `entry_kind`s."""
    p = project(
        files={"app.py": "", "scripts/seed.py": ""},
        entry_points=EntryPointOutput(entry_points=[
            EntryPoint(
                method="POST", path="/api/chat", handler="chat",
                file_path="app.py", line=12,
            ),
            EntryPoint(
                method="CLI", path="", handler="<module>",
                file_path="scripts/seed.py", line=1,
            ),
        ]),
    )

    delta = EntryPointStage().apply(p.delta, p.ctx)

    by_name = {n.name: n for n in delta.nodes}
    assert set(by_name) == {"POST /api/chat", "CLI:scripts/seed.py"}
    assert "entry_kind" not in by_name["POST /api/chat"].attributes
    assert by_name["CLI:scripts/seed.py"].attributes["entry_kind"] == "cli"
    assert p.ctx.entry_node_by_handler == {
        "chat": by_name["POST /api/chat"].id,
        "<module>": by_name["CLI:scripts/seed.py"].id,
    }
