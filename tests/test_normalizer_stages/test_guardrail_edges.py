"""GuardrailEdgeStage: `guards` (guardrail → component) from source (B.4a).

ADR-0019 §1: a `guards` edge is emitted only when the source-level
evidence establishes a protection relationship — a call site through the
call graph, or a `@guardrail(...)` decorator — and never from
co-location. Four emission cases, three non-emission cases, plus
determinism and the "no traversal effect" check.

The edge is `structural`: `flow_kind="structural"`, `observed_by=["ast"]`,
`data_flow_confirmed is None` (ADR-0015 decision 5, ADR-0016).

No deterministic classifier produces a `guardrail` node today —
`classification.py` answers `sink` / `agent` / `llm_endpoint` from
capabilities and nothing else — so each fixture puts one in the delta
the way `detector_merge` would: a `guardrail` node carrying a
`source_file`. The resolver reads `delta.nodes`, which is the point of
running it after the merge.

The call graph is not hand-fed. Every fixture builds it with the real
detector (`entry_points.run`), so what the resolver reads is what the
pipeline would have given it — including the absence of `self.`-calls.
"""

from __future__ import annotations

import ast

from aicorellator.graph.models import Edge, Node
from aicorellator.parsing.capability_extractor import (
    FileCapabilities,
    SymbolCapabilities,
)
from aicorellator.parsing.entry_points import run as run_entry_point_detector
from aicorellator.parsing.models import TSASymbol
from aicorellator.parsing.normalizer_helpers import edge_id
from aicorellator.parsing.normalizer_stages import (
    ClassNodeStage,
    GuardrailEdgeStage,
    MethodNodeStage,
)

AGENTS = "agents/invoice.py"
GUARDRAILS = "core/guardrails/pii.py"
TOOLS = "tools/invoice.py"

# E1: an agent whose method calls the guardrail by bare name.
AGENT_SOURCE = """\
from core.guardrails.pii import pii_check


class InvoiceAgent:
    def process(self, text):
        return pii_check(text)
"""

# E2: a component decorated with the guardrail decorator.
DECORATED_SOURCE = """\
from core.guardrails.decorators import guardrail


@guardrail
class InvoiceAgent:
    def process(self, text):
        return text
"""

# E2, dotted form: the `@mcp.tool`-shaped decorator.
DOTTED_DECORATOR_SOURCE = """\
import mcp


@mcp.guardrail(scope="input")
class InvoiceAgent:
    def process(self, text):
        return text
"""

# E3: a tool in another file, calling the guardrail it imports.
TOOL_SOURCE = """\
from core.guardrails.pii import pii_check


def process_invoice(text):
    return pii_check(text)
"""

# E4: three tools, one guardrail.
THREE_TOOLS_SOURCE = """\
from core.guardrails.pii import pii_check


def alpha(text):
    return pii_check(text)


def beta(text):
    return pii_check(text)


def gamma(text):
    return pii_check(text)
"""

# N1: a component and a guardrail in one file, no call between them.
CO_LOCATED_SOURCE = """\
def process_invoice(text):
    return text
"""

# N3: the `self.`-call that the call graph cannot see.
SELF_CALL_SOURCE = """\
class InvoiceAgent:
    def __init__(self, detector):
        self.pii = detector

    def process(self, text):
        return self.pii.check(text)
"""


# ============================================================
# Fixture builders
# ============================================================

def _symbol(
    name: str,
    kind: str,
    file_path: str,
    source: str,
    class_name: str | None = None,
) -> TSASymbol:
    """A TSA symbol at the real line of `name` in `source`."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            if node.name == name:
                return TSASymbol(
                    name=name, kind=kind, file_path=file_path,
                    line_start=node.lineno, line_end=node.lineno + 4,
                    class_name=class_name,
                )
    raise AssertionError(name)


def _caps(
    *entries: tuple[str, str, set[str]],
) -> dict[str, FileCapabilities]:
    """`(file, symbol, capabilities)` triples → the file capability map.

    Capabilities are the only positive signal about behaviour
    (ADR-0016, decision 7): `calls_llm` + `invokes_mcp_tool` makes a
    class an agent; `invokes_mcp_tool` makes a function a tool.
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


