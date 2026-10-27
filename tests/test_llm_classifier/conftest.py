"""Shared builders for the Layer 1b tests (D.2).

Three things every test in this package needs, and none of them should
build by hand:

  - `answer(...)` — a well-formed assistant message. The classifier's input
    is JSON text, so the tests speak JSON text.
  - `FakeLLM` — a scripted `llm_call`. Responses come back in call order,
    which is the order the classifier asks in, so no test matches on the
    prompt's contents (a test that did would break every time the prompt is
    reworded, without the classifier changing at all).
  - `scan(...)` / `Scan.run(...)` — a whole runner input set: files on disk
    (the runner parses them), TSA symbols, capabilities, detector output, a
    config file and a cache path, plus a `run()` that fills in a policy.

No test in this package touches the network: `llm_call` is always injected,
and the one test that exercises the HTTP path monkeypatches `requests.post`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from aicorellator.ingestion.discovery import FileEntry, FileManifest
from aicorellator.ingestion.target import Target
from aicorellator.parsing.capability_extractor import (
    FileCapabilities,
    SymbolCapabilities,
)
from aicorellator.parsing.framework_detectors import DetectionResult
from aicorellator.parsing.llm_classifier import (
    CACHE_FILENAME,
    Classifier,
    ClassifierCache,
    ClassifierConfig,
    UnclassifiedSymbol,
    run_classifier,
)
from aicorellator.parsing.models import TSASymbol, TSAOutput
from aicorellator.scan_config import default_scan_config


CONFIG_YAML = """\
llm_classifier_model:
  base_url: "http://localhost:1234/v1"
  model: "test/model"
  temperature: 0.0
  max_tokens: 800
  timeout: 120
"""


def answer(kind: str, capabilities: tuple[str, ...] = ()) -> str:
    """A well-formed assistant message for one classification."""
    return json.dumps({
        "kind": kind,
        "capabilities": list(capabilities),
        "reasoning": f"the body says {kind}",
    })


class FakeLLM:
    """A scripted `llm_call`, consumed in call order.

    `None` entries stand for "no answer" (a timeout, a refused format), and
    an `Exception` entry is raised, so the failure paths are as easy to
    script as the happy one.
    """

    def __init__(self, *responses: str | None | Exception) -> None:
        self.responses: list[str | None | Exception] = list(responses)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system: str, user: str) -> str | None:
        self.calls.append((system, user))
        if not self.responses:
            return None
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def symbol(
    name: str,
    kind: str = "method",
    *,
    file_path: str = "pkg/mod.py",
    class_name: str | None = None,
    source: str | None = None,
    imports: list[str] | None = None,
    has_tool_decorator: bool = False,
    parent_kind: str | None = None,
    line_start: int = 1,
) -> UnclassifiedSymbol:
    """An `UnclassifiedSymbol` with everything but the name defaulted."""
    return UnclassifiedSymbol(
        name=name,
        kind=kind,
        file_path=file_path,
        line_start=line_start,
        source=(
            source
            if source is not None
            else f"def {name}(self):\n    return None\n"
        ),
        imports=list(imports or []),
        class_name=class_name,
        has_tool_decorator=has_tool_decorator,
        parent_kind=parent_kind,
    )


@pytest.fixture
def classifier():
    """Build a `Classifier` with a scripted LLM call."""

    def build(
        *responses: str | None | Exception,
        cache: ClassifierCache | None = None,
        model: str = "test/model",
        **kwargs,
    ) -> Classifier:
        return Classifier(
            ClassifierConfig(model=model),
            cache=cache,
            llm_call=FakeLLM(*responses),
            **kwargs,
        )

    return build


@pytest.fixture
def memory_cache():
    """A cache in memory, closed after the test."""
    cache = ClassifierCache(":memory:")
    yield cache
    cache.close()


@pytest.fixture
def caps():
    """Build a map: `caps({"pkg/mod.py": {"Agent": {"calls_llm"}}})`."""

    def build(
        mapping: dict[str, dict[str, set[str]]],
    ) -> dict[str, FileCapabilities]:
        out: dict[str, FileCapabilities] = {}
        for file_path, symbols in mapping.items():
            out[file_path] = FileCapabilities(
                file_path=file_path,
                symbols=[
                    SymbolCapabilities(
                        symbol_name=name,
                        file_path=file_path,
                        capabilities=set(capabilities),
                    )
                    for name, capabilities in symbols.items()
                ],
            )
        return out

    return build


@pytest.fixture
def tsa():
    """Build a `TSAOutput` from `(name, kind, file, class_name[, line])`."""

    def build(*rows, **kwargs) -> TSAOutput:
        symbols = [
            TSASymbol(
                name=name,
                kind=kind,
                file_path=file_path,
                line_start=line_start,
                line_end=line_start,
                class_name=class_name,
            )
            for row in rows
            for name, kind, file_path, class_name, line_start in (
                row if len(row) == 5 else (*row, 1),
            )
        ]
        return TSAOutput(symbols=symbols, **kwargs)

    return build


@dataclass
class Scan:
    """A runner input set, and a `run()` that supplies a policy."""

    manifest: FileManifest
    tsa: TSAOutput
    capabilities: dict[str, FileCapabilities]
    detection: DetectionResult
    config_path: Path
    cache_path: Path

    def run(
        self,
        *,
        mode: str = "auto",
        action: str = "ask",
        llm_call=None,
        **kwargs,
    ):
        """Run the classifier with a policy built from `mode`/`action`."""
        policy = default_scan_config()
        policy.llm_classifier.mode = mode
        policy.unclassified.action = action
        return run_classifier(
            manifest=self.manifest,
            tsa_output=self.tsa,
            capabilities=self.capabilities,
            framework_detection=self.detection,
            scan=policy,
            config_path=self.config_path,
            cache_path=self.cache_path,
            llm_call=llm_call,
            **kwargs,
        )


@pytest.fixture
def scan(tmp_path: Path):
    """Build a `Scan`: files on disk, TSA symbols, caps, detectors, config."""

    def build(
        files: dict[str, str] | None = None,
        symbols: TSAOutput | None = None,
        capabilities: dict[str, FileCapabilities] | None = None,
        detection: DetectionResult | None = None,
        config: str | None = CONFIG_YAML,
        cache: Path | None = None,
    ) -> Scan:
        root = tmp_path / "target"
        # `Target` rejects a root that does not exist, and this fixture may
        # be asked for no files at all.
        root.mkdir(parents=True, exist_ok=True)
        manifest = FileManifest(target=Target(root=root, kind="folder"))
        for index, (path, source) in enumerate((files or {}).items()):
            abs_path = root / path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(source, encoding="utf-8")
            manifest.files.append(FileEntry(
                path=path,
                abs_path=abs_path,
                language="python",
                content_hash=f"h{index}",
                size=len(source),
            ))

        config_path = tmp_path / "config.yaml"
        if config is not None:
            config_path.write_text(config, encoding="utf-8")

        return Scan(
            manifest=manifest,
            tsa=symbols or TSAOutput(),
            capabilities=capabilities or {},
            detection=detection or DetectionResult(),
            config_path=config_path,
            cache_path=cache or (tmp_path / "cache" / CACHE_FILENAME),
        )

    return build
