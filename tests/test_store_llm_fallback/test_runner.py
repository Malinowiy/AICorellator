"""The runner: what it fills, what it refuses, and what it links (ADR-0023).

The tests go through `run` with real files on disk, because half of what
the runner does is read source: the call shape, the code window, the
imports. `llm_call` is always injected, so no test needs a server.

F1–F5 are the acceptance criteria's fill cases; the cache and the scope
filter are pinned beside them because they are what decides *whether* a
site is asked about at all.
"""

from __future__ import annotations

import logging

import pytest
import requests

from aicorellator.graph.models import Node
from aicorellator.store.llm_fallback import FallbackCache, FallbackError
from aicorellator.store.llm_fallback.prompt import IDENTITY_SCHEMA
from aicorellator.store.llm_fallback.runner import (
    IMPORT_LINES,
    WINDOW_LINES,
)
from aicorellator.store.resolver import store_node_id

from .conftest import FakeLLM, answer, read_site, upload, write_site


HANDLER = "app/upload.py"

# A handler whose store is named by its docstring and whose `open` has a
# parameter for a path: exactly the gap ADR-0023 §1 describes.
SOURCE = '''\
def handle(upload_dir, payload):
    """Store the uploaded file under /uploads/."""
    with open(upload_dir, "wb") as fh:
        fh.write(payload)
    with open(upload_dir, "rb") as fh:
        return fh.read()
'''

WRITE_LINE = 3
READ_LINE = 5


def writer_node() -> Node:
    """The component the write site lives in, as the delta holds it."""
    return Node(
        id="tool:handle", kind="tool", name="handle", host_id="localhost",
        attributes={"source_file": HANDLER, "line_start": 1},
    )


def source_node() -> Node:
    """The `untrusted_source` the read site reads, at the read's line."""
    return Node(
        id="untrusted_source:read", kind="untrusted_source",
        name="user_upload:open", host_id="localhost",
        attributes={
            "source_file": HANDLER,
            "source_type": "user_upload",
            "line_start": READ_LINE,
        },
    )


def _case(case, **kwargs):
    """A case with the handler file, both nodes and an upload site."""
    return case(
        files={HANDLER: SOURCE},
        nodes=[writer_node(), source_node()],
        upload_sites=[upload()],
        **kwargs,
    )


# ============================================================
# F1 — a gap is filled
# ============================================================

def test_a_named_site_creates_the_store_and_its_edge(case) -> None:
    """F1 — one write site, one answer, one store and one `writes_to`."""
    fake = FakeLLM(answer("/uploads/", 0.9))
    built = _case(case)

    stores, edges = built.run(
        [write_site(line=WRITE_LINE)], llm_call=fake,
    )

    assert [n.name for n in stores] == ["fs:/uploads/"]
    assert stores[0].id == store_node_id("fs", "/uploads/")
    assert stores[0].kind == "store"
    assert stores[0].attributes == {
        "family": "fs",
        "identity_hint": "/uploads/",
        "identity_source": "llm",
    }

    assert len(edges) == 1
    edge = edges[0]
    assert edge.kind == "writes_to"
    assert edge.source_id == "tool:handle"
    assert edge.target_id == stores[0].id
    assert edge.flow_kind == "structural"
    assert edge.observed_by == ["ast"]
    assert edge.data_flow_confirmed is None
    assert edge.identity_confidence == 0.2
    assert edge.attributes == {
        "identity_source": "llm",
        "identity_confidence_llm": 0.9,
    }
    assert len(fake.calls) == 1


def test_a_read_site_links_the_store_to_its_source(case) -> None:
    """F1 — the read direction: `store → untrusted_source`."""
    fake = FakeLLM(answer("/uploads/"))
    built = _case(case)

    stores, edges = built.run([read_site(line=READ_LINE)], llm_call=fake)

    assert [n.name for n in stores] == ["fs:/uploads/"]
    assert len(edges) == 1
    assert edges[0].kind == "reads_from"
    assert edges[0].source_id == stores[0].id
    assert edges[0].target_id == "untrusted_source:read"
    assert edges[0].identity_confidence == 0.2


def test_both_directions_share_one_store(case) -> None:
    """One hint, one store: two sites are one place (ADR-0021 §1)."""
    fake = FakeLLM(answer("/uploads/"), answer("/uploads/"))
    built = _case(case)

    stores, edges = built.run(
        [write_site(line=WRITE_LINE), read_site(line=READ_LINE)],
        llm_call=fake,
    )

    assert len(stores) == 1
    by_kind = {e.kind: e for e in edges}
    assert set(by_kind) == {"writes_to", "reads_from"}
    assert by_kind["writes_to"].source_id == "tool:handle"
    assert by_kind["writes_to"].target_id == stores[0].id
    assert by_kind["reads_from"].source_id == stores[0].id
    assert by_kind["reads_from"].target_id == "untrusted_source:read"


