"""The runner: modes, what is sent, and what is done with what is left (D.2).

These tests go through `run_classifier` with real files on disk, because
half of what the runner does is read source: the prompt's symbol text, the
file's imports, the decorators limiter 3 reads, and the AST definition that
has to match a TSA symbol.
"""

from __future__ import annotations

import logging

import pytest

from aicorellator.graph.models import Node
from aicorellator.parsing.framework_detectors import DetectionResult
from aicorellator.parsing.llm_classifier import (
    ClassifierConfigError,
    ClassifierError,
)
from aicorellator.parsing.llm_classifier.cache import ClassifierCache
from aicorellator.parsing.llm_classifier.classifier import FRAMEWORK

from .conftest import FakeLLM, answer


MODULE = '''\
import os

from pkg import helper


class Agent:
    def run(self):
        return None


class Helper:
    def adjust(self):
        return None


def standalone(value):
    return value
'''


# ============================================================
# mode: off
# ============================================================

def test_mode_off_makes_no_call(scan, tsa) -> None:
    """The default. No request, no nodes, and still a labelled result."""
    fake = FakeLLM()
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
    ).run(mode="off", action="skip", llm_call=fake)

    assert fake.calls == []
    assert result.nodes == []
    assert result.edges == []
    assert result.framework == FRAMEWORK


def test_mode_off_never_reads_the_model_section(scan, tsa) -> None:
    """A scan that does not call the layer must not need its config.

    No config file is written at all here: `mode: off` has to return before
    anything tries to load `llm_classifier_model`.
    """
    fake = FakeLLM()
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
        config=None,
    ).run(mode="off", action="skip", llm_call=fake)

    assert fake.calls == []
    assert result.nodes == []


def test_a_missing_model_section_is_an_error_when_the_layer_runs(
    scan, tsa
) -> None:
    """Asking for a layer you did not configure is worse than not asking."""
    s = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
        config="detectors:\n  enabled: []\n",
    )
    with pytest.raises(ClassifierConfigError, match="llm_classifier_model"):
        s.run(llm_call=FakeLLM(answer("tool")))


# ============================================================
# mode: auto — only the gaps
# ============================================================

def test_auto_sends_only_the_unclassified_symbols(scan, tsa, caps) -> None:
    """`Agent` has a classifying capability, so it is never sent."""
    fake = FakeLLM(answer("tool", ("reads_env",)))
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(
            ("Agent", "class", "pkg/mod.py", None),
            ("standalone", "method", "pkg/mod.py", None),
        ),
        capabilities=caps({"pkg/mod.py": {"Agent": {"calls_llm"}}}),
    ).run(llm_call=fake)

    assert len(fake.calls) == 1
    assert "def standalone" in fake.calls[0][1]
    assert [(node.name, node.kind) for node in result.nodes] == [
        ("standalone", "tool")
    ]


def test_a_detector_node_means_classified(scan, tsa) -> None:
    """The merge identity `(name, source_file)` is the "already classified" key."""
    detection = DetectionResult(nodes=[
        Node(
            id="tool:detector",
            kind="tool",
            name="standalone",
            host_id="localhost",
            attributes={"source_file": "pkg/mod.py"},
        )
    ])
    fake = FakeLLM()
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
        detection=detection,
    ).run(llm_call=fake)

    assert fake.calls == []
    assert result.nodes == []


def test_a_detector_node_in_another_file_is_not_classified(scan, tsa) -> None:
    """Same name, different file: a different symbol, so it is sent."""
    detection = DetectionResult(nodes=[
        Node(
            id="tool:detector",
            kind="tool",
            name="standalone",
            host_id="localhost",
            attributes={"source_file": "other/mod.py"},
        )
    ])
    fake = FakeLLM(answer("tool"))
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
        detection=detection,
    ).run(llm_call=fake)

    assert len(fake.calls) == 1
    assert [node.name for node in result.nodes] == ["standalone"]


# ============================================================
# mode: required — everything is sent, and deterministic still wins
# ============================================================

