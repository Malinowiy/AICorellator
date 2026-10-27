"""Answer validation, and the HTTP path (D.2).

`parse_answer` is the layer's whole contract in one function, so it gets
the hostile cases: non-JSON, a non-object, an unknown kind, a capability
nobody defined. The transport tests monkeypatch `requests.post` — they
care about the request body and about what a status code means, not about
a server.
"""

from __future__ import annotations

import json
import logging

import pytest
import requests

from aicorellator.parsing.llm_classifier import (
    Classifier,
    ClassifierConfig,
    SymbolClassification,
    classify_file,
    parse_answer,
)
from aicorellator.parsing.llm_classifier.classifier import FRAMEWORK
from aicorellator.parsing.llm_classifier.prompt import CLASSIFICATION_SCHEMA

from .conftest import FakeLLM, answer, symbol


# ============================================================
# parse_answer
# ============================================================

def test_a_well_formed_answer_is_accepted() -> None:
    assert parse_answer(answer("tool", ("reads_env",))) == SymbolClassification(
        kind="tool",
        capabilities=("reads_env",),
        reasoning="the body says tool",
    )


def test_capabilities_are_deduplicated_and_sorted() -> None:
    parsed = parse_answer(answer("agent", ("calls_llm", "reads_env", "calls_llm")))
    assert parsed.capabilities == ("calls_llm", "reads_env")


def test_unknown_capabilities_are_dropped() -> None:
    """The vocabulary is closed and shared with the deterministic extractor."""
    parsed = parse_answer(answer("tool", ("reads_filesystem", "teleports")))
    assert parsed.capabilities == ("reads_filesystem",)


def test_absent_capabilities_are_an_empty_tuple() -> None:
    text = json.dumps({"kind": "tool", "reasoning": "no caps"})
    assert parse_answer(text).capabilities == ()


def test_missing_reasoning_is_allowed() -> None:
    """`reasoning` is not cached or stored, so a missing one is not fatal."""
    assert parse_answer('{"kind": "sink", "capabilities": []}').reasoning == ""


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        "",
        "[]",
        '"a string"',
        "null",
        '{"capabilities": []}',
        '{"kind": null, "capabilities": []}',
        '{"kind": 7, "capabilities": []}',
        '{"kind": "orchestrator", "capabilities": []}',
        '{"kind": "Tool", "capabilities": []}',
        '{"kind": "tool", "capabilities": "reads_env"}',
        '{"kind": "tool", "capabilities": {"reads_env": true}}',
    ],
)
def test_a_malformed_answer_is_rejected(text: str) -> None:
    assert parse_answer(text) is None


def test_fenced_json_is_accepted() -> None:
    """Small models wrap the object in a code fence; that is not a failure."""
    text = "```json\n" + answer("tool") + "\n```"
    assert parse_answer(text).kind == "tool"


def test_json_wrapped_in_prose_is_accepted() -> None:
    text = "Here is the answer: " + answer("sink") + " Hope that helps."
    assert parse_answer(text).kind == "sink"


def test_part_of_parent_and_unknown_are_valid_answers() -> None:
    """They mean "no node", which is an answer — not a malformed one."""
    assert parse_answer(answer("part_of_parent")).kind == "part_of_parent"
    assert parse_answer(answer("unknown")).kind == "unknown"


# ============================================================
# The HTTP path
# ============================================================

class FakeResponse:
    """The parts of `requests.Response` this module reads."""

    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError("no json", "", 0)
        return self._payload


def completion(text: str, model: str = "test/model") -> dict:
    """An OpenAI-compatible success body."""
    return {
        "model": model,
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
    }


@pytest.fixture
def posts(monkeypatch):
    """Capture `requests.post` calls and script the responses.

    Returns a list of `(url, payload, timeout)` tuples. Responses are
    consumed in call order; once the scripted ones run out, a 200 with a
    valid `tool` answer is returned.
    """

    def install(*responses):
        calls: list[tuple[str, dict, int]] = []
        queue = list(responses)

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append((url, json, timeout))
            if queue:
                return queue.pop(0)
            return FakeResponse(payload=completion(answer("tool")))

        monkeypatch.setattr(requests, "post", fake_post)
        return calls

    return install


def test_the_request_body_carries_the_schema(posts) -> None:
    """Schema-constrained decoding is the reason for this wire format."""
    calls = posts()
    layer = Classifier(ClassifierConfig(model="test/model"))

    layer.classify_file("pkg/mod.py", [symbol("read")])

    url, payload, timeout = calls[0]
    assert url == "http://localhost:1234/v1/chat/completions"
    assert payload["model"] == "test/model"
    assert payload["response_format"] == CLASSIFICATION_SCHEMA
    assert payload["temperature"] == 0.0
    assert payload["max_tokens"] == 800
    assert payload["stream"] is False
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert timeout == 120