def test_the_prompt_carries_the_site_and_the_window(case) -> None:
    """The model is asked about *this* call site and *this* code."""
    fake = FakeLLM(answer("/uploads/"))
    built = _case(case)

    built.run([write_site(line=WRITE_LINE)], llm_call=fake)

    system, user = fake.calls[0]
    assert "[UNTRUSTED_DATA]" in system
    assert "call: open" in user
    assert "upload_dir" in user
    assert f"file: {HANDLER}" in user


def test_a_site_reported_twice_is_asked_about_once(case) -> None:
    """Two families can report one call; the model is asked once."""
    fake = FakeLLM(answer("/uploads/"))
    built = _case(case)

    stores, edges = built.run(
        [write_site(line=WRITE_LINE), write_site(line=WRITE_LINE)],
        llm_call=fake,
    )

    assert len(fake.calls) == 1
    assert len(stores) == 1
    assert len(edges) == 1


# ============================================================
# F2/F3 — what is not sent
# ============================================================

def test_a_deterministic_site_is_not_sent(case) -> None:
    """F2 — the deterministic answer wins; the fallback fills gaps only."""
    fake = FakeLLM()
    built = _case(case)

    stores, edges = built.run(
        [write_site(line=WRITE_LINE, identity_hint="/uploads/")],
        llm_call=fake,
    )

    assert fake.calls == []
    assert stores == []
    assert edges == []


def test_a_site_outside_the_writers_scope_is_not_sent(case) -> None:
    """F3 — no upload site, no scope, no question (ADR-0022 §1)."""
    fake = FakeLLM()
    built = case(
        files={HANDLER: SOURCE},
        nodes=[writer_node(), source_node()],
        upload_sites=[],
    )

    stores, edges = built.run([write_site(line=WRITE_LINE)], llm_call=fake)

    assert fake.calls == []
    assert stores == []
    assert edges == []


def test_a_file_the_writer_imports_is_in_scope(case) -> None:
    """The scope is the writer's file or a file it imports either way."""
    fake = FakeLLM(answer("/uploads/"))
    built = case(
        files={
            HANDLER: SOURCE,
            "app/storage.py": "def save(p):\n    return open(p, 'wb')\n",
        },
        nodes=[
            writer_node(),
            source_node(),
            Node(
                id="tool:save", kind="tool", name="save",
                host_id="localhost",
                attributes={"source_file": "app/storage.py", "line_start": 1},
            ),
        ],
        upload_sites=[upload()],
    )

    stores, _ = built.run(
        [write_site(file_path="app/storage.py", line=2)],
        llm_call=fake,
        imports_by_file={HANDLER: ["app.storage"]},
    )

    assert [n.name for n in stores] == ["fs:/uploads/"]


def test_a_file_out_of_scope_is_not_sent(case) -> None:
    """An unrelated module's unresolved site is not asked about."""
    fake = FakeLLM()
    built = case(
        files={
            HANDLER: SOURCE,
            "app/other.py": "def f(p):\n    return open(p, 'wb')\n",
        },
        nodes=[writer_node(), source_node()],
        upload_sites=[upload()],
    )

    stores, edges = built.run(
        [write_site(file_path="app/other.py", line=2)], llm_call=fake,
    )

    assert fake.calls == []
    assert stores == []
    assert edges == []


# ============================================================
# F4/F5 — a gap that stays a gap
# ============================================================

def test_a_null_hint_is_no_store_and_no_edges(case, caplog) -> None:
    """F4 — "the code names no store" is an answer, and it is a warning."""
    fake = FakeLLM(answer(None, 0.0))
    built = _case(case)

    with caplog.at_level(logging.WARNING):
        stores, edges = built.run(
            [write_site(line=WRITE_LINE)], llm_call=fake,
        )

    assert stores == []
    assert edges == []
    assert "stays unresolved" in caplog.text


def test_no_answer_is_no_store_and_no_edges(case, caplog) -> None:
    """A timeout is not an answer: nothing is created, nothing is cached."""
    fake = FakeLLM(None)
    built = _case(case)

    with caplog.at_level(logging.WARNING):
        stores, edges = built.run(
            [write_site(line=WRITE_LINE)], llm_call=fake,
        )

    assert stores == []
    assert edges == []
    assert "stays unresolved" in caplog.text
    with FallbackCache(built.cache_path) as cache:
        assert cache.count() == 0