def _guardrail_node(name: str, file_path: str) -> Node:
    """The `guardrail` node `detector_merge` would have left in the delta.

    `source_file` is what the resolver reads to find the definition it
    has to inspect for a decorator.
    """
    return Node(
        id=f"guardrail:{name}:{file_path}",
        kind="guardrail",
        name=name,
        host_id="localhost",
        attributes={"source_file": file_path},
    )


def _agent_scenario(project):
    """An agent whose method calls `pii_check`, plus the guardrail node."""
    p = project(
        files={AGENTS: AGENT_SOURCE},
        symbols=[_symbol("InvoiceAgent", "class", AGENTS, AGENT_SOURCE)],
        capabilities=_caps(
            (AGENTS, "InvoiceAgent", {"calls_llm", "invokes_mcp_tool"}),
        ),
    )
    p.ctx.entry_points = run_entry_point_detector(p.ctx.manifest)
    ClassNodeStage().apply(p.delta, p.ctx)
    p.delta.nodes.append(_guardrail_node("pii_check", GUARDRAILS))
    return p


def _decorated_scenario(project, source: str):
    """An agent carrying a guardrail decorator, plus the guardrail node."""
    p = project(
        files={AGENTS: source},
        symbols=[_symbol("InvoiceAgent", "class", AGENTS, source)],
        capabilities=_caps(
            (AGENTS, "InvoiceAgent", {"calls_llm", "invokes_mcp_tool"}),
        ),
    )
    p.ctx.entry_points = run_entry_point_detector(p.ctx.manifest)
    ClassNodeStage().apply(p.delta, p.ctx)
    p.delta.nodes.append(_guardrail_node("guardrail", GUARDRAILS))
    return p


# ============================================================
# Emitted (E1–E4)
# ============================================================

def test_a_component_that_calls_a_guardrail_gets_an_edge(project) -> None:
    """E1: the agent's method calls `pii_check` by bare name."""
    p = _agent_scenario(project)

    GuardrailEdgeStage().apply(p.delta, p.ctx)

    guardrail = p.named("pii_check")
    agent = p.named("InvoiceAgent")
    guards = p.edges("guards")
    assert [(e.source_id, e.target_id) for e in guards] == [
        (guardrail.id, agent.id),
    ]
    # A declaration, not a path: structural, AST-observed, and no
    # data-flow claim (ADR-0015 decision 5, ADR-0016).
    assert guards[0].flow_kind == "structural"
    assert guards[0].observed_by == ["ast"]
    assert guards[0].data_flow_confirmed is None
    assert guards[0].attributes["resolved_via"] == "call_graph"


def test_a_component_decorated_with_the_guardrail_decorator_gets_an_edge(
    project,
) -> None:
    """E2: `@guardrail` on the component is the evidence."""
    p = _decorated_scenario(project, DECORATED_SOURCE)

    GuardrailEdgeStage().apply(p.delta, p.ctx)

    guardrail = p.named("guardrail")
    agent = p.named("InvoiceAgent")
    guards = p.edges("guards")
    assert [(e.source_id, e.target_id) for e in guards] == [
        (guardrail.id, agent.id),
    ]
    assert guards[0].flow_kind == "structural"
    assert guards[0].attributes["resolved_via"] == "decorator"


def test_a_dotted_guardrail_decorator_is_recognized(project) -> None:
    """E2, dotted form: `@mcp.guardrail(...)` — the last segment is what
    counts, mirroring `framework_detectors/langchain.py::_decorator_name`."""
    p = _decorated_scenario(project, DOTTED_DECORATOR_SOURCE)

    GuardrailEdgeStage().apply(p.delta, p.ctx)

    assert len(p.edges("guards")) == 1


