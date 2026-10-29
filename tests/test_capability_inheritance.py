"""Tests for capability inheritance from a scanned base class (B.10b).

ADR-0020: `caps(cls) = caps(cls's own body) ∪ caps(cls's direct base)`,
one level, single base, resolved against the classes the scan parsed.
`extract` is unchanged — it still produces each file's own-body
capabilities — and `apply_inheritance` is the pass over the whole
manifest that adds the base's, because the base may live in another
file.

Every fixture writes real files, discovers them and runs `extract`, so
the input to `apply_inheritance` is the shape the pipeline hands it and
the assertions are on what a scan would see. Each test names the ADR
section it pins.
"""

from __future__ import annotations

from pathlib import Path

from aicorellator.ingestion import discover, ingest
from aicorellator.parsing import (
    apply_inheritance,
    classify_class,
    make_context,
    normalize,
)
from aicorellator.parsing.capability_extractor import (
    FileCapabilities,
    extract,
)
from aicorellator.parsing.models import TSASymbol, TSAOutput


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------

def _write(tmp_path: Path, name: str, code: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return path


def _extract_all(
    tmp_path: Path, files: dict[str, str]
) -> tuple[dict[str, FileCapabilities], object]:
    """Write `files`, discover them, and run `extract` per file.

    This is the pipeline's shape: one `FileCapabilities` per manifest
    file, keyed by the manifest's relative path.
    """
    for name, code in files.items():
        _write(tmp_path, name, code)
    manifest = discover(ingest(tmp_path, kind="folder"))
    capabilities = {
        entry.path: extract(entry.abs_path, entry.path)
        for entry in manifest.files
        if entry.language == "python"
    }
    return capabilities, manifest


def _apply(
    tmp_path: Path, files: dict[str, str]
) -> dict[str, FileCapabilities]:
    """`extract` per file, then `apply_inheritance` over the manifest."""
    capabilities, manifest = _extract_all(tmp_path, files)
    return apply_inheritance(capabilities, manifest)


def _caps(
    result: dict[str, FileCapabilities], file_path: str, class_name: str
) -> set[str]:
    """The capabilities on a class's entry after inheritance."""
    for symbol in result[file_path].symbols:
        if symbol.symbol_name == class_name:
            return set(symbol.capabilities)
    raise AssertionError(f"no entry for {class_name} in {file_path}")


# ------------------------------------------------------------
# §1 — what inherits
# ------------------------------------------------------------

def test_a_subclass_inherits_its_bases_own_capabilities(
    tmp_path: Path,
) -> None:
    """The FinBot shape: the base calls the LLM, the subclass does not.

    `InvoiceAgent`'s body only calls the base's method, so before B.10b
    it had no capability, was classified as nothing, and disappeared
    from the graph. ADR-0020 §1 gives it the base's own-body set.
    """
    result = _apply(tmp_path, {"agents/base.py": '''
class BaseAgent:
    def __init__(self, llm_client):
        self.llm_client = llm_client

    def _run_agent_loop(self, messages):
        return self.llm_client.chat(messages)


class InvoiceAgent(BaseAgent):
    def process(self, task):
        return self._run_agent_loop(task)
'''})
    assert _caps(result, "agents/base.py", "BaseAgent") == {"calls_llm"}
    assert _caps(result, "agents/base.py", "InvoiceAgent") == {"calls_llm"}


def test_the_subclass_is_classified_as_an_agent(tmp_path: Path) -> None:
    """The point of the credit: the subclass becomes a node again.

    `classify_class` reads `caps & AGENT_CAPABILITIES`, which contains
    `calls_llm`, so the inherited capability alone makes the subclass an
    `agent` — the same answer
    `test_classification.py::test_llm_capability_alone_is_an_agent` pins
    for a class that calls the LLM in its own body.
    """
    result = _apply(tmp_path, {"agents/invoice.py": '''
class BaseAgent:
    def _run_agent_loop(self, messages):
        return self.llm_client.chat(messages)


class InvoiceAgent(BaseAgent):
    def process(self, task):
        return self._run_agent_loop(task)
'''})
    caps = _caps(result, "agents/invoice.py", "InvoiceAgent")
    classified = classify_class(
        "InvoiceAgent", "agents/invoice.py", sorted(caps)
    )
    assert classified is not None
    assert classified.kind == "agent"


def test_a_wrapped_client_subclass_inherits_too(tmp_path: Path) -> None:
    """The base is a client, not an agent: the rule is not agent-specific.

    ADR-0020 inherits *capabilities*, whatever they are; nothing in the
    rule reads the base's name or the subclass's.
    """
    result = _apply(tmp_path, {"llm/client.py": '''
class BaseLLMClient:
    def ask(self, prompt):
        return self.model_client.invoke(prompt)


class ChatClient(BaseLLMClient):
    def send(self, prompt):
        return self.ask(prompt)
'''})
    assert _caps(result, "llm/client.py", "BaseLLMClient") == {"calls_llm"}
    assert _caps(result, "llm/client.py", "ChatClient") == {"calls_llm"}


def test_a_base_in_another_file_is_resolved(tmp_path: Path) -> None:
    """§1 — the cross-file case, which is why the pass reads the manifest.

    The base is in one file, the subclass in a second, and a third file
    is unrelated. The pass parses the manifest's files to read
    `ClassDef.bases`, because a `TSASymbol` does not carry the base list.
    """
    result = _apply(tmp_path, {
        "agents/base.py": '''
class BaseAgent:
    def _run_agent_loop(self, messages):
        return self.llm_client.chat(messages)
''',
        "agents/invoice.py": '''
class InvoiceAgent(BaseAgent):
    def process(self, task):
        return self._run_agent_loop(task)
''',
        "unrelated.py": '''
class Other:
    def go(self):
        open("/tmp/x")
''',
    })
    assert _caps(result, "agents/invoice.py", "InvoiceAgent") == {
        "calls_llm"
    }
    assert _caps(result, "unrelated.py", "Other") == {"reads_filesystem"}


def test_a_subclass_that_has_the_capability_credits_it_once(
    tmp_path: Path,
) -> None:
    """§1 — the union is a set: a capability is credited once.

    Both bodies call the LLM. The class entry stays one entry with one
    `calls_llm`, not two — the union cannot duplicate, and no second
    symbol entry is appended for the inherited contribution.
    """
    result = _apply(tmp_path, {"a.py": '''
class BaseAgent:
    def _run(self, messages):
        return self.llm_client.chat(messages)


class InvoiceAgent(BaseAgent):
    def process(self, task):
        return self.llm_client.invoke(task)
'''})
    assert _caps(result, "a.py", "InvoiceAgent") == {"calls_llm"}
    entries = [
        s for s in result["a.py"].symbols
        if s.symbol_name == "InvoiceAgent"
    ]
    assert len(entries) == 1


def test_the_subclasss_own_capabilities_are_kept(tmp_path: Path) -> None:
    """§1 — the union, not the replacement.

    The subclass runs a shell command of its own and inherits the
    base's LLM call; both capabilities land on the class entry.
    """
    result = _apply(tmp_path, {"a.py": '''
import subprocess


class BaseAgent:
    def _run(self, messages):
        return self.llm_client.chat(messages)


class ToolAgent(BaseAgent):
    def shell(self, cmd):
        subprocess.run(cmd, shell=True)
'''})
    assert _caps(result, "a.py", "ToolAgent") == {
        "calls_llm",
        "executes_code",
        "executes_shell",
    }


def test_an_override_does_not_block_inheritance(tmp_path: Path) -> None:
    """§3 — the override is accepted as a false positive on *kind*.

    The AST sees that `_run_agent_loop` is overridden; it cannot see
    what the override calls. Refusing inheritance would turn the common
    shape into a false negative on *enumeration* — the node would leave
    the graph — so the override does not block (ADR-0020 §3).
    """
    result = _apply(tmp_path, {"a.py": '''
class BaseAgent:
    def _run_agent_loop(self, messages):
        return self.llm_client.chat(messages)


class InvoiceAgent(BaseAgent):
    def _run_agent_loop(self, messages):
        return messages
'''})
    assert _caps(result, "a.py", "InvoiceAgent") == {"calls_llm"}


# ------------------------------------------------------------
# §2, §4 — what does not inherit
# ------------------------------------------------------------

def test_a_base_outside_the_scanned_tree_inherits_nothing(
    tmp_path: Path,
) -> None:
    """§2 — the base is a `ClassDef` the scan produced, or it is nothing.

    A qualified base is an `ast.Attribute`, a name the scan never
    defined resolves to nothing, and a string base is a forward
    reference the ADR does not resolve. All three inherit nothing: there
    is no import, no suffix match and no name table (ADR-0016, decision
    7), so `langchain.agents.AgentExecutor` cannot credit anything.
    """
    result = _apply(tmp_path, {"a.py": '''
import langchain.agents


class Qualified(langchain.agents.AgentExecutor):
    def go(self):
        open("/tmp/x")


class Unresolved(Missing):
    def go(self):
        open("/tmp/x")


class StringBase("BaseAgent"):
    def go(self):
        open("/tmp/x")
'''})
    for name in ("Qualified", "Unresolved", "StringBase"):
        assert _caps(result, "a.py", name) == {"reads_filesystem"}, name


def test_a_class_without_a_base_keeps_its_own_body(tmp_path: Path) -> None:
    """§2 — no base, nothing to inherit: the entry is `extract`'s."""
    result = _apply(tmp_path, {"a.py": '''
class Plain:
    def go(self):
        open("/tmp/x")
'''})
    assert _caps(result, "a.py", "Plain") == {"reads_filesystem"}


def test_a_class_that_names_itself_inherits_nothing(tmp_path: Path) -> None:
    """§1 — the base has to be a *different* class.

    `class Selfish(Selfish)` names itself; resolving it would union a
    class's own capabilities into themselves, which credits nothing new
    and is not inheritance.
    """
    result = _apply(tmp_path, {"a.py": '''
class Selfish(Selfish):
    def go(self):
        open("/tmp/x")
'''})
    assert _caps(result, "a.py", "Selfish") == {"reads_filesystem"}


def test_inheritance_is_one_level(tmp_path: Path) -> None:
    """§4 — a grandparent's capabilities do not reach a grandchild.

    `C` carries the call, `B` inherits it, and `A(B)` does *not* see it:
    `inherited_caps` is the base's own body, so the pass never walks a
    chain.
    """
    result = _apply(tmp_path, {"a.py": '''
class C:
    def go(self):
        return self.llm.chat("x")


class B(C):
    pass


class A(B):
    pass
'''})
    assert _caps(result, "a.py", "C") == {"calls_llm"}
    assert _caps(result, "a.py", "B") == {"calls_llm"}
    assert _caps(result, "a.py", "A") == set()


def test_multiple_bases_inherit_from_neither(tmp_path: Path) -> None:
    """§4 — two bases inherit from neither; no "first base", no union.

    The ADR declines to decide what "the base" means when there are two,
    so the pass declines to guess: neither base's capability is credited,
    even when both are in the scanned tree.
    """
    result = _apply(tmp_path, {"a.py": '''
class LlmMixin:
    def go(self):
        return self.llm.chat("x")


class FileMixin:
    def go(self):
        open("/tmp/x")


class Both(LlmMixin, FileMixin):
    pass
'''})
    assert _caps(result, "a.py", "Both") == set()
    assert _caps(result, "a.py", "LlmMixin") == {"calls_llm"}


def test_a_declared_capability_is_not_inherited(tmp_path: Path) -> None:
    """§2 — a base that says it has a capability but does not call it.

    ADR-0009 is call-based and ADR-0020 does not widen it: the docstring
    and the class attribute credit nothing, so the subclass inherits
    nothing.
    """
    result = _apply(tmp_path, {"a.py": '''
class BaseAgent:
    """calls_llm"""

    capability = "calls_llm"

    def _run(self, messages):
        return messages


class InvoiceAgent(BaseAgent):
    pass
'''})
    assert _caps(result, "a.py", "BaseAgent") == set()
    assert _caps(result, "a.py", "InvoiceAgent") == set()


def test_a_base_defined_in_two_files_inherits_nothing(
    tmp_path: Path,
) -> None:
    """§2 — two classes with the base's name is ambiguity, not a match.

    The ADR resolves a name against the classes the scan parsed; two
    files defining `BaseAgent` are two unrelated classes, and picking
    one would be a guess. Same file first (tested above), a unique
    cross-file definition next, and nothing for the rest.
    """
    result = _apply(tmp_path, {
        "one/base.py": '''
class BaseAgent:
    def go(self):
        return self.llm.chat("x")
''',
        "two/base.py": '''
class BaseAgent:
    def go(self):
        open("/tmp/x")
''',
        "app.py": '''
class InvoiceAgent(BaseAgent):
    pass
''',
    })
    assert _caps(result, "app.py", "InvoiceAgent") == set()


# ------------------------------------------------------------
# §5 — cycle safety, determinism, purity
# ------------------------------------------------------------

def test_a_cycle_terminates_and_credits_each_class_once(
    tmp_path: Path,
) -> None:
    """§5 — `A(B)` and `B(A)`: terminate, one credit each.

    The pass reads every inherited set from the *input* capabilities, so
    a cycle is two independent one-level unions rather than a walk that
    could loop. Each class ends with both capabilities, once each — the
    union cannot grow a set, which is what "credited at most once"
    means here.
    """
    result = _apply(tmp_path, {"a.py": '''
class A(B):
    def go(self):
        return self.llm.chat("x")


class B(A):
    def go(self):
        open("/tmp/x")
'''})
    assert _caps(result, "a.py", "A") == {"calls_llm", "reads_filesystem"}
    assert _caps(result, "a.py", "B") == {"calls_llm", "reads_filesystem"}


def test_two_runs_agree_and_the_input_is_not_mutated(
    tmp_path: Path,
) -> None:
    """§5 determinism, and the pass is pure.

    The same input twice yields equal output, and the input dict and its
    entries are untouched — `apply_inheritance` returns a new dict, so a
    caller can keep the per-file result and hand the extended one on.
    """
    capabilities, manifest = _extract_all(tmp_path, {"a.py": '''
class BaseAgent:
    def _run(self, messages):
        return self.llm_client.chat(messages)


class InvoiceAgent(BaseAgent):
    pass
'''})
    before = {
        path: [(s.symbol_name, sorted(s.capabilities))
               for s in fc.symbols]
        for path, fc in capabilities.items()
    }

    first = apply_inheritance(capabilities, manifest)
    second = apply_inheritance(capabilities, manifest)

    assert first == second
    assert {
        path: [(s.symbol_name, sorted(s.capabilities))
               for s in fc.symbols]
        for path, fc in capabilities.items()
    } == before
    assert _caps(first, "a.py", "InvoiceAgent") == {"calls_llm"}
    assert _caps(capabilities, "a.py", "InvoiceAgent") == set()


def test_methods_are_never_touched(tmp_path: Path) -> None:
    """§2 — class-level only: the subclass's methods keep their own set.

    A method of the subclass does not inherit the base's method
    capabilities (method-level inheritance is a separate ADR), and
    `extract` emits no entry for a method that credits nothing — so
    `process`, which only calls the base, is not in the file's symbols
    after the pass either. The class entry gains `calls_llm`.
    """
    result = _apply(tmp_path, {"a.py": '''
class BaseAgent:
    def _run_agent_loop(self, messages):
        return self.llm_client.chat(messages)


class InvoiceAgent(BaseAgent):
    def process(self, task):
        return self._run_agent_loop(task)
'''})
    names = [s.symbol_name for s in result["a.py"].symbols]
    assert _caps(result, "a.py", "InvoiceAgent") == {"calls_llm"}
    assert "process" not in names
    assert _caps(result, "a.py", "_run_agent_loop") == {"calls_llm"}


def test_an_unparseable_base_file_credits_nothing(tmp_path: Path) -> None:
    """§5 / open question 4 — the base the scan read but could not parse.

    `BaseAgent` is a name the subclass spells out, but the file it came
    from has a syntax error, so `extract` produced no entry for it. The
    base credits nothing and the subclass inherits nothing — the same
    answer as an external base, because the rule resolves against what
    the scan produced.
    """
    result = _apply(tmp_path, {
        "agents/base.py": "class BaseAgent(:\n",
        "agents/invoice.py": '''
class InvoiceAgent(BaseAgent):
    def process(self, task):
        return task
''',
    })
    assert result["agents/base.py"].symbols == []
    assert _caps(result, "agents/invoice.py", "InvoiceAgent") == set()


# ------------------------------------------------------------
# End to end — the subclass reaches the graph
# ------------------------------------------------------------

def test_the_subclass_becomes_an_agent_node(tmp_path: Path) -> None:
    """B.10b's purpose, through the normalizer: the node is back.

    `ClassNodeStage` asks `classify_class` for each class symbol and
    keeps the ones that answer. `InvoiceAgent` answers because
    `apply_inheritance` put its base's `calls_llm` on its entry before
    `normalize` ran; without the pass `InvoiceAgent` is not in the delta
    at all — the disappearance ADR-0020 was written to fix.

    The base itself is *not* a node: `BaseAgent` is in
    `patterns.EXCLUDED_CLASSES`, and an exclusion withholds a node, it
    never grants one (ADR-0016, decision 7). So the base contributes
    capabilities without appearing, which is the case the class-name
    rules used to cover and no longer do.

    TSA output is hand-built rather than run (`run_tsa` shells out to
    tree-sitter-analyzer); the base list the normalizer does not read is
    the pass's input, and the pass has already read it from the AST.
    """
    capabilities, manifest = _extract_all(tmp_path, {
        "agents/base.py": '''
class BaseAgent:
    def _run_agent_loop(self, messages):
        return self.llm_client.chat(messages)


class InvoiceAgent(BaseAgent):
    def process(self, task):
        return self._run_agent_loop(task)
''',
    })
    tsa = TSAOutput(
        symbols=[
            TSASymbol(
                name="BaseAgent", kind="class",
                file_path="agents/base.py",
                line_start=2, line_end=4,
            ),
            TSASymbol(
                name="InvoiceAgent", kind="class",
                file_path="agents/base.py",
                line_start=7, line_end=9,
            ),
        ],
        files_analyzed=1,
    )

    without = normalize(make_context(manifest, tsa, capabilities=capabilities))
    with_pass = normalize(make_context(
        manifest, tsa,
        capabilities=apply_inheritance(capabilities, manifest),
    ))

    assert "InvoiceAgent" not in {n.name for n in without.nodes}
    agents = {
        n.name: n for n in with_pass.nodes if n.kind == "agent"
    }
    assert set(agents) == {"InvoiceAgent"}
    assert "calls_llm" in agents["InvoiceAgent"].capabilities
