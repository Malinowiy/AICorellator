"""The LangChain callbacks detector through the normalizer (B.4-gap-4).

The detector's own tests are
`tests/test_framework_langchain_callback.py`. Two things need a
`NormalizeContext` and the file-backed `project` fixture, and both are
about what happens to a detector node *after* the detector:

  - **M1 — the merge.** `DetectorMergeStage` is keyed by
    `(name, source_file)` and makes the detector node authoritative for
    kind (ADR-0011, ADR-0024 §5). A callback class is the strongest case
    for that rule: the capability layer reads the class's *body* (this
    one writes an audit log, so its capabilities say `sink`), the
    detector reads the SDK's *base* (so the kind is `guardrail`), and
    the two are the same symbol. The TSA node is replaced, its
    capabilities move to the detector node, and the detector node's
    `observed_by` survives.

  - **X1 — B.4a.** `GuardrailEdgeStage` reads `delta.nodes` after the
    merge, so a detector `guardrail` node is a `guardrail` node to it.
    For this shape B.4a's answer depends on the component, which is what
    the fixtures pin: a component that *instantiates* the handler calls
    its name and B.4a links the pair; a component that receives an
    already-built handler calls nothing, and B.4a emits nothing. B.4a
    is unchanged by this step; the fixtures say what it does.

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
from aicorellator.parsing.framework_detectors.langchain_callback import (
    LangChainCallbackDetector,
)
from aicorellator.parsing.models import TSASymbol
from aicorellator.parsing.normalizer_stages import (
    ClassNodeStage,
    DetectorMergeStage,
    GuardrailEdgeStage,
)

AGENTS = "agents/bot.py"
HOOKS_FILE = "core/audit/hooks.py"

# M1: a class the capability layer calls a sink (its hook writes an audit
# log) and the detector calls a guardrail (its base is the SDK's). One
# symbol, two producers, and the merge decides between them.
MERGE_SOURCE = """\
from langchain_core.callbacks import BaseCallbackHandler


class AuditHook(BaseCallbackHandler):
    \"\"\"Logs every run to disk.\"\"\"

    def on_llm_start(self, serialized, prompts, **kwargs):
        write_audit_log(prompts)
"""

# X1a: the agent *builds* the handler in its own body. `AuditHook()` is
# an `ast.Name` call, so the call graph records the name, and the name is
# the guardrail node's own name — B.4a's call-site rule finds evidence.
BUILT_SOURCE = """\
from langchain_core.callbacks import BaseCallbackHandler