def test_a_component_that_calls_a_guardrail_in_another_file_gets_an_edge(
    project,
) -> None:
    """E3: the tool is in `tools/invoice.py`, the guardrail in
    `core/guardrails/pii.py`, and the tool imports it."""
    p = project(
        files={TOOLS: TOOL_SOURCE, GUARDRAILS: "def pii_check(text): ...\n"},
        symbols=[_symbol("process_invoice", "method", TOOLS, TOOL_SOURCE)],
        capabilities=_caps((TOOLS, "process_invoice", {"invokes_mcp_tool"})),
    )
    p.ctx.entry_points = run_entry_point_detector(p.ctx.manifest)
    MethodNodeStage().apply(p.delta, p.ctx)
    p.delta.nodes.append(_guardrail_node("pii_check", GUARDRAILS))

    GuardrailEdgeStage().apply(p.delta, p.ctx)

    guardrail = p.named("pii_check")
    tool = p.named("process_invoice")
    guards = p.edges("guards")
    assert [(e.source_id, e.target_id) for e in guards] == [
        (guardrail.id, tool.id),
    ]
    # The import is what the scenario is about; the resolution itself is
    # by name through the call graph (ADR-0019 §1).
    assert p.ctx.entry_points.imports[TOOLS] == ["core.guardrails.pii"]


def test_one_guardrail_protects_every_component_that_calls_it(project) -> None:
    """E4: three tools call one guardrail → three edges, unique ids."""
    names = ("alpha", "beta", "gamma")
    p = project(
        files={TOOLS: THREE_TOOLS_SOURCE},
        symbols=[
            _symbol(name, "method", TOOLS, THREE_TOOLS_SOURCE)
            for name in names
        ],
        capabilities=_caps(
            *[(TOOLS, name, {"invokes_mcp_tool"}) for name in names]
        ),
    )
    p.ctx.entry_points = run_entry_point_detector(p.ctx.manifest)
    MethodNodeStage().apply(p.delta, p.ctx)
    p.delta.nodes.append(_guardrail_node("pii_check", GUARDRAILS))

    GuardrailEdgeStage().apply(p.delta, p.ctx)

    guardrail = p.named("pii_check")
    guards = p.edges("guards")
    assert len(guards) == 3
    assert len({e.id for e in guards}) == 3
    assert {e.source_id for e in guards} == {guardrail.id}
    assert {e.target_id for e in guards} == {p.named(n).id for n in names}


# ============================================================
# Not emitted (N1–N3)
# ============================================================

def test_a_colocated_component_and_guardrail_get_no_edge(project) -> None:
    """N1: the same file is a layout, not a defensive relationship."""
    p = project(
        files={TOOLS: CO_LOCATED_SOURCE},
        symbols=[
            _symbol("process_invoice", "method", TOOLS, CO_LOCATED_SOURCE)
        ],
        capabilities=_caps((TOOLS, "process_invoice", {"invokes_mcp_tool"})),
    )
    p.ctx.entry_points = run_entry_point_detector(p.ctx.manifest)
    MethodNodeStage().apply(p.delta, p.ctx)
    p.delta.nodes.append(_guardrail_node("pii_check", TOOLS))

    GuardrailEdgeStage().apply(p.delta, p.ctx)

    assert p.named("process_invoice").attributes["source_file"] == TOOLS
    assert p.named("pii_check").attributes["source_file"] == TOOLS
    assert p.edges("guards") == []


def test_a_guardrail_nothing_calls_gets_no_edge(project) -> None:
    """N2: wired in by DI, a framework hook or an import side effect —
    a false negative, and it is accepted (ADR-0019 §1)."""
    p = project(
        files={TOOLS: CO_LOCATED_SOURCE},
        symbols=[
            _symbol("process_invoice", "method", TOOLS, CO_LOCATED_SOURCE)
        ],
        capabilities=_caps((TOOLS, "process_invoice", {"invokes_mcp_tool"})),
    )
    p.ctx.entry_points = run_entry_point_detector(p.ctx.manifest)
    MethodNodeStage().apply(p.delta, p.ctx)
    p.delta.nodes.append(_guardrail_node("PIIDetector", GUARDRAILS))

    GuardrailEdgeStage().apply(p.delta, p.ctx)

    assert p.named("PIIDetector").kind == "guardrail"
    assert p.named("process_invoice").kind == "tool"
    assert p.edges("guards") == []


