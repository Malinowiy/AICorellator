"""Untrusted-source reads — the `untrusted_source` node kind's detector.

1.3a (ADR-0010). The graph's other nodes are actors (`agent`, `tool`,
`mcp_server`, `entry_point`) and actions (`sink`, `llm_endpoint`,
`credential`, `guardrail`). An `untrusted_source` is neither: it is the
*place* attacker-influenced content is read from — a retrieval corpus, an
MCP tool catalogue, a mailbox. Chain groups anchor on it in 1.3b, which
is why it is a node kind and not a node attribute.

Two halves, one table. `patterns.UNTRUSTED_SOURCE_RULES` maps a source
type to the `CapabilityRule` whose patterns evidence it, and
`patterns.untrusted_source_match` is the only lookup:

    capability_extractor   credits `reads_untrusted` on the reader
    this module            names the node after the source type

so a call that is credited with the capability is exactly a call that
produces a node — a divergence (a capability with no node, a node with no
capability) is not a state the graph can hold.

The source type is the *identity* of a source, and the read site is the
only evidence available: a corpus path, a tool name or a mailbox address
is not in the call. So a source node is one read site —

    id    sha256(source_type + call_target + source_file)
    name  "<source_type>:<call_target>"

which collapses repeated reads of the same handle in the same file (the
same `retriever.invoke` called from two methods is one store) and keeps
two different handles apart (a `collection.query` and a `retriever.invoke`
in one file are two stores, and the detector does not know they are the
same one).

Detection is call-site based, like every other capability (ADR-0009): no
dataflow, no values. See the untrusted-source section of `patterns.py`
for what is deliberately *not* a source in 1.3a — web scrapes (already
`http_egress`), user uploads (a structural signal, not a call shape, and
deferred rather than guessed at), and `session.list_tools` (the one
overlap with the ORM read family).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ..patterns import untrusted_source_match
from .capability_extractor import _dotted_name


@dataclass(frozen=True)
class UntrustedSourceRead:
    """One call site that reads from a known untrusted source.

    `call_target` is the dotted name as spelled in the source, which is
    the evidence for the node's identity and for its node name.
    `reader` is the innermost enclosing definition — the function or
    method whose body makes the call, or None at module level, where the
    reader is the module itself. `container` is the enclosing class when
    there is one, which is the fallback reader: a method that is not
    classified (not a tool, sink or agent) has no node of its own, and the
    class that exercises the same capability does.
    """

    source_type: str
    call_target: str
    line: int
    reader: str | None
    container: str | None


def find_untrusted_source_reads(
    module: ast.Module,
) -> list[UntrustedSourceRead]:
    """Every untrusted-source read in a parsed module, in source order.

    Order is the module's own: module-level statements first, then each
    top-level definition in the order it appears. The stage relies on
    that order to make the first read site the node's name, so the node
    is a function of the file, not of a set's iteration order.
    """
    reads: list[UntrustedSourceRead] = []

    # Module-level statements. A call at module level is attributed to
    # the module: `reader` is None, and the stage links the source to an
    # entry point only when one shares the file.
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.ClassDef):
            continue
        reads.extend(_reads_in(node, reader=None, container=None))

    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            reads.extend(
                _reads_in(node, reader=node.name, container=None)
            )
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    reads.extend(
                        _reads_in(
                            item, reader=item.name, container=node.name
                        )
                    )

    return reads


def _reads_in(
    node: ast.AST,
    *,
    reader: str | None,
    container: str | None,
) -> list[UntrustedSourceRead]:
    """Matching calls inside one statement or definition body.

    `ast.walk` descends into nested definitions, so a call inside a
    closure is attributed to the definition that lexically encloses it in
    the module's top level. That is the same over-approximation the body
    analyzers make (1.2a.1): the capability is a property of the
    enclosing symbol, and the symbol is what has a node.
    """
    reads: list[UntrustedSourceRead] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        target = _dotted_name(child.func)
        if target is None:
            continue
        match = untrusted_source_match(target)
        if match is None:
            continue
        reads.append(UntrustedSourceRead(
            source_type=match[0],
            call_target=target,
            line=child.lineno,
            reader=reader,
            container=container,
        ))
    return reads


__all__ = ["UntrustedSourceRead", "find_untrusted_source_reads"]