class AuditHook(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        write_audit_log(prompts)


class InvoiceAgent:
    def process(self, text):
        handler = AuditHook()
        return llm(text, callbacks=[handler])
"""

# X1b: the agent *receives* the handler — built at module level, passed
# in a list. No component body names `AuditHook`, so B.4a's call-site
# rule finds nothing: a node and no edge. The module-level binding is the
# common shape (a handler registered once at import time).
RECEIVED_SOURCE = """\
from langchain_core.callbacks import BaseCallbackHandler


class AuditHook(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        write_audit_log(prompts)


AUDIT_HOOK = AuditHook()


class InvoiceAgent:
    def process(self, text):
        return llm(text, callbacks=[AUDIT_HOOK])
"""

# X1c: both producers in one file — a capability-derived guardrail
# (`pii_check`, called from the agent body) and a detector-derived one.
MIXED_SOURCE = """\
from langchain_core.callbacks import BaseCallbackHandler
from core.rails.pii import pii_check


class AuditHook(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        write_audit_log(prompts)


class InvoiceAgent:
    def process(self, text):
        pii_check(text)
        return llm(text)
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
    class an agent, and a write makes it a sink.
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
    return LangChainCallbackDetector().detect(
        file_path, ast.parse(source), source
    ).nodes


def _detection(nodes: list[Node]) -> DetectionResult:
    return DetectionResult(framework="langchain_callback", nodes=list(nodes))


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

    `AuditHook` is one symbol with two readings: the capability layer sees
    a body that writes an audit log and classifies the class a `sink`; the
    detector sees `BaseCallbackHandler` in its bases and emits a
    `guardrail`. The detector node replaces the class node, takes the
    class's capabilities (the detector found none of its own), and keeps
    its own `observed_by` — which is what makes the two producers
    tellable apart in the graph afterwards.
    """
    nodes = _detector_nodes(MERGE_SOURCE, AGENTS)
    assert [n.name for n in nodes] == ["AuditHook"]
    assert nodes[0].attributes["line_start"] == 4

    p = project(
        files={AGENTS: MERGE_SOURCE},
        symbols=[_symbol("AuditHook", "class", AGENTS, MERGE_SOURCE)],
        capabilities=_caps((AGENTS, "AuditHook", {"writes_filesystem"})),
        detection=_detection(nodes),
    )
    ClassNodeStage().apply(p.delta, p.ctx)
    tsa_node = p.named("AuditHook")
    assert tsa_node.kind == "sink"
    assert tsa_node.capabilities != []

    DetectorMergeStage().apply(p.delta, p.ctx)

    guardrails = p.nodes("guardrail")
    assert len(guardrails) == 1
    # The detector's id, not a rebuilt one: the node is the replacement.
    assert guardrails[0].id == nodes[0].id
    # The class node is gone, not duplicated, and its kind with it.
    assert p.nodes("sink") == []
    # Capabilities survive the replacement (ADR-0011).
    assert guardrails[0].capabilities == tsa_node.capabilities
    assert guardrails[0].observed_by == [
        "ast", "framework:langchain_callback",
    ]


# ============================================================
# X1 — B.4a on a detector node
# ============================================================

def test_a_callback_built_in_a_component_body_is_linked(project) -> None:
    """X1a: `handler = AuditHook()` inside the agent.

    B.4a's call-site rule reads its name-keyed call graph, which records
    `ast.Name` call targets, and `AuditHook()` is one — with the same
    name the guardrail node carries. So the edge is emitted, and it is
    not a claim this step makes: B.4a decides what a `guards` edge needs,
    and this fixture pins what it decides for a callback a component
    constructs.
    """
    p, nodes = _scan(
        project,
        source=BUILT_SOURCE,
        symbols=[
            _symbol("AuditHook", "class", AGENTS, BUILT_SOURCE),
            _symbol("InvoiceAgent", "class", AGENTS, BUILT_SOURCE),
        ],
        capabilities=_agent_caps(AGENTS, "InvoiceAgent"),
    )

    assert [n.name for n in nodes] == ["AuditHook"]
    guardrail = p.named("AuditHook")
    agent = p.named("InvoiceAgent")
    assert guardrail.kind == "guardrail"
    assert agent.kind == "agent"
    guards = p.edges("guards")
    assert [(e.source_id, e.target_id) for e in guards] == [
        (guardrail.id, agent.id),
    ]
    assert guards[0].attributes["resolved_via"] == "call_graph"
    assert guards[0].flow_kind == "structural"


def test_a_callback_the_component_receives_is_not_linked(project) -> None:
    """X1b: the handler is built at module level and passed in a list.

    No component body names `AuditHook`, so B.4a's call-site rule finds
    nothing — the false negative its own docstring states (a guardrail
    wired in by dependency injection). The node is in the graph either
    way; this fixture pins the "or none" half of B.4a's behaviour.
    """
    p, nodes = _scan(
        project,
        source=RECEIVED_SOURCE,
        symbols=[
            _symbol("AuditHook", "class", AGENTS, RECEIVED_SOURCE),
            _symbol("InvoiceAgent", "class", AGENTS, RECEIVED_SOURCE),
        ],
        capabilities=_agent_caps(AGENTS, "InvoiceAgent"),
    )

    assert [n.name for n in nodes] == ["AuditHook"]
    assert p.named("AuditHook").kind == "guardrail"
    assert p.named("InvoiceAgent").kind == "agent"
    # Why there is no edge: the agent's body calls `llm`, not the class.
    assert p.ctx.entry_points.call_graph["process"] == {"llm"}
    assert p.edges("guards") == []


def test_the_two_producers_coexist_and_are_distinguishable(project) -> None:
    """X1c: a capability guardrail and a detector one, one file.

    Two `guardrail` nodes, two producers, two `observed_by` values — the
    precedence is a merge on `(name, source_file)`, and these two symbols
    do not collide. B.4a links the capability node from the bare-name
    call (`pii_check`) and the detector node from nothing.
    """
    capability_node = _capability_guardrail("pii_check", HOOKS_FILE)
    p, nodes = _scan(
        project,
        source=MIXED_SOURCE,
        symbols=[
            _symbol("AuditHook", "class", AGENTS, MIXED_SOURCE),
            _symbol("InvoiceAgent", "class", AGENTS, MIXED_SOURCE),
        ],
        capabilities=_agent_caps(AGENTS, "InvoiceAgent"),
        extra_nodes=[capability_node],
    )

    assert [n.name for n in nodes] == ["AuditHook"]
    guardrails = {n.name: n for n in p.nodes("guardrail")}
    assert sorted(guardrails) == ["AuditHook", "pii_check"]
    assert guardrails["AuditHook"].observed_by == [
        "ast", "framework:langchain_callback",
    ]
    assert guardrails["pii_check"].observed_by == ["ast"]

    agent = p.named("InvoiceAgent")
    guards = p.edges("guards")
    assert [(e.source_id, e.target_id) for e in guards] == [
        (capability_node.id, agent.id),
    ]
    assert guards[0].attributes["resolved_via"] == "call_graph"


def test_a_callback_with_no_component_yields_a_node_and_no_edge(
    project,
) -> None:
    """A file with no agent and no tool: the node is still in the graph.

    B.4a needs a component; the payload (B.4b) names `guardrail` nodes,
    so the node's existence does not depend on an edge.
    """
    p, nodes = _scan(project, source=MERGE_SOURCE, file_path=HOOKS_FILE)

    assert [n.name for n in nodes] == ["AuditHook"]
    assert p.named("AuditHook").kind == "guardrail"
    assert p.nodes("agent") == []
    assert p.nodes("tool") == []
    assert p.nodes("sink") == []
    assert p.edges("guards") == []