def test_a_self_prefixed_call_gets_no_edge(project) -> None:
    """N3: `self.pii.check(x)` is an attribute call, so the call graph
    never records it (ADR-0019 §1)."""
    p = project(
        files={AGENTS: SELF_CALL_SOURCE},
        symbols=[_symbol("InvoiceAgent", "class", AGENTS, SELF_CALL_SOURCE)],
        capabilities=_caps(
            (AGENTS, "InvoiceAgent", {"calls_llm", "invokes_mcp_tool"}),
        ),
    )
    p.ctx.entry_points = run_entry_point_detector(p.ctx.manifest)
    ClassNodeStage().apply(p.delta, p.ctx)
    p.delta.nodes.append(_guardrail_node("PIIDetector", GUARDRAILS))

    GuardrailEdgeStage().apply(p.delta, p.ctx)

    recorded = set().union(*p.ctx.entry_points.call_graph.values())
    assert "self.pii.check" in SELF_CALL_SOURCE   # the call is in the source
    assert "PIIDetector" not in recorded          # and not in the call graph
    assert p.edges("guards") == []


# ============================================================
# Determinism, dedupe, and no traversal effect
# ============================================================

def test_the_same_fixture_produces_the_same_edge_ids(project) -> None:
    def run() -> list[str]:
        p = _agent_scenario(project)
        GuardrailEdgeStage().apply(p.delta, p.ctx)
        return sorted(e.id for e in p.edges("guards"))

    assert run() == run()
    assert len(run()) == 1


def test_an_edge_the_delta_already_has_is_not_emitted_twice(project) -> None:
    """The edge id is content-derived, so an existing edge wins."""
    p = _agent_scenario(project)
    guardrail = p.named("pii_check")
    agent = p.named("InvoiceAgent")
    p.delta.edges.append(Edge(
        id=edge_id(guardrail.id, agent.id, "guards"),
        source_id=guardrail.id, target_id=agent.id,
        kind="guards", host_id="localhost",
    ))

    GuardrailEdgeStage().apply(p.delta, p.ctx)

    assert len(p.edges("guards")) == 1


def test_a_guards_edge_does_not_change_chain_enumeration(
    project, store,
) -> None:
    """A chain through the guarded component is enumerated exactly as
    before the stage ran: `guards` is `structural`, and `find_chains`
    traverses `control` and `data` only (ADR-0015, decision 5)."""
    p = _agent_scenario(project)
    agent = p.named("InvoiceAgent")

    entry = Node(
        id="entry_point:1", kind="entry_point", name="POST /invoice",
        host_id="localhost",
    )
    sink = Node(
        id="sink:1", kind="sink", name="delete_file", host_id="localhost",
    )
    p.delta.nodes.extend([entry, sink])
    p.delta.edges.extend([
        Edge(
            id="edge:entry", source_id=entry.id, target_id=agent.id,
            kind="delegates_to", host_id="localhost", flow_kind="control",
        ),
        Edge(
            id="edge:sink", source_id=agent.id, target_id=sink.id,
            kind="has_access_to", host_id="localhost", flow_kind="control",
        ),
    ])
    for node in p.delta.nodes:
        store.nodes.upsert(node)
    for edge in p.delta.edges:
        store.edges.upsert(edge)

    before = [(c.nodes, c.edges) for c in store.query.find_chains()]
    assert len(before) == 1

    GuardrailEdgeStage().apply(p.delta, p.ctx)
    guards = p.edges("guards")
    assert len(guards) == 1
    for edge in guards:
        store.edges.upsert(edge)

    after = [(c.nodes, c.edges) for c in store.query.find_chains()]
    assert after == before