def test_required_sends_every_symbol_but_keeps_only_the_gaps(
    scan, tsa, caps
) -> None:
    """ADR-0016: `required` invokes the model for every symbol.

    It still cannot *replace* a deterministic node. `Agent` is classified
    `agent` by its capabilities; the model says `tool`, and the model's
    answer is dropped — not merged, not merged as capabilities, dropped.
    """
    fake = FakeLLM(
        answer("tool", ("reads_env",)),          # Agent — must be dropped
        answer("sink", ("executes_shell",)),     # standalone — kept
    )
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(
            ("Agent", "class", "pkg/mod.py", None),
            ("standalone", "method", "pkg/mod.py", None),
        ),
        capabilities=caps({"pkg/mod.py": {"Agent": {"calls_llm"}}}),
    ).run(mode="required", action="skip", llm_call=fake)

    assert len(fake.calls) == 2
    assert [(node.name, node.kind) for node in result.nodes] == [
        ("standalone", "sink")
    ]


def test_required_fails_when_a_symbol_stays_unclassified(scan, tsa) -> None:
    """The mode is a demand: a gap at the end is an error, not a warning."""
    s = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
    )
    with pytest.raises(ClassifierError, match="left unclassified"):
        s.run(mode="required", llm_call=FakeLLM(answer("unknown")))


def test_required_is_satisfied_when_everything_is_resolved(
    scan, tsa, caps
) -> None:
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(
            ("Agent", "class", "pkg/mod.py", None),
            ("standalone", "method", "pkg/mod.py", None),
        ),
        capabilities=caps({"pkg/mod.py": {"Agent": {"calls_llm"}}}),
    ).run(
        mode="required",
        action="skip",
        llm_call=FakeLLM(answer("tool"), answer("tool")),
    )

    assert [node.name for node in result.nodes] == ["standalone"]


# ============================================================
# What is never sent
# ============================================================

@pytest.mark.parametrize(
    ("name", "kind", "class_name"),
    [
        ("_private", "method", None),
        ("__init__", "method", "Helper"),
        ("create_api_server", "method", None),
        ("BaseAgent", "class", None),
        ("ParseError", "class", None),
        ("InvoiceSchema", "class", None),
    ],
)
def test_a_withheld_name_is_never_sent(
    scan, tsa, name: str, kind: str, class_name: str | None
) -> None:
    """An exclusion withholds a node, so the model must not be asked.

    Sending `ParseError` would invite the model to create the node the
    deterministic layer refused — an LLM overruling a deterministic
    decision, which is the one thing a limiter exists to prevent.
    """
    fake = FakeLLM(answer("tool"))
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa((name, kind, "pkg/mod.py", class_name)),
    ).run(mode="required", action="skip", llm_call=fake)

    assert fake.calls == []
    assert result.nodes == []


def test_non_python_files_are_ignored(scan, tsa) -> None:
    fake = FakeLLM(answer("tool"))
    s = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
    )
    s.manifest.files[0].language = "yaml"

    assert s.run(llm_call=fake).nodes == []
    assert fake.calls == []


def test_a_symbol_of_an_unknown_kind_is_ignored(scan, tsa) -> None:
    """TSA's vocabulary is classes and methods; anything else has no node."""
    fake = FakeLLM(answer("tool"))
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("widget", "variable", "pkg/mod.py", None)),
    ).run(llm_call=fake)

    assert fake.calls == []
    assert result.nodes == []


def test_a_symbol_without_a_definition_is_skipped(scan, tsa) -> None:
    """TSA and `ast` can disagree; a symbol with no source has no prompt."""
    fake = FakeLLM(answer("tool"))
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("vanished", "method", "pkg/mod.py", None)),
    ).run(action="skip", llm_call=fake)

    assert fake.calls == []
    assert result.nodes == []


def test_an_unparseable_file_is_skipped(scan, tsa) -> None:
    fake = FakeLLM(answer("tool"))
    result = scan(
        files={"pkg/mod.py": "def broken(:\n"},
        symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
    ).run(action="skip", llm_call=fake)

    assert fake.calls == []
    assert result.nodes == []


# ============================================================
# unclassified.action
# ============================================================

