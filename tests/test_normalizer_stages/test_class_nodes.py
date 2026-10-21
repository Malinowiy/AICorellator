"""ClassNodeStage: a class symbol becomes a node of its classified kind."""

from __future__ import annotations

from aicorellator.parsing.models import TSASymbol
from aicorellator.parsing.normalizer_stages import ClassNodeStage


def test_class_symbol_becomes_an_agent_node(project) -> None:
    """`InvoiceAgent` is classified as an agent, not as a plain class."""
    p = project(
        files={"agents/invoice.py": ""},
        symbols=[TSASymbol(
            name="InvoiceAgent",
            kind="class",
            file_path="agents/invoice.py",
            line_start=10,
            line_end=50,
        )],
    )

    delta = ClassNodeStage().apply(p.delta, p.ctx)

    assert [(n.kind, n.name) for n in delta.nodes] == [
        ("agent", "InvoiceAgent"),
    ]
    assert delta.nodes[0].attributes["source_file"] == "agents/invoice.py"
    assert delta.nodes[0].attributes["line_start"] == 10
