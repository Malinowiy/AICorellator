"""GuardrailCapabilityStage: the `validates_input` capability (B.4-gap-1).

ADR-0024 §2's rule, as this stage implements it: a function or method
**called from** a component — a caller whose capabilities are
`calls_llm`, `invokes_mcp_tool` or `reads_untrusted` — and carrying
**none of** `SINK_CAPABILITIES`, `AGENT_CAPABILITIES`,
`LLM_ENDPOINT_CAPABILITIES`, `TOOL_CAPABILITIES` is credited
`validates_input`; `classify_method` then yields `guardrail` and
`MethodNodeStage` builds the node.

Every fixture is built from the real producers, because both halves of
the rule come from them:

  - the capability map is the real `capability_extractor.extract` over
    the fixture's files — so an "agent" is an agent because a call shape
    in its body credits `calls_llm`, not because a test says so;
  - the call graph is the real `entry_points.run` — flat, name-keyed,
    `ast.Name` calls only (`entry_points._collect_call_names`).

The last point is what the `logger.info` case below pins, and it is a
correction to the wording rather than to the rule: `logger.info(text)` is
an *attribute* call, so the call graph does not hold it, and a literal
reading of ADR-0024 §2's example cannot be credited. What the ADR means
by the accepted false positive is the bare-name form — a capability-free
project helper called from an agent body — and that is pinned here.
"""

from __future__ import annotations

import ast

from aicorellator.parsing.capability_extractor import extract
from aicorellator.parsing.classification import classify_method
from aicorellator.parsing.entry_points import run as run_entry_point_detector
from aicorellator.parsing.models import TSASymbol
from aicorellator.parsing.normalizer_helpers import caps_for_symbol
from aicorellator.parsing.normalizer_stages import (
    ClassNodeStage,
    GuardrailCapabilityStage,
    GuardrailEdgeStage,
    MethodNodeStage,
)
from aicorellator.parsing.normalizer_stages.guardrail_capability import (
    CREDITED_CAPABILITY,
    credit_validates_input,
)

AGENTS = "agents/chat.py"
UTILS = "core/validation.py"

# P1: an agent whose method calls a capability-free helper by bare name.
# `self.llm.chat(text)` is what credits `calls_llm`
# (patterns.CALLS_LLM_RULE — the `llm` handle prefix and the `chat`
# verb), so the class is an agent and the method's own capabilities make
# it a component caller too.
AGENT_SOURCE = """\
class ChatAgent:
    def run(self, text):
        answer = self.llm.chat(text)
        return validate_input(answer)


def validate_input(text):
    return text.strip()
"""

# P2/N4: the same call shape, but the callee is a method of another class.
METHOD_SOURCE = """\
class ChatAgent:
    def run(self, text):
        answer = self.llm.chat(text)
        return check(answer)


class TextGuard:
    def check(self, text):
        return text.strip()
"""

# P3: the accepted false positive, in its reachable form — `log_line` is a
# plain helper that does nothing but print, and it is credited because the
# agent calls it by bare name and its own body carries no capability.
FALSE_POSITIVE_SOURCE = """\
class ChatAgent:
    def run(self, text):
        answer = self.llm.chat(text)
        log_line(answer)
        return answer


def log_line(text):
    print(text)
"""

# P3, the form that cannot be credited: `logger.info(text)` is an
# attribute call, and `_collect_call_names` records `ast.Name` targets
# only. `info` is a real symbol with no capability, called — in a
# different spelling than the call graph keeps.
ATTRIBUTE_CALL_SOURCE = """\
class ChatAgent:
    def run(self, text):
        answer = self.llm.chat(text)
        logger.info(answer)
        return answer


def info(text):
    return text
"""

# P5: the two other component callers. `session.call_tool(name)` credits
# `invokes_mcp_tool` on the caller and `client.list_tools()` credits
# `reads_untrusted` (an entry-point-shaped reader), so both functions are
# component callers and both calls to `validate_input` are evidence.
OTHER_CALLERS_SOURCE = """\
def call_via_mcp(session, name, text):
    session.call_tool(name)
    return validate_input(text)


def read_catalogue(client, text):
    client.list_tools()
    return validate_input(text)


def validate_input(text):
    return text.strip()
"""

# N4: an agent that calls a class by bare name. A class is not a function
# and not a method, so ADR-0024 §2's rule does not reach it, and
# `classify_class` has no guardrail branch to read the capability if it
# were credited.
CLASS_CALL_SOURCE = """\
class ChatAgent:
    def run(self, text):
        answer = self.llm.chat(text)
        policy = InputPolicy()
        return policy.apply(answer)


class InputPolicy:
    def apply(self, text):
        return text
"""

