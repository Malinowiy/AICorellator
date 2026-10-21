"""LethalTrifectaStage: the `lethal_trifecta` node attribute (1.3d).

The stage reads capabilities and nothing else, so these tests build
nodes by hand rather than files: the question is one combination, and
the fixture would only be in the way.

The corpus answer is zero (no node in any of the four corpora carries
all three capability sets at once), which is exactly why the definition
is pinned here on synthetic nodes — the mechanism has to be right
before a real one exists to exercise it.
"""

from __future__ import annotations

import pytest

from aicorellator.graph.models import Node
from aicorellator.parsing.normalizer_stages import LethalTrifectaStage

READ = "reads_untrusted"
SECRET = "reads_env"
EGRESS = "executes_shell"

TRIFECTA = [READ, SECRET, EGRESS]


def _node(
    node_id: str, kind: str = "tool", capabilities: list[str] | None = None,
) -> Node:
    return Node(
        id=node_id,
        kind=kind,
        name=node_id.split(":", 1)[1],
        host_id="localhost",
        capabilities=capabilities or [],
    )


def _apply(project, *nodes: Node) -> list[Node]:
    p = project()
    p.delta.nodes.extend(nodes)
    return LethalTrifectaStage().apply(p.delta, p.ctx).nodes


# ------------------------------------------------------------
# The combination
# ------------------------------------------------------------

@pytest.mark.parametrize("egress", [
    "http_egress",
    "writes_filesystem",
    "executes_shell",
    "executes_code",
])
def test_any_egress_capability_completes_the_trifecta(
    project, egress: str
) -> None:
    """The egress half is a set of four, not the shell alone.

    ADR-0009's prose lists three; `LETHAL_TRIFECTA_EGRESS_CAPS` has four
    (`executes_code` was added when the capability was split out of
    `executes_shell`), and the constant is the source of truth.
    """
    node = _node("tool:reader", capabilities=[READ, SECRET, egress])

    [flagged] = _apply(project, node)

    assert flagged.attributes["lethal_trifecta"] is True


@pytest.mark.parametrize("secret", ["reads_secrets", "reads_env"])
def test_either_secret_capability_completes_the_trifecta(
    project, secret: str
) -> None:
    """The secret half is a set of two."""
    node = _node("tool:reader", capabilities=[READ, secret, EGRESS])

    [flagged] = _apply(project, node)

    assert flagged.attributes["lethal_trifecta"] is True


@pytest.mark.parametrize("missing", [READ, SECRET, EGRESS])
def test_missing_any_one_half_is_not_the_trifecta(
    project, missing: str
) -> None:
    """Two of the three is not the trifecta — and the answer is False.

    `False`, not absent: the stage is the attribute's only producer, so
    every node it sees carries a value, and "not computed" (a graph
    written before 1.3d) stays distinguishable from "computed false".
    See the stage docstring.
    """
    caps = [c for c in TRIFECTA if c != missing]
    node = _node("tool:reader", capabilities=caps)

    [flagged] = _apply(project, node)

    assert flagged.attributes["lethal_trifecta"] is False


def test_reads_untrusted_alone_is_not_the_trifecta(project) -> None:
    """The read half on its own — the FinBot `MCPToolProvider` shape."""
    node = _node("tool:reader", capabilities=[READ])

    [flagged] = _apply(project, node)

    assert flagged.attributes["lethal_trifecta"] is False


def test_a_node_without_capabilities_is_not_the_trifecta(project) -> None:
    node = _node("tool:reader")

    [flagged] = _apply(project, node)

    assert flagged.attributes["lethal_trifecta"] is False


# ------------------------------------------------------------
# What the stage does to the graph
# ------------------------------------------------------------

def test_every_node_kind_is_visited_and_carries_the_attribute(
    project,
) -> None:
    """The attribute is not node-kind specific, unlike the body ones.

    Any kind can read untrusted input and hold secrets, so the stage
    visits the whole delta and writes a value on each node — there is no
    "not applicable" state here.
    """
    nodes = [
        _node("agent:a", "agent", TRIFECTA),
        _node("tool:t", "tool", [READ, SECRET, "http_egress"]),
        _node("sink:s", "sink", [READ]),
        _node("untrusted_source:u", "untrusted_source"),
    ]

    result = _apply(project, *nodes)

    assert [n.attributes["lethal_trifecta"] for n in result] == [
        True, True, False, False,
    ]


def test_the_attribute_is_recomputed_not_guarded(project) -> None:
    """A node that no longer qualifies loses the flag.

    `body_attributes` leaves an existing attribute alone because a
    detector may have enriched the node. Nothing produces this
    attribute but this stage, so a stale `True` would be worse than a
    recomputed `False`.
    """
    node = _node("tool:reader", capabilities=[READ])
    node.attributes["lethal_trifecta"] = True

    [flagged] = _apply(project, node)

    assert flagged.attributes["lethal_trifecta"] is False


def test_the_stage_returns_the_delta_it_was_given(project) -> None:
    node = _node("tool:reader", capabilities=TRIFECTA)
    p = project()
    p.delta.nodes.append(node)

    delta = LethalTrifectaStage().apply(p.delta, p.ctx)

    assert delta is p.delta
    assert delta.nodes == [node]


def test_the_flag_is_deterministic_across_runs(project) -> None:
    """Two runs over the same capabilities agree, bit for bit."""
    caps = [READ, "reads_secrets", "executes_code", "writes_filesystem"]

    [first] = _apply(project, _node("tool:reader", capabilities=list(caps)))
    [second] = _apply(project, _node("tool:reader", capabilities=list(caps)))

    assert first.attributes["lethal_trifecta"] is True
    assert first.attributes == second.attributes


def test_applying_twice_is_idempotent(project) -> None:
    node = _node("tool:reader", capabilities=TRIFECTA)
    p = project()
    p.delta.nodes.append(node)

    stage = LethalTrifectaStage()
    stage.apply(p.delta, p.ctx)
    stage.apply(p.delta, p.ctx)

    assert node.attributes["lethal_trifecta"] is True
