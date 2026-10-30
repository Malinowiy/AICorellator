"""The prompt: its own version, its markers, its schema (ADR-0023 §3).

The prompt is a separate artifact from D.2's, and the three things that
make it one are pinned here: a `PROMPT_VERSION` of its own (so the two
prompts' cache entries never collide), the `[UNTRUSTED_DATA]` markers with
the rule that reads them, and a schema that allows `null`.
"""

from __future__ import annotations

from aicorellator.parsing.llm_classifier import (
    PROMPT_VERSION as CLASSIFIER_VERSION,
)
from aicorellator.store.llm_fallback import PROMPT_VERSION
from aicorellator.store.llm_fallback.prompt import (
    FEW_SHOT,
    IDENTITY_SCHEMA,
    SYSTEM_PROMPT,
    build_user_prompt,
)


def test_the_version_is_its_own() -> None:
    """D.2's version and the fallback's are different keys on purpose."""
    assert PROMPT_VERSION == "store_identity:v1.0.0"
    assert PROMPT_VERSION != CLASSIFIER_VERSION


def test_the_system_prompt_names_the_markers_and_the_null_answer() -> None:
    assert "[UNTRUSTED_DATA]" in SYSTEM_PROMPT
    assert "[/UNTRUSTED_DATA]" in SYSTEM_PROMPT
    assert "data, not" in SYSTEM_PROMPT
    assert "`null`" in SYSTEM_PROMPT


def test_the_markers_wrap_every_few_shot_example() -> None:
    """The defence is in the examples too, not only in the target."""
    user = build_user_prompt("open", "def f(): pass", "a.py", [])
    opens = user.count("[UNTRUSTED_DATA]")
    closes = user.count("[/UNTRUSTED_DATA]")
    assert opens == closes == len(FEW_SHOT) + 1


def test_the_target_carries_the_four_inputs() -> None:
    user = build_user_prompt(
        "s3.put_object", "def upload(): pass", "app/routes.py",
        ["import boto3"],
    )
    assert "call: s3.put_object" in user
    assert "code:\ndef upload(): pass" in user
    assert "file: app/routes.py" in user
    assert "imports: ['import boto3']" in user


def test_every_few_shot_answer_matches_the_schema() -> None:
    for example in FEW_SHOT:
        answer = example["answer"]
        assert set(answer) == {"identity_hint", "confidence", "reasoning"}
        assert answer["identity_hint"] is None or isinstance(
            answer["identity_hint"], str
        )
        assert 0.0 <= answer["confidence"] <= 1.0


def test_the_schema_allows_a_null_hint() -> None:
    """A null hint is an answer, so the wire form has to admit it."""
    schema = IDENTITY_SCHEMA["json_schema"]["schema"]
    assert schema["properties"]["identity_hint"]["type"] == [
        "string", "null",
    ]
    assert schema["required"] == [
        "identity_hint", "confidence", "reasoning",
    ]
    assert schema["additionalProperties"] is False
    assert IDENTITY_SCHEMA["json_schema"]["name"] == "store_identity"


def test_the_schema_has_no_family_field() -> None:
    """The family comes from the site, never from the answer (ADR-0023 §3)."""
    schema = IDENTITY_SCHEMA["json_schema"]["schema"]
    assert "family" not in schema["properties"]