def test_a_400_steps_the_response_format_down(posts) -> None:
    """A server that rejects `json_schema` may accept `json_object`.

    The benchmark's probe, inline: the first real symbol finds a form the
    endpoint accepts, and the accepted form is remembered.
    """
    calls = posts(
        FakeResponse(400, text="unsupported response_format"),
        FakeResponse(payload=completion(answer("tool"))),
    )
    layer = Classifier(ClassifierConfig(model="test/model"))

    result = layer.classify_file("pkg/mod.py", [symbol("read")])

    assert [call[1]["response_format"] for call in calls] == [
        CLASSIFICATION_SCHEMA,
        {"type": "json_object"},
    ]
    assert result.nodes[0].kind == "tool"

    # The accepted form is remembered: the next symbol asks once.
    layer.classify_file("pkg/mod.py", [symbol("write")])
    assert calls[-1][1]["response_format"] == {"type": "json_object"}


def test_every_rejected_form_means_no_answer(posts) -> None:
    calls = posts(
        FakeResponse(400, text="no"),
        FakeResponse(400, text="no"),
        FakeResponse(400, text="no"),
    )
    layer = Classifier(ClassifierConfig(model="test/model"))

    assert layer.classify_file("pkg/mod.py", [symbol("read")]).nodes == []
    assert [call[1].get("response_format") for call in calls] == [
        CLASSIFICATION_SCHEMA,
        {"type": "json_object"},
        None,
    ]


def test_a_connection_error_does_not_step_the_format_down(monkeypatch) -> None:
    """Only HTTP 400 means "the request shape is wrong".

    An endpoint that is down must not be tried three times per symbol: that
    would be three timeouts per symbol, on a scan that is already failing.
    """
    attempts: list[str] = []

    def refusing(url, json=None, headers=None, timeout=None):
        attempts.append(url)
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "post", refusing)
    layer = Classifier(ClassifierConfig(model="test/model"))

    assert layer.classify_file("pkg/mod.py", [symbol("read")]).nodes == []
    assert len(attempts) == 1
    assert layer._response_format == "schema"


def test_a_timeout_is_no_answer(monkeypatch, caplog) -> None:
    def timing_out(url, json=None, headers=None, timeout=None):
        raise requests.Timeout("too slow")

    monkeypatch.setattr(requests, "post", timing_out)
    layer = Classifier(ClassifierConfig(model="test/model"))

    with caplog.at_level(logging.WARNING):
        result = layer.classify_file("pkg/mod.py", [symbol("read")])

    assert result.nodes == []
    assert "timeout" in caplog.text


@pytest.mark.parametrize("status", [500, 503, 404])
def test_a_non_200_is_dropped(posts, status: int) -> None:
    posts(FakeResponse(status, text="server error"))
    layer = Classifier(ClassifierConfig(model="test/model"))

    assert layer.classify_file("pkg/mod.py", [symbol("read")]).nodes == []


def test_an_unusable_response_shape_is_dropped(posts) -> None:
    posts(FakeResponse(payload={"choices": []}))
    layer = Classifier(ClassifierConfig(model="test/model"))

    assert layer.classify_file("pkg/mod.py", [symbol("read")]).nodes == []


def test_an_injected_call_never_touches_the_network(monkeypatch) -> None:
    """Every other test in this package depends on this being true."""
    def explode(*args, **kwargs):
        raise AssertionError("requests.post must not be called")

    monkeypatch.setattr(requests, "post", explode)
    layer = Classifier(
        ClassifierConfig(model="test/model"),
        llm_call=FakeLLM(answer("tool")),
    )

    assert layer.classify_file("pkg/mod.py", [symbol("read")]).nodes[0].kind == (
        "tool"
    )


# ============================================================
# The convenience wrapper
# ============================================================

def test_classify_file_builds_a_classifier_per_call() -> None:
    """One file by hand, with no scan to hang a `Classifier` on."""
    result = classify_file(
        "pkg/mod.py",
        [symbol("read")],
        config=ClassifierConfig(model="test/model"),
        llm_call=FakeLLM(answer("sink", ("executes_shell",))),
    )

    assert result.framework == FRAMEWORK
    assert result.nodes[0].kind == "sink"
    assert result.edges == []