def test_an_unusable_answer_is_a_warning_in_auto(case, caplog) -> None:
    """F5 (auto) — a malformed answer leaves the site unresolved."""
    fake = FakeLLM("not json at all")
    built = _case(case)

    with caplog.at_level(logging.WARNING):
        stores, edges = built.run(
            [write_site(line=WRITE_LINE)], llm_call=fake,
        )

    assert stores == []
    assert edges == []
    assert "unusable answer" in caplog.text


def test_an_unusable_answer_is_an_error_in_required(case) -> None:
    """F5 (required) — the analyst asked for the layer, and a gap fails."""
    fake = FakeLLM("not json at all")
    built = _case(case)

    with pytest.raises(FallbackError, match="required"):
        built.run(
            [write_site(line=WRITE_LINE)], llm_call=fake, mode="required",
        )


def test_a_null_hint_is_an_error_in_required(case) -> None:
    fake = FakeLLM(answer(None, 0.0))
    built = _case(case)

    with pytest.raises(FallbackError, match="named no store"):
        built.run(
            [write_site(line=WRITE_LINE)], llm_call=fake, mode="required",
        )


def test_a_named_store_with_no_writer_node_earns_nothing(
    case, caplog,
) -> None:
    """The model named a store, and nothing in the graph writes to it."""
    fake = FakeLLM(answer("/uploads/"))
    built = case(
        files={HANDLER: SOURCE},
        nodes=[source_node()],
        upload_sites=[upload()],
    )

    with caplog.at_level(logging.WARNING):
        stores, edges = built.run(
            [write_site(line=WRITE_LINE)], llm_call=fake,
        )

    assert stores == []
    assert edges == []
    assert "has no node in the graph" in caplog.text


def test_a_named_store_with_no_source_node_earns_nothing(case) -> None:
    """The read direction has the same rule: no `reads_from`, no store."""
    fake = FakeLLM(answer("/uploads/"))
    built = case(
        files={HANDLER: SOURCE},
        nodes=[writer_node()],
        upload_sites=[upload()],
    )

    stores, edges = built.run([read_site(line=READ_LINE)], llm_call=fake)

    assert stores == []
    assert edges == []


def test_an_unparseable_file_is_skipped(case) -> None:
    """No module, no window, no question — and not a scan failure."""
    fake = FakeLLM()
    built = case(
        files={HANDLER: "def broken(:\n"},
        nodes=[writer_node()],
        upload_sites=[upload()],
    )

    stores, edges = built.run(
        [write_site(line=WRITE_LINE)], llm_call=fake, mode="required",
    )

    assert fake.calls == []
    assert stores == []
    assert edges == []


# ============================================================
# The cache (C2) and the window
# ============================================================

def test_a_second_run_is_served_from_the_cache(case) -> None:
    """C2 — the same site, the same key, no second call."""
    first = FakeLLM(answer("/uploads/"))
    built = _case(case)
    built.run([write_site(line=WRITE_LINE)], llm_call=first)
    assert len(first.calls) == 1

    second = FakeLLM()
    stores, edges = built.run(
        [write_site(line=WRITE_LINE)], llm_call=second,
    )

    assert second.calls == []
    assert len(stores) == 1
    assert len(edges) == 1


def test_a_cached_null_hint_is_not_re_asked(case) -> None:
    """A null answer is an answer: the second scan does not ask again."""
    built = _case(case)
    built.run([write_site(line=WRITE_LINE)], llm_call=FakeLLM(answer(None)))

    second = FakeLLM()
    stores, edges = built.run(
        [write_site(line=WRITE_LINE)], llm_call=second,
    )

    assert second.calls == []
    assert stores == []
    assert edges == []


def test_a_changed_window_is_a_new_question(case) -> None:
    """The code is the question, so editing it re-asks."""
    built = _case(case)
    built.run([write_site(line=WRITE_LINE)], llm_call=FakeLLM(answer("a")))

    edited = case(
        files={HANDLER: SOURCE.replace("/uploads/", "/documents/")},
        nodes=[writer_node(), source_node()],
        upload_sites=[upload()],
        cache=built.cache_path,
    )
    second = FakeLLM(answer("/documents/"))
    stores, _ = edited.run(
        [write_site(line=WRITE_LINE)], llm_call=second,
    )

    assert len(second.calls) == 1
    assert [n.name for n in stores] == ["fs:/documents/"]


