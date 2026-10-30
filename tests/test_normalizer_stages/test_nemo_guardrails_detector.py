"""The NeMo Guardrails detector through the normalizer (B.4-gap-3).

The detector's own tests are `tests/test_framework_nemo_guardrails.py`.
Two things need a `NormalizeContext` and the file-backed `project`
fixture, and both are about what happens to a detector node *after* the
detector:

  - **M1 — the merge.** `DetectorMergeStage` is keyed by
    `(name, source_file)` and makes the detector node authoritative for
    kind (ADR-0011, ADR-0024 §1). A TSA node of the same name in the same
    file is replaced, its capabilities move to the detector node, and the
    detector node's `observed_by` survives — which is what keeps the
    three producers distinguishable in the graph.

  - **X1 — B.4a.** `GuardrailEdgeStage` reads `delta.nodes` after the
    merge, so a detector `guardrail` node is a `guardrail` node to it.
    B.4-gap-3 does not change B.4a; these fixtures pin what B.4a does
    with this shape today, including the case where it emits nothing.

The pipeline order is the real one: the detector runs, the merge places
its nodes in the delta, and B.4a runs last on the merged node set.
"""

from __future__ import annotations

import ast

from aicorellator.graph.models import Node
from aicorellator.parsing.capability_extractor import (
    FileCapabilities,
    SymbolCapabilities,
)
from aicorellator.parsing.entry_points import run as run_entry_point_detector
from aicorellator.parsing.framework_detectors import DetectionResult
from aicorellator.parsing.framework_detectors.nemo_guardrails import (
    NeMoGuardrailsDetector,
)
from aicorellator.parsing.models import TSASymbol
from aicorellator.parsing.normalizer_stages import (
    ClassNodeStage,
    DetectorMergeStage,
    GuardrailEdgeStage,
)

AGENTS = "agents/bot.py"
RAILS_FILE = "core/rails/nemo.py"

# M1: a TSA class the detector's node collides with — same name, same
# file. The collision is contrived on purpose; the merge is keyed by
# `(name, source_file)`, and that is the only way to exercise it.
MERGE_SOURCE = """\
from nemoguardrails import LLMRails, RailsConfig


class rails:
    \"\"\"A project class named the way the variable is.\"\"\"

    def check(self, text):
        return text


config = RailsConfig.from_path("config")
rails = LLMRails(config)
"""

# X1a: the rails is constructed and then *used*. Both are attribute
# calls (`RailsConfig.from_path`, `rails.generate`) plus the one
# construction, and the call graph records `ast.Name` targets only — the
# node is named after the variable, and no body calls that name, so
# nothing links the guardrail to the agent.
USED_SOURCE = """\
from nemoguardrails import LLMRails, RailsConfig


class InvoiceAgent:
    def process(self, text):
        config = RailsConfig.from_path("config")
        rails = LLMRails(config)
        return rails.generate(messages=[{"role": "user", "content": text}])
"""

# X1b: an unassigned construction. The node is named after the class,
# and the agent's method calls that name — so B.4a's literal-name
# call-site rule does find evidence here.
BARE_SOURCE = """\
from nemoguardrails import LLMRails


class InvoiceAgent:
    def process(self, config):
        return LLMRails(config)
"""

# X1c: both producers in one file — a capability-derived guardrail
# (`pii_check`, called from the agent body) and a detector-derived one.
MIXED_SOURCE = """\
from nemoguardrails import LLMRails, RailsConfig
from core.rails.pii import pii_check


class InvoiceAgent:
    def process(self, text):
        config = RailsConfig.from_path("config")
        rails = LLMRails(config)
        pii_check(text)
        return rails.generate(messages=[])
"""


# ============================================================
# Fixture builders
# ============================================================

