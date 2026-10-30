"""`parse_answer` — the whole contract, one function (ADR-0023 §3).

The two claims are checked against the schema the prompt advertised: a
JSON object, an `identity_hint` that is a string or `null`, a `confidence`
in 0.0–1.0. Anything else is None, and the caller turns None into "left
unresolved" and does not cache it.
"""

from __future__ import annotations

import json

import pytest

from aicorellator.store.llm_fallback import StoreIdentity, parse_answer


def _text(**fields) -> str:
    return json.dumps(fields)


def test_a_valid_answer() -> None:
    answer = parse_answer(_text(
        identity_hint="docs", confidence=0.9, reasoning="the docstring",
    ))
    assert answer == StoreIdentity("docs", 0.9, "the docstring")


def test_a_null_hint_is_valid() -> None:
    """The prompt's own "the code names no store" is an answer."""
    assert parse_answer(_text(
        identity_hint=None, confidence=0.0, reasoning="nothing here",
    )) == StoreIdentity(None, 0.0, "nothing here")


def test_a_missing_confidence_is_zero() -> None:
    assert parse_answer(_text(identity_hint="docs")) == StoreIdentity("docs")


def test_a_missing_reasoning_is_empty() -> None:
    answer = parse_answer(_text(identity_hint="docs", confidence=0.5))
    assert answer == StoreIdentity("docs", 0.5, "")


def test_a_non_string_reasoning_is_dropped() -> None:
    answer = parse_answer(_text(
        identity_hint="docs", confidence=0.5, reasoning=7,
    ))
    assert answer.reasoning == ""


def test_a_blank_hint_is_a_null_hint() -> None:
    """A name that is only whitespace names nothing."""
    assert parse_answer(_text(
        identity_hint="   ", confidence=0.0,
    )) == StoreIdentity(None, 0.0, "")


def test_the_hint_is_stripped() -> None:
    assert parse_answer(_text(
        identity_hint="  docs  ", confidence=0.5,
    )).identity_hint == "docs"


def test_the_confidence_may_be_an_integer() -> None:
    assert parse_answer(_text(
        identity_hint="docs", confidence=1,
    )).confidence == 1.0


def test_json_in_a_fence_is_parsed() -> None:
    """D.2's JSON extraction is reused, fences and prose included."""
    text = "```json\n" + _text(identity_hint="docs", confidence=0.5) + "\n```"
    assert parse_answer(text).identity_hint == "docs"


def test_prose_around_the_json_is_parsed() -> None:
    text = "Here is the answer: " + _text(identity_hint="docs")
    assert parse_answer(text).identity_hint == "docs"


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        "{}",
        '{"confidence": 0.5}',
        "[]",
        "7",
        '"docs"',
        _text(identity_hint=7, confidence=0.5),
        _text(identity_hint=["docs"], confidence=0.5),
        _text(identity_hint={"name": "docs"}, confidence=0.5),
        _text(identity_hint="docs", confidence="high"),
        _text(identity_hint="docs", confidence=True),
        _text(identity_hint="docs", confidence=1.5),
        _text(identity_hint="docs", confidence=-0.1),
        _text(identity_hint="docs", confidence=None),
    ],
)
def test_an_unusable_answer_is_none(text: str) -> None:
    assert parse_answer(text) is None


def test_the_kind_of_the_answer_is_the_hint_not_the_technology() -> None:
    """The schema has no family: an answer can be wrong, not inventive."""
    answer = parse_answer(_text(identity_hint="s3:docs", confidence=0.9))
    assert answer.identity_hint == "s3:docs"
    assert not hasattr(answer, "family")
