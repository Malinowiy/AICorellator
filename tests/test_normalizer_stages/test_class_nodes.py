"""ClassNodeStage: a class symbol becomes a node of its classified kind.

The kind comes from the class body's capabilities (ADR-0009), not from
its name (ADR-0016, decision 7).
"""

from __future__ import annotations

from aicorellator.parsing.capability_extractor import (
    FileCapabilities,
    SymbolCapabilities,
)
from aicorellator.parsing.models import TSASymbol
from aicorellator.parsing.normalizer_stages import ClassNodeStage

AGENTS = "agents/invoice.py"


def _caps(*pairs: tuple[str, str]) -> dict[str, FileCapabilities]:
    """Capabilities for named classes in `AGENTS` (ADR-0009)."""
    return {
        AGENTS: FileCapabilities(
            file_path=AGENTS,
            symbols=[
                SymbolCapabilities(
                    symbol_name=name,
                    file_path=AGENTS,
                    capabilities={capability},
                )
                for name, capability in pairs
            ],
        ),
    }


def _class_symbol(name: str) -> TSASymbol:
    return TSASymbol(
        name=name,
        kind="class",
        file_path=AGENTS,
        line_start=10,
        line_end=50,
    )


def test_class_symbol_becomes_an_agent_node(project) -> None:
    """A class whose body calls an LLM is an agent node."""
    p = project(
        files={AGENTS: ""},
        symbols=[_class_symbol("InvoiceAgent")],
        capabilities=_caps(("InvoiceAgent", "calls_llm")),
    )

    delta = ClassNodeStage().apply(p.delta, p.ctx)

    assert [(n.kind, n.name) for n in delta.nodes] == [
        ("agent", "InvoiceAgent"),
    ]
    assert delta.nodes[0].attributes["source_file"] == AGENTS
    assert delta.nodes[0].attributes["line_start"] == 10


def test_class_symbol_becomes_a_sink_node(project) -> None:
    """A class whose body writes is a sink, whatever it is called."""
    p = project(
        files={AGENTS: ""},
        symbols=[_class_symbol("RandomHelper")],
        capabilities=_caps(("RandomHelper", "writes_filesystem")),
    )

    delta = ClassNodeStage().apply(p.delta, p.ctx)

    assert [(n.kind, n.name) for n in delta.nodes] == [
        ("sink", "RandomHelper"),
    ]
    assert delta.nodes[0].attributes["category"] == "filesystem"


def test_agent_name_without_a_capability_is_not_a_node(project) -> None:
    """The class-A suffix rule is gone (ADR-0016, decision 7).

    `InvoiceAgent` used to be an agent because of its name. It is a node
    now only if its body says so, or if a framework detector saw it.
    """
    p = project(
        files={AGENTS: ""},
        symbols=[_class_symbol("InvoiceAgent")],
    )

    delta = ClassNodeStage().apply(p.delta, p.ctx)

    assert delta.nodes == []