def test_action_ask_warns_with_a_sample_and_a_hint(scan, tsa, caplog) -> None:
    """D.2 does not prompt: "ask" is a warning for a human to read later."""
    with caplog.at_level(logging.WARNING):
        result = scan(
            files={"pkg/mod.py": MODULE},
            symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
        ).run(mode="off", action="ask", llm_call=FakeLLM())

    assert result.nodes == []
    assert "left unclassified" in caplog.text
    assert "standalone (pkg/mod.py)" in caplog.text
    assert "mode to 'auto'" in caplog.text


def test_action_skip_is_quiet_about_the_same_symbols(scan, tsa, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        scan(
            files={"pkg/mod.py": MODULE},
            symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
        ).run(mode="off", action="skip", llm_call=FakeLLM())

    assert caplog.records == []


def test_action_error_fails_the_scan(scan, tsa) -> None:
    """`error` is the demand `mode: required` makes, however the mode is set."""
    with pytest.raises(ClassifierError, match="left unclassified"):
        scan(
            files={"pkg/mod.py": MODULE},
            symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
        ).run(
            mode="auto",
            action="error",
            llm_call=FakeLLM(answer("unknown")),
        )


def test_action_llm_with_mode_off_warns_that_it_needs_a_mode(
    scan, tsa, caplog
) -> None:
    """A contradictory policy is reported, not silently ignored."""
    with caplog.at_level(logging.WARNING):
        scan(
            files={"pkg/mod.py": MODULE},
            symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
        ).run(mode="off", action="llm", llm_call=FakeLLM())

    assert "unclassified.action='llm'" in caplog.text


def test_action_ask_names_a_symbol_the_model_could_not_classify(
    scan, tsa, caplog
) -> None:
    with caplog.at_level(logging.WARNING):
        scan(
            files={"pkg/mod.py": MODULE},
            symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
        ).run(mode="auto", action="ask", llm_call=FakeLLM(answer("unknown")))

    assert "could not classify" in caplog.text


# ============================================================
# Limiters, driven from the deterministic layers
# ============================================================

def test_a_method_of_a_capability_classified_agent_is_suppressed(
    scan, tsa, caps
) -> None:
    """Limiter 2, with `parent_kind` coming from `classify_class` itself.

    `Agent` carries `calls_llm`, so the deterministic layer classifies it an
    agent — which is what makes the model's `agent` for `run` redundant
    rather than informative.
    """
    fake = FakeLLM(answer("agent", ("calls_llm",)))
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("run", "method", "pkg/mod.py", "Agent")),
        capabilities=caps({"pkg/mod.py": {"Agent": {"calls_llm"}}}),
    ).run(llm_call=fake)

    assert len(fake.calls) == 1
    assert result.nodes == []


def test_a_method_of_a_detector_classified_agent_is_suppressed(scan, tsa) -> None:
    """Detector output is authoritative for kind, so it sets `parent_kind` too."""
    detection = DetectionResult(nodes=[
        Node(
            id="agent:detector",
            kind="agent",
            name="Agent",
            host_id="localhost",
            attributes={"source_file": "pkg/mod.py"},
        )
    ])
    fake = FakeLLM(answer("agent", ("calls_llm",)))
    result = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("run", "method", "pkg/mod.py", "Agent")),
        detection=detection,
    ).run(llm_call=fake)

    assert result.nodes == []


# ============================================================
# What the prompt gets
# ============================================================

def test_the_prompt_gets_the_source_and_the_imports(scan, tsa) -> None:
    fake = FakeLLM(answer("tool"))
    scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
    ).run(llm_call=fake)

    user = fake.calls[0][1]
    assert "def standalone(value):" in user
    assert "return value" in user
    assert "os" in user and "pkg" in user
    assert "[UNTRUSTED_DATA]" in user


