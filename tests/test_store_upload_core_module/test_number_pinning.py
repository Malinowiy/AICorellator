"""The module-level numbers, computed from `confidence`'s constants (P1).

§8's signals are independent and combine multiplicatively
(`combine_signals`), so the numbers are products and not sums:

    module body         1 − (1 − 0.4) × (1 − 0.6) × (1 − 0.2) = 0.808
    top-level function  1 − (1 − 0.5) × (1 − 0.6) × (1 − 0.2) = 0.84

The brief's prose gives `0.4 + 0.6 + 0.2 = 0.896` for the first one, which
adds the signals and is not what `combine_signals` computes. The rule is
the combination, so these are the numbers, and they are pinned against the
constants and not against the prose — the way
`tests/test_store_upload_core/test_writer.py` pins §8's 0.872 / 0.84 /
0.8. The `0.896` assertion below is there so the discrepancy is a
recorded decision and not a silent one.

The other numbers the branch produces follow from the same constants: a
write in another file's function loses the same-module signal and is
`1 − 0.5 × 0.4 = 0.8`, and the weakest link the branch can state is still
above §9's default threshold of 0.7.
"""

from __future__ import annotations

from aicorellator.store.confidence import (
    CODE_CONTEXT_SAME_MODULE,
    IDENTITY_CONFIDENCE_CAP,
    LITERAL_EQUAL,
    combine_signals,
)
from aicorellator.store.context.upload.writer import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    UPLOAD_WRITER_BASE,
    UPLOAD_WRITER_BASE_MODULE,
)

from .conftest import MODULE, run_module

MODULE_BODY = (
    'with open("/uploads/report.txt", "wb") as fh:\n'
    "    fh.write('x')\n"
)

TOP_LEVEL = (
    "def save(payload):\n"
    '    with open("/uploads/report.txt", "wb") as fh:\n'
    "        fh.write(payload)\n"
)


def _module_body_link() -> float:
    """The module body's link: base 0.4, equal literal, same module."""
    return combine_signals(
        UPLOAD_WRITER_BASE_MODULE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
    )


def _function_link() -> float:
    """A top-level function's link: base 0.5, equal literal, same module."""
    return combine_signals(
        UPLOAD_WRITER_BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
    )


def test_the_module_body_number_is_the_product_of_its_signals() -> None:
    """P1 — `1 − 0.6 × 0.4 × 0.8 = 0.808`, from the constants."""
    assert round(_module_body_link(), 4) == 0.808
    # The brief's prose, for the record: adding the signals is not the rule.
    assert round(_module_body_link(), 4) != 0.896


def test_the_top_level_function_number_is_the_product_of_its_signals() -> None:
    """P1 — `1 − 0.5 × 0.4 × 0.8 = 0.84`, from the constants."""
    assert round(_function_link(), 4) == 0.84


def test_the_module_body_base_is_below_the_function_base() -> None:
    """The rule the numbers encode: a function is stronger than a module.

    The brief leaves the number to the implementation and fixes the
    ordering — "the handler is a discrete unit and the module is a file".
    The bases and the links they produce both keep it, so a module body's
    claim is never the equal of a function's.
    """
    assert UPLOAD_WRITER_BASE_MODULE < UPLOAD_WRITER_BASE
    assert _module_body_link() < _function_link()


def test_the_writer_applies_those_numbers(project) -> None:
    """P1 — the writer's two placements, against the computed constants.

    The same two fixtures `test_writer_module.py` uses, so the constants
    and the code are tied together in one place: a change to either moves
    both assertions.
    """
    _, body_edges = run_module(project, {MODULE: MODULE_BODY})
    _, function_edges = run_module(
        project, {MODULE: TOP_LEVEL}, call_graph={"save": set()},
    )

    assert round(body_edges[0].identity_confidence, 4) == round(
        _module_body_link(), 4
    )
    assert round(function_edges[0].identity_confidence, 4) == round(
        _function_link(), 4
    )


def test_the_weakest_module_link_is_still_above_the_default_threshold(
    project,
) -> None:
    """P1 — no combination without an equal literal, and the floor holds.

    The weakest link the branch states is a write in another file's
    function, `1 − 0.5 × 0.4 = 0.8`; §9's default threshold is 0.7, so
    every link the branch can state survives the writer's own floor.
    """
    _, edges = run_module(
        project,
        {MODULE: "def handle(payload):\n    save(payload)\n",
         "app/storage.py": TOP_LEVEL},
        call_graph={"handle": {"save"}, "save": set()},
    )

    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.8]
    assert edges[0].identity_confidence > DEFAULT_CONFIDENCE_THRESHOLD


def test_no_combination_reaches_the_cap() -> None:
    """ADR-0021 §7 — the strongest link is short of certainty."""
    assert _function_link() < IDENTITY_CONFIDENCE_CAP
