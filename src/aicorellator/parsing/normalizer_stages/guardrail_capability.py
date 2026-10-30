"""GuardrailCapabilityStage — the `validates_input` capability (B.4-gap-1).

Run order: 2 of 21 (see `normalizer_stages.STAGES`) — before
`class_nodes` and `method_nodes`, which read the map this stage writes.

ADR-0024 §2 decides the first of three `guardrail` producers, and it is a
**negative capability**: `validates_input` is credited on a function or
method that

  - is **called from** the body of an agent, a tool or an entry point —
    a caller whose own capabilities include `calls_llm`,
    `invokes_mcp_tool` or `reads_untrusted` (`CALLER_CAPABILITIES`), and
  - carries **none of** `SINK_CAPABILITIES`, `AGENT_CAPABILITIES`,
    `LLM_ENDPOINT_CAPABILITIES`, `TOOL_CAPABILITIES`
    (`CLASSIFYING_CAPABILITIES`).

`classification.classify_method` yields `guardrail` from the capability
and `MethodNodeStage` builds the node, whose `observed_by` stays the
`Node` default `["ast"]` — the provenance ADR-0024 §1 assigns to a
capability-derived guardrail. This stage emits no node and no edge: it
writes one capability into `ctx.capabilities`, the per-symbol map
`capability_extractor` fills, and the node stages read it afterwards.
B.4a's `guardrail_edges` (stage 21) then links the node to the component
that calls it, by the same call graph this stage read.

**The caller's kind is read from capabilities, not from nodes.** Stage
2a runs before `class_nodes` (3), `method_nodes` (4) and `entry_points`
(5) have produced anything, so there is no `agent` / `tool` /
`entry_point` node to ask. The caller is a component because its
capabilities say so: `calls_llm` is an agent, `invokes_mcp_tool` is a
tool, `reads_untrusted` is a handler that reads untrusted input. That
union is this stage's reading of ADR-0024 §2's "called from the body of
a node with kind agent, tool, or entry_point" — the deterministic
capability is the node's kind, one stage early. A caller that is a tool
by *layout* only (a public method of an MCP server with no capability of
its own) is therefore not a caller here: the layout rule is
`classify_method`'s, and this stage does not read paths. That is a known
false negative, accepted with the others below.

**The false positives are the ADR's.** A capability-free helper called
from an agent body is a `validates_input` candidate; ADR-0024 §2 names
`logger.info(text)` and `print(text)` as the examples. The capability is
weak by design (ADR-0024 §6), the payload's `guardrails` section is a
signal the model weighs rather than a filter (ADR-0019 §2), and the
alternative — a table of names like `validate_input` — is ADR-0016
decision 7's removal and stays removed. One of the ADR's two examples
cannot be reached as written: `logger.info(text)` is an `ast.Attribute`
call, and the call graph holds `ast.Name` targets only, so that call is
invisible here — the same limitation that makes B.4a miss
`self.pii.check(x)`. The bare-name helper is what this stage credits.

**The call graph is flat, and its collisions are pre-existing.**
`ctx.entry_points.call_graph` is keyed by function name with no file
scope (`entry_points._collect_call_names` records `ast.Name` calls
only), so a call name is matched project-wide and a caller's
capabilities are looked up by name across every file. Two functions of
the same name in two files therefore collide: an agent calling
`validate_input` credits *every* symbol of that name the scan saw, and a
caller named `run` in a non-agent file counts as an agent caller if some
other file's `run` calls an LLM. This is the shape `guardrail_edges`
(B.4a) and `entry_to_sink` already read; narrowing the call graph to a
file or a scope is a separate item, and it is not this step's.

**A class is never a guardrail.** The capability is credited on a
function or a method, so a class symbol is not credited — and
`classify_class` has no guardrail branch, so a class would not become a
`guardrail` node if it were. The method is the node; the class keeps
whatever its own body yields.

**Determinism.** The symbols are walked in `tsa_output.symbols` order,
the two tests are set membership, and the credited pairs are returned
sorted, so the same scan credits the same symbols in the same order.

**The LLM classifier runs before this stage, not after.**
`run_scan.py` runs it before `normalize()`, on the map as the extractor
left it, so the classifier is never asked about a symbol this stage
credited. Precedence between the producers is ADR-0024 §1's and is
resolved by `DetectorMergeStage` (11), which replaces a capability-derived
node with a detector node at the same `(name, source_file)`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ...patterns import (
    AGENT_CAPABILITIES,
    CLASSIFYING_CAPABILITIES,
)
from ..capability_extractor import FileCapabilities, SymbolCapabilities
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import caps_for_symbol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta
    from ..models import TSASymbol


log = logging.getLogger(__name__)


# The one capability this stage credits (ADR-0024 §2). The classification
# reads `patterns.GUARDRAIL_CAPABILITIES` — the set, not this name — and
# this constant must stay a member of it.
CREDITED_CAPABILITY = "validates_input"

# The capabilities that make a caller a component whose call on a
# capability-free symbol is evidence of a guardrail (ADR-0024 §2): an
# agent (`calls_llm`), a tool (`invokes_mcp_tool`), and an entry point —
# a handler that reads untrusted input (`reads_untrusted`), which is the
# capability the extractor credits on the read the request arrives in.
CALLER_CAPABILITIES: frozenset[str] = AGENT_CAPABILITIES | frozenset({
    "reads_untrusted",
})


class GuardrailCapabilityStage:
    """`validates_input` on the symbols ADR-0024 §2's rule names."""

    name = "guardrail_capability"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        credited = credit_validates_input(ctx)
        if credited:
            log.debug(
                "guardrail_capability: %s credited on %d symbol(s)",
                CREDITED_CAPABILITY,
                len(credited),
            )
        return delta