def test_the_window_and_the_imports_are_capped() -> None:
    """A window and an import list are bounded, so a call is bounded."""
    assert (WINDOW_LINES, IMPORT_LINES) == (60, 40)


def test_the_window_is_the_enclosing_definition(case) -> None:
    """The window is the handler's body, not the file's preamble."""
    fake = FakeLLM(answer("/uploads/"))
    built = _case(case)

    built.run([write_site(line=WRITE_LINE)], llm_call=fake)

    _, user = fake.calls[0]
    assert '"""Store the uploaded file under /uploads/."""' in user
    assert "imports: []" in user


# ============================================================
# The HTTP path (the probe D.2 does, restated)
# ============================================================

class FakeResponse:
    """The parts of `requests.Response` this module reads."""

    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def completion(text: str, model: str = "test/model") -> dict:
    """An OpenAI-compatible success body."""
    return {
        "model": model,
        "choices": [
            {"message": {"content": text}, "finish_reason": "stop"}
        ],
    }


@pytest.fixture
def posts(monkeypatch):
    """Capture `requests.post` calls and script the responses.

    Returns a list of `(url, payload, timeout)` tuples. Responses are
    consumed in call order; once the scripted ones run out, a 200 with a
    valid answer is returned.
    """

    def install(*responses):
        calls: list[tuple[str, dict, int]] = []
        queue = list(responses)

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append((url, json, timeout))
            if queue:
                return queue.pop(0)
            return FakeResponse(payload=completion(answer("/uploads/")))

        monkeypatch.setattr(requests, "post", fake_post)
        return calls

    return install


def test_the_request_body_carries_the_schema(case, posts) -> None:
    """Schema-constrained decoding is the reason for this wire format."""
    calls = posts()
    built = _case(case)

    built.run([write_site(line=WRITE_LINE)])

    url, payload, timeout = calls[0]
    assert url == "http://localhost:1234/v1/chat/completions"
    assert payload["model"] == "test/model"
    assert payload["response_format"] == IDENTITY_SCHEMA
    assert payload["temperature"] == 0.0
    assert payload["max_tokens"] == 400
    assert payload["stream"] is False
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert timeout == 120


def test_a_400_steps_the_response_format_down(case, posts) -> None:
    """A server that rejects `json_schema` may accept `json_object`."""
    calls = posts(
        FakeResponse(400, text="unsupported response_format"),
        FakeResponse(payload=completion(answer("/uploads/"))),
    )
    built = _case(case)

    stores, _ = built.run([write_site(line=WRITE_LINE)])

    assert [call[1]["response_format"] for call in calls] == [
        IDENTITY_SCHEMA,
        {"type": "json_object"},
    ]
    assert [n.name for n in stores] == ["fs:/uploads/"]


def test_every_rejected_form_means_no_answer(case, posts, caplog) -> None:
    """Three refusals are three refusals: the site stays unresolved."""
    calls = posts(
        FakeResponse(400, text="no"),
        FakeResponse(400, text="no"),
        FakeResponse(400, text="no"),
    )
    built = _case(case)

    with caplog.at_level(logging.WARNING):
        stores, edges = built.run([write_site(line=WRITE_LINE)])

    assert len(calls) == 3
    assert stores == []
    assert edges == []
    assert "stays unresolved" in caplog.text


def test_a_non_200_is_no_answer(case, posts, caplog) -> None:
    calls = posts(FakeResponse(500, text="boom"))
    built = _case(case)

    with caplog.at_level(logging.WARNING):
        stores, edges = built.run([write_site(line=WRITE_LINE)])

    assert len(calls) == 1
    assert stores == []
    assert edges == []


def test_a_transport_error_is_no_answer(case, monkeypatch, caplog) -> None:
    def boom(url, json=None, headers=None, timeout=None):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "post", boom)
    built = _case(case)

    with caplog.at_level(logging.WARNING):
        stores, edges = built.run([write_site(line=WRITE_LINE)])

    assert stores == []
    assert edges == []
    assert "request failed" in caplog.text


def test_an_unusable_response_shape_is_no_answer(
    case, posts, caplog,
) -> None:
    calls = posts(FakeResponse(payload={"unexpected": True}))
    built = _case(case)

    with caplog.at_level(logging.WARNING):
        stores, edges = built.run([write_site(line=WRITE_LINE)])

    assert len(calls) == 1
    assert stores == []
    assert edges == []
    assert "unusable response shape" in caplog.text