def _symbol(
    name: str,
    kind: str,
    file_path: str,
    source: str,
) -> TSASymbol:
    """A TSA symbol at the real line of `name` in `source`."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            if node.name == name:
                return TSASymbol(
                    name=name, kind=kind, file_path=file_path,
                    line_start=node.lineno, line_end=node.lineno + 4,
                )
    raise AssertionError(name)


def _caps(
    *entries: tuple[str, str, set[str]],
) -> dict[str, FileCapabilities]:
    """`(file, symbol, capabilities)` triples → the file capability map.

    Capabilities are the only positive signal about behaviour
    (ADR-0016, decision 7): `calls_llm` + `invokes_mcp_tool` makes a
    class an agent.
    """
    out: dict[str, FileCapabilities] = {}
    for file_path, name, capabilities in entries:
        fc = out.setdefault(
            file_path, FileCapabilities(file_path=file_path, symbols=[])
        )
        fc.symbols.append(SymbolCapabilities(
            symbol_name=name,
            file_path=file_path,
            capabilities=set(capabilities),
        ))
    return out


def _detector_nodes(source: str, file_path: str) -> list[Node]:
    """The nodes the real detector returns for `source`."""
    return NeMoGuardrailsDetector().detect(
        file_path, ast.parse(source), source
    ).nodes


def _detection(nodes: list[Node]) -> DetectionResult:
    return DetectionResult(framework="nemo_guardrails", nodes=list(nodes))


def _capability_guardrail(name: str, file_path: str) -> Node:
    """The `guardrail` node the capability producer would have left.

    Built by hand, the way `test_guardrail_edges` builds one: the stage
    under test reads `delta.nodes`, and what put the node there is not
    what these tests are about.
    """
    return Node(
        id=f"guardrail:{name}:{file_path}",
        kind="guardrail",
        name=name,
        host_id="localhost",
        attributes={"source_file": file_path},
    )


def _scan(
    project,
    *,
    source: str,
    file_path: str = AGENTS,
    symbols: list[TSASymbol] | None = None,
    capabilities: dict[str, FileCapabilities] | None = None,
    extra_nodes: list[Node] | None = None,
):
    """The pipeline up to B.4a: detector → merge → guardrail_edges.

    Returns the project and the detector's own node list, so a test can
    compare what the detector returned with what the graph kept.
    """
    nodes = _detector_nodes(source, file_path)
    p = project(
        files={file_path: source},
        symbols=symbols,
        capabilities=capabilities,
        detection=_detection(nodes),
    )
    p.ctx.entry_points = run_entry_point_detector(p.ctx.manifest)
    ClassNodeStage().apply(p.delta, p.ctx)
    DetectorMergeStage().apply(p.delta, p.ctx)
    p.delta.nodes.extend(extra_nodes or [])
    GuardrailEdgeStage().apply(p.delta, p.ctx)
    return p, nodes


def _agent_caps(file_path: str, name: str) -> dict[str, FileCapabilities]:
    return _caps((file_path, name, {"calls_llm", "invokes_mcp_tool"}))


# ============================================================
# M1 — the merge
# ============================================================

def test_a_detector_guardrail_replaces_the_tsa_node_of_the_same_name(
    project,
) -> None:
    """ADR-0011: same `(name, source_file)`, the detector is authoritative.

    The TSA index classifies a class named `rails`; the detector finds an
    `LLMRails(config)` construction in the same file, named after the
    variable it is bound to. The detector node replaces the class node,
    takes the class's capabilities (the detector found none of its own),
    and keeps its own `observed_by` — which is what makes the two
    producers tellable apart in the graph afterwards.
    """
    nodes = _detector_nodes(MERGE_SOURCE, AGENTS)
    assert [n.name for n in nodes] == ["rails"]
    assert nodes[0].attributes["line_start"] == 12

    p = project(
        files={AGENTS: MERGE_SOURCE},
        symbols=[_symbol("rails", "class", AGENTS, MERGE_SOURCE)],
        capabilities=_agent_caps(AGENTS, "rails"),
        detection=_detection(nodes),
    )
    ClassNodeStage().apply(p.delta, p.ctx)
    tsa_node = p.named("rails")
    assert tsa_node.kind == "agent"
    assert tsa_node.capabilities != []

    DetectorMergeStage().apply(p.delta, p.ctx)

    guardrails = p.nodes("guardrail")
    assert len(guardrails) == 1
    # The detector's id, not a rebuilt one: the node is the replacement.
    assert guardrails[0].id == nodes[0].id
    # The class node is gone, not duplicated.
    assert p.nodes("agent") == []
    # Capabilities survive the replacement (ADR-0011).
    assert guardrails[0].capabilities == tsa_node.capabilities
    assert guardrails[0].observed_by == [
        "ast", "framework:nemo_guardrails",
    ]


# ============================================================
# X1 — B.4a on a detector node
# ============================================================

def test_a_used_rails_gets_a_node_and_no_edge(project) -> None:
    """X1a: `rails = LLMRails(config)` then `rails.generate(...)`.

    The call graph records `ast.Name` targets only
    (`entry_points._collect_call_names`), so the agent's method records
    the construction's own name `LLMRails` — and not `rails`, the
    variable the node is named after (`rails.generate(...)` is an
    attribute call, and so is `RailsConfig.from_path(...)`). The node is
    in the graph; B.4a emits nothing for it. This is the shape the brief
    describes: an `LLMRails` wraps a generation call at runtime, and the
    AST has no static component to link.
    """
    p, nodes = _scan(
        project,
        source=USED_SOURCE,
        symbols=[_symbol("InvoiceAgent", "class", AGENTS, USED_SOURCE)],
        capabilities=_agent_caps(AGENTS, "InvoiceAgent"),
    )

    assert [n.name for n in nodes] == ["rails"]
    assert p.named("rails").kind == "guardrail"
    assert p.named("InvoiceAgent").kind == "agent"
    # Why there is no edge: the body's one recorded call is the class
    # name, not the variable the node is named after.
    assert p.ctx.entry_points.call_graph["process"] == {"LLMRails"}
    assert p.edges("guards") == []



def test_a_bare_construction_is_linked_by_the_call_graph(project) -> None:
    """X1b: `return LLMRails(config)` — the node is named after the class.

    B.4a's evidence is the literal name at a call site in the component's
    body, and an unassigned construction is named `LLMRails`, which is
    exactly the name the body calls. B.4-gap-3 does not change B.4a, so
    the edge is emitted. It is not a claim this step makes: B.4a decides
    what a `guards` edge needs, and this fixture pins what it decides.
    """
    p, nodes = _scan(
        project,
        source=BARE_SOURCE,
        symbols=[_symbol("InvoiceAgent", "class", AGENTS, BARE_SOURCE)],
        capabilities=_agent_caps(AGENTS, "InvoiceAgent"),
    )

    assert [n.name for n in nodes] == ["LLMRails"]
    guardrail = p.named("LLMRails")
    agent = p.named("InvoiceAgent")
    guards = p.edges("guards")
    assert [(e.source_id, e.target_id) for e in guards] == [
        (guardrail.id, agent.id),
    ]
    assert guards[0].attributes["resolved_via"] == "call_graph"
    assert guards[0].flow_kind == "structural"


def test_the_two_producers_coexist_and_are_distinguishable(project) -> None:
    """X1c: a capability guardrail and a detector one, one file.

    Two `guardrail` nodes, two producers, two `observed_by` values — the
    precedence is a merge on `(name, source_file)`, and these two symbols
    do not collide. B.4a links each from its own evidence: the capability
    node from the bare-name call (`pii_check`), the detector node from
    nothing.
    """
    capability_node = _capability_guardrail("pii_check", RAILS_FILE)
    p, nodes = _scan(
        project,
        source=MIXED_SOURCE,
        symbols=[_symbol("InvoiceAgent", "class", AGENTS, MIXED_SOURCE)],
        capabilities=_agent_caps(AGENTS, "InvoiceAgent"),
        extra_nodes=[capability_node],
    )

    assert [n.name for n in nodes] == ["rails"]
    guardrails = {n.name: n for n in p.nodes("guardrail")}
    assert sorted(guardrails) == ["pii_check", "rails"]
    assert guardrails["rails"].observed_by == [
        "ast", "framework:nemo_guardrails",
    ]
    assert guardrails["pii_check"].observed_by == ["ast"]

    agent = p.named("InvoiceAgent")
    guards = p.edges("guards")
    assert [(e.source_id, e.target_id) for e in guards] == [
        (capability_node.id, agent.id),
    ]
    assert guards[0].attributes["resolved_via"] == "call_graph"


def test_a_construction_with_no_component_yields_a_node_and_no_edge(
    project,
) -> None:
    """A file with no agent and no tool: the node is still in the graph.

    B.4a needs a component; the payload (B.4b) names `guardrail` nodes,
    so the node's existence does not depend on an edge.
    """
    source = """\
from nemoguardrails import LLMRails, RailsConfig

config = RailsConfig.from_path("config")
rails = LLMRails(config)
"""
    p, nodes = _scan(project, source=source, file_path=RAILS_FILE)

    assert [n.name for n in nodes] == ["rails"]
    assert p.named("rails").kind == "guardrail"
    assert p.nodes("agent") == []
    assert p.nodes("tool") == []
    assert p.edges("guards") == []