def credit_validates_input(
    ctx: NormalizeContext,
) -> list[tuple[str, str]]:
    """Credit `validates_input`; return the `(file_path, name)` pairs.

    The one entry point of the rule, so a caller that wants to know what
    the stage did does not have to diff the capability map. The pairs are
    sorted, which is what makes the answer stable across runs.

    Returns `[]` — and touches nothing — when no caller in the call graph
    is a component: a scan with no agent, tool or untrusted reader has no
    symbol this rule can be about, and the map is left exactly as the
    extractor wrote it.
    """
    callers = _component_callers(ctx)
    if not callers:
        return []

    callers_by_callee = _callers_by_callee(ctx.entry_points.call_graph)
    credited: list[tuple[str, str]] = []
    for symbol in ctx.tsa_output.symbols:
        if symbol.kind == "class":
            # A guardrail is a function or a method (ADR-0024 §2). A class
            # is not credited, and `classify_class` has no guardrail
            # branch to read the capability if it were: the method is the
            # node.
            continue
        if _is_a_component(ctx, symbol):
            continue
        if not callers_by_callee.get(symbol.name, frozenset()) & callers:
            continue
        _credit(ctx, symbol)
        credited.append((symbol.file_path, symbol.name))
    # A file may define the same name twice (two `run` methods in two
    # classes); the capability is one entry per `(file, name)` — the same
    # key `caps_for_symbol` reads — so the answer is deduplicated.
    return sorted(set(credited))


def _is_a_component(ctx: NormalizeContext, symbol: "TSASymbol") -> bool:
    """True when the symbol's own capabilities make it a component.

    The negative half of the rule (ADR-0024 §2): a symbol carrying a
    sink, agent, llm_endpoint or tool capability is that kind, and the
    classification's own branches say so. It is not a guardrail, and
    crediting it would only add a capability no branch reads.
    """
    own = caps_for_symbol(ctx.capabilities, symbol.file_path, symbol.name)
    return bool(set(own) & CLASSIFYING_CAPABILITIES)


def _component_callers(ctx: NormalizeContext) -> frozenset[str]:
    """Symbol names whose capabilities make them an agent, tool or entry.

    Read from `ctx.capabilities` across every file, and by name: the call
    graph this stage matches against is flat, so the caller is resolved
    the same way, and a name that is a component in one file is a
    component caller here (the documented collision in the module
    docstring).
    """
    callers: set[str] = set()
    for file_caps in ctx.capabilities.values():
        for symbol_caps in file_caps.symbols:
            if symbol_caps.capabilities & CALLER_CAPABILITIES:
                callers.add(symbol_caps.symbol_name)
    return frozenset(callers)


def _callers_by_callee(
    call_graph: dict[str, set[str]],
) -> dict[str, frozenset[str]]:
    """`callee name` → the callers that call it, from the flat graph.

    Built once per scan rather than scanned per symbol, and it is the
    same relation `guardrail_edges` (B.4a) tests one pair at a time:
    membership, not reachability — a symbol one hop away in a *chain*
    was not called by the component, so it is not evidence.
    """
    index: dict[str, set[str]] = {}
    for caller, callees in call_graph.items():
        for callee in callees:
            index.setdefault(callee, set()).add(caller)
    return {
        callee: frozenset(names) for callee, names in index.items()
    }


def _credit(ctx: NormalizeContext, symbol: "TSASymbol") -> None:
    """Write the capability into the symbol's map entry.

    The entry is matched the way `caps_for_symbol` matches — by file and
    symbol name — so the set the classifier reads is the set written
    here. A symbol with no capability of its own has no entry at all
    (`capability_extractor` records one only for a body that produced
    something), which is the ordinary case for the symbols this stage
    credits: the entry is created rather than looked up.
    """
    file_caps = ctx.capabilities.get(symbol.file_path)
    if file_caps is None:
        file_caps = FileCapabilities(file_path=symbol.file_path)
        ctx.capabilities[symbol.file_path] = file_caps
    for symbol_caps in file_caps.symbols:
        if symbol_caps.symbol_name == symbol.name:
            symbol_caps.capabilities.add(CREDITED_CAPABILITY)
            return
    file_caps.symbols.append(SymbolCapabilities(
        symbol_name=symbol.name,
        file_path=symbol.file_path,
        capabilities={CREDITED_CAPABILITY},
    ))