# N1: a sink called from an agent body — `open(path, "w")` credits
# `writes_filesystem`, so the symbol is a component of its own.
SINK_SOURCE = """\
class ChatAgent:
    def run(self, path, text):
        answer = self.llm.chat(text)
        save_file(path, answer)
        return answer


def save_file(path, data):
    with open(path, "w") as handle:
        handle.write(data)
"""

# N2/N3: a capability-free helper called from a plain function (no
# component caller anywhere), a helper nothing calls, and the plain
# function itself.
PLAIN_CALLER_SOURCE = """\
def process(text):
    return validate_input(text)


def validate_input(text):
    return text.strip()


def never_called(text):
    return text
"""

# The flat call graph's collision: the same function name in two files,
# one of them called by an agent.
COLLIDING_SOURCE = """\
def validate_input(text):
    return text.strip()
"""

COLLIDING_AGENT_SOURCE = """\
class ChatAgent:
    def run(self, text):
        answer = self.llm.chat(text)
        return validate_input(answer)


def validate_input(text):
    return text.strip()
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


def _symbols(file_path: str, source: str) -> list[TSASymbol]:
    """Every class and function in `source`, as TSA reports them.

    TSA's `--structure` output has two kinds: a class, and a method (a
    module-level function is a method with `class_name=None`).
    """
    out: list[TSASymbol] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef):
            out.append(_symbol(node.name, "class", file_path, source))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(_symbol(
                        item.name, "method", file_path, source,
                        class_name=node.name,
                    ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(_symbol(node.name, "method", file_path, source))
    return out


def _context(project, files: dict[str, str], symbols: list[TSASymbol]):
    """A project carrying the real call graph and the real capabilities."""
    p = project(files=files, symbols=symbols)
    p.ctx.entry_points = run_entry_point_detector(p.ctx.manifest)
    p.ctx.capabilities = {
        entry.path: extract(entry.abs_path, entry.path)
        for entry in p.ctx.manifest.files
    }
    return p


def _run(project, files: dict[str, str], symbols: list[TSASymbol]):
    """The producer (stage 2) plus the two node stages that read it."""
    p = _context(project, files, symbols)
    GuardrailCapabilityStage().apply(p.delta, p.ctx)
    ClassNodeStage().apply(p.delta, p.ctx)
    MethodNodeStage().apply(p.delta, p.ctx)
    return p


def _kinds(p) -> list[tuple[str, str]]:
    return [(n.kind, n.name) for n in p.delta.nodes]


def _has_capability(p, file_path: str, name: str) -> bool:
    return CREDITED_CAPABILITY in caps_for_symbol(
        p.ctx.capabilities, file_path, name
    )


def _snapshot(p) -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    """The capability map as a comparable value, order included."""
    return {
        path: [
            (sym.symbol_name, tuple(sorted(sym.capabilities)))
            for sym in file_caps.symbols
        ]
        for path, file_caps in p.ctx.capabilities.items()
    }


# ============================================================
# Credited (P1–P3, P5)
# ============================================================

def test_a_helper_called_from_an_agent_becomes_a_guardrail(project) -> None:
    """P1: the agent's method calls `validate_input` by bare name.

    The capability is the only signal the callee has: its body is a
    `str.strip()` call, so no other branch of `classify_method` would
    have made it a node.
    """
    p = _run(
        project,
        {AGENTS: AGENT_SOURCE},
        _symbols(AGENTS, AGENT_SOURCE),
    )

    assert _has_capability(p, AGENTS, "validate_input")
    assert _kinds(p) == [
        ("agent", "ChatAgent"),
        ("guardrail", "validate_input"),
    ]
    node = p.named("validate_input")
    assert node.observed_by == ["ast"]
    assert node.capabilities == ["validates_input"]
    assert node.attributes["source_file"] == AGENTS


def test_the_classification_yields_guardrail_from_the_capability() -> None:
    """The node's kind is `classify_method`'s, not the stage's.

    The stage writes a capability; the classification is the only place
    a kind is decided (ADR-0024 §2).
    """
    c = classify_method(
        "validate_input", AGENTS, None, ["validates_input"],
    )

    assert c is not None
    assert c.kind == "guardrail"


def test_a_method_called_from_an_agent_becomes_a_guardrail(project) -> None:
    """P2: the callee is a method of another class, called by bare name.

    The call graph keys a method by its own name
    (`entry_points.call_graph[node.name]`), so a method of another class
    is reachable exactly when the call is written without a receiver.
    """
    p = _run(
        project,
        {AGENTS: METHOD_SOURCE},
        _symbols(AGENTS, METHOD_SOURCE),
    )

    assert _has_capability(p, AGENTS, "check")
    assert _kinds(p) == [
        ("agent", "ChatAgent"),
        ("guardrail", "check"),
    ]
    assert p.named("check").observed_by == ["ast"]


def test_a_capability_free_helper_called_from_an_agent_is_credited(
    project,
) -> None:
    """P3: the accepted false positive, in its reachable form.

    `log_line` prints and does nothing else. It is credited because the
    agent calls it and it carries no capability of its own — ADR-0024 §2
    accepts that, ADR-0009's "false positives accepted" is the frame,
    and the payload's `guardrails` section (B.4b) is what weighs it.
    """
    p = _run(
        project,
        {AGENTS: FALSE_POSITIVE_SOURCE},
        _symbols(AGENTS, FALSE_POSITIVE_SOURCE),
    )

    assert _has_capability(p, AGENTS, "log_line")
    assert ("guardrail", "log_line") in _kinds(p)


def test_an_attribute_call_is_not_evidence(project) -> None:
    """P3, the form the call graph cannot see.

    `logger.info(text)` is an `ast.Attribute` call and
    `entry_points._collect_call_names` records `ast.Name` targets only,
    so the ADR's literal example is a false negative here. The same
    limitation is why B.4a does not see `self.pii.check(x)`
    (`normalizer_stages.guardrail_edges`).
    """
    p = _run(
        project,
        {AGENTS: ATTRIBUTE_CALL_SOURCE},
        _symbols(AGENTS, ATTRIBUTE_CALL_SOURCE),
    )

    assert p.ctx.entry_points.call_graph["run"] == set()
    assert not _has_capability(p, AGENTS, "info")
    assert ("guardrail", "info") not in _kinds(p)


def test_a_tool_caller_and_an_entry_caller_are_component_callers(
    project,
) -> None:
    """P5: `invokes_mcp_tool` and `reads_untrusted` are callers too.

    ADR-0024 §2 names three caller kinds — agent, tool, entry_point — and
    a tool or an entry point is a capability before it is a node. The
    union is the reading this stage implements; a reading that accepted
    `calls_llm` alone would miss both of these.
    """
    p = _run(
        project,
        {AGENTS: OTHER_CALLERS_SOURCE},
        _symbols(AGENTS, OTHER_CALLERS_SOURCE),
    )

    assert _has_capability(p, AGENTS, "validate_input")
    assert ("guardrail", "validate_input") in _kinds(p)
    # The callers are components themselves, so the negative half holds
    # for them: neither is credited, and neither is a guardrail.
    assert not _has_capability(p, AGENTS, "call_via_mcp")
    assert not _has_capability(p, AGENTS, "read_catalogue")


# ============================================================
# Not credited (N1–N4)
# ============================================================

def test_a_sink_called_from_an_agent_is_not_a_guardrail(project) -> None:
    """N1: `save_file` writes, so it is a component, not a guardrail.

    The case the negative half exists for: the agent calls it, so it is
    called from a component, and it is still not a guardrail — its own
    body says what it is.
    """
    p = _run(
        project,
        {AGENTS: SINK_SOURCE},
        _symbols(AGENTS, SINK_SOURCE),
    )

    assert not _has_capability(p, AGENTS, "save_file")
    assert _kinds(p) == [
        ("agent", "ChatAgent"),
        ("sink", "save_file"),
    ]
    assert p.named("save_file").attributes["category"] == "filesystem"


def test_a_sink_that_validates_input_is_still_a_sink() -> None:
    """O1: the classification's order, with both capabilities present.

    The producer never writes the two together (`_is_a_component`), so
    this is the classification's own precedence, hand-fed: a sink is the
    stronger evidence, and a guardrail is what is left.
    """
    both = classify_method(
        "save_file", AGENTS, None,
        ["validates_input", "writes_filesystem"],
    )

    assert both is not None
    assert both.kind == "sink"
    assert both.attributes["category"] == "filesystem"


def test_a_helper_called_from_a_plain_function_is_not_credited(
    project,
) -> None:
    """N2/N3: no component caller anywhere, so nothing is credited.

    `process` has no capability of its own — its body is one call to
    `validate_input` — so that call is not evidence about anything, and
    `never_called` has no caller at all. The delta is empty: none of the
    three symbols is a component, and none is a guardrail.
    """
    p = _run(
        project,
        {AGENTS: PLAIN_CALLER_SOURCE},
        _symbols(AGENTS, PLAIN_CALLER_SOURCE),
    )

    assert not _has_capability(p, AGENTS, "validate_input")
    assert not _has_capability(p, AGENTS, "process")
    assert _kinds(p) == []


def test_a_class_called_from_an_agent_is_not_credited(project) -> None:
    """N4: the rule reaches a function or a method, never a class.

    `InputPolicy()` is a bare-name call, so the call graph holds it and
    the agent is a component caller — and the symbol is a class, which
    ADR-0024 §2 does not credit. `classify_class` has no guardrail branch
    either, so the class's kind is whatever its own body yields: nothing,
    here.
    """
    p = _run(
        project,
        {AGENTS: CLASS_CALL_SOURCE},
        _symbols(AGENTS, CLASS_CALL_SOURCE),
    )

    assert p.ctx.entry_points.call_graph["run"] == {"InputPolicy"}
    assert not _has_capability(p, AGENTS, "InputPolicy")
    assert _kinds(p) == [("agent", "ChatAgent")]


# ============================================================
# The stage's own behaviour
# ============================================================

def test_the_stage_leaves_the_map_alone_when_nothing_qualifies(
    project,
) -> None:
    """The no-op, asserted on the map rather than on the delta.

    A scan with no component caller returns an empty list and writes
    nothing — the map stays the extractor's, entry for entry.
    """
    p = _context(
        project,
        {AGENTS: PLAIN_CALLER_SOURCE},
        _symbols(AGENTS, PLAIN_CALLER_SOURCE),
    )
    before = _snapshot(p)

    assert credit_validates_input(p.ctx) == []
    assert _snapshot(p) == before


def test_the_credited_pairs_are_sorted_and_stable(project) -> None:
    """Determinism: the same scan credits the same symbols, in order.

    The order is `(file, name)` over `tsa_output.symbols`, so it does not
    depend on the capability map's iteration order.
    """
    seen = set()
    for _ in range(2):
        p = _context(
            project,
            {AGENTS: AGENT_SOURCE, UTILS: COLLIDING_SOURCE},
            _symbols(AGENTS, AGENT_SOURCE)
            + _symbols(UTILS, COLLIDING_SOURCE),
        )
        seen.add(tuple(credit_validates_input(p.ctx)))

    assert seen == {((AGENTS, "validate_input"), (UTILS, "validate_input"))}


def test_the_same_name_in_another_file_is_credited_too(project) -> None:
    """The flat call graph's collision, pinned as it is.

    `ctx.entry_points.call_graph` has no file scope, so a caller is
    resolved by name across the scan and every symbol of that name is
    credited — including the one in a file the agent never touches. The
    capability map is per file, so the two become two `guardrail` nodes.
    """
    p = _run(
        project,
        {AGENTS: COLLIDING_AGENT_SOURCE, UTILS: COLLIDING_SOURCE},
        _symbols(AGENTS, COLLIDING_AGENT_SOURCE)
        + _symbols(UTILS, COLLIDING_SOURCE),
    )

    assert _has_capability(p, AGENTS, "validate_input")
    assert _has_capability(p, UTILS, "validate_input")
    assert _kinds(p) == [
        ("agent", "ChatAgent"),
        ("guardrail", "validate_input"),
        ("guardrail", "validate_input"),
    ]
    assert len({n.id for n in p.delta.nodes}) == 3


# ============================================================
# B.4a's emission, on the node this stage produces
# ============================================================

def test_b4a_links_the_capability_guardrail_to_the_agent(project) -> None:
    """B.4a emits `guards` from the node this stage produces.

    The producer and the emitter read the same call graph, so the edge is
    there without any change to B.4a: `guardrail → component`, from the
    call-site evidence.
    """
    p = _run(
        project,
        {AGENTS: AGENT_SOURCE},
        _symbols(AGENTS, AGENT_SOURCE),
    )

    GuardrailEdgeStage().apply(p.delta, p.ctx)

    guardrail = p.named("validate_input")
    agent = p.named("ChatAgent")
    guards = p.edges("guards")
    assert [(e.source_id, e.target_id) for e in guards] == [
        (guardrail.id, agent.id),
    ]
    assert guards[0].flow_kind == "structural"
    assert guards[0].attributes["resolved_via"] == "call_graph"