def test_the_symbol_text_excludes_its_decorators(scan, tsa) -> None:
    """As the benchmark sliced it: the model judges the body.

    The decorator travels out of band, as `has_tool_decorator`, and limiter 3
    is its only reader.
    """
    fake = FakeLLM(answer("tool"))
    scan(
        files={"pkg/mod.py": "@mcp.tool\ndef read(path):\n    return path\n"},
        symbols=tsa(("read", "method", "pkg/mod.py", None)),
    ).run(llm_call=fake)

    assert "def read(path):" in fake.calls[0][1]
    assert "@mcp.tool" not in fake.calls[0][1]


def test_a_reused_method_name_matches_the_right_definition(scan, tsa) -> None:
    """Two classes, one method name: the nearest line wins."""
    source = (
        "class First:\n"
        "    def adjust(self):\n"
        "        return 1\n"
        "\n"
        "\n"
        "class Second:\n"
        "    def adjust(self):\n"
        "        return 2\n"
    )
    fake = FakeLLM(answer("tool"))
    scan(
        files={"pkg/mod.py": source},
        symbols=tsa(("adjust", "method", "pkg/mod.py", "Second", 8)),
    ).run(llm_call=fake)

    assert "return 2" in fake.calls[0][1]
    assert "return 1" not in fake.calls[0][1]


# ============================================================
# Limiter 3, end to end
# ============================================================

TOOLBOX = '''\
class Toolbox:
    def read(self, path):
        return path
'''


def test_a_tool_without_a_decorator_in_a_server_is_marked(scan, tsa) -> None:
    """The model says both halves; the second one needs a declaration.

    Classes are processed before methods precisely so this works: the
    `mcp_server` parent is a node *this layer* created moments earlier, and
    limiter 3 reads its kind from it.
    """
    fake = FakeLLM(answer("mcp_server"), answer("tool"))
    result = scan(
        files={"pkg/mod.py": TOOLBOX},
        symbols=tsa(
            ("Toolbox", "class", "pkg/mod.py", None),
            ("read", "method", "pkg/mod.py", "Toolbox"),
        ),
    ).run(action="skip", llm_call=fake)

    nodes = {node.name: node for node in result.nodes}
    assert nodes["Toolbox"].kind == "mcp_server"
    assert nodes["read"].attributes["unverified_tool"] is True


def test_a_declared_tool_in_a_server_is_not_marked(scan, tsa) -> None:
    """`@mcp.tool` is in the code, so nothing is unverified about it."""
    fake = FakeLLM(answer("mcp_server"), answer("tool"))
    result = scan(
        files={"pkg/mod.py":
               "class Toolbox:\n"
               "    @mcp.tool\n"
               "    def read(self, path):\n"
               "        return path\n"},
        symbols=tsa(
            ("Toolbox", "class", "pkg/mod.py", None),
            ("read", "method", "pkg/mod.py", "Toolbox"),
        ),
    ).run(action="skip", llm_call=fake)

    nodes = {node.name: node for node in result.nodes}
    assert "unverified_tool" not in nodes["read"].attributes


# ============================================================
# The cache, at the runner's level
# ============================================================

def test_the_cache_is_written_where_the_runner_was_told(scan, tsa) -> None:
    s = scan(
        files={"pkg/mod.py": MODULE},
        symbols=tsa(("standalone", "method", "pkg/mod.py", None)),
    )
    s.run(action="skip", llm_call=FakeLLM(answer("tool")))

    assert s.cache_path.exists()
    with ClassifierCache(s.cache_path) as cache:
        assert cache.count() == 1


def test_a_second_run_is_served_from_the_cache(scan, tsa) -> None:
    """Same corpus, same answer, no second call — the determinism guarantee."""
    inputs = {
        "files": {"pkg/mod.py": MODULE},
        "symbols": tsa(("standalone", "method", "pkg/mod.py", None)),
    }
    first = scan(**inputs).run(
        action="skip", llm_call=FakeLLM(answer("tool", ("reads_env",)))
    )

    second_fake = FakeLLM(answer("sink"))
    second = scan(**inputs).run(action="skip", llm_call=second_fake)

    assert first.nodes[0].kind == "tool"
    assert second_fake.calls == []
    assert second.nodes[0].kind == "tool"
    assert second.nodes[0].capabilities == ["reads_env"]
