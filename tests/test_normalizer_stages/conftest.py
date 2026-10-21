"""Shared builders for the per-stage tests (step 4).

A stage takes a `GraphDelta` and a `NormalizeContext`. These fixtures
build both from a handful of keyword arguments — the files the stage may
read (written to `tmp_path`, because two stages parse source), the TSA
symbols, and whichever optional scan output the stage under test reads.

`Project.nodes(kind)` / `Project.edges(kind)` keep the assertions short
and readable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from aicorellator.ingestion.discovery import FileEntry, FileManifest
from aicorellator.ingestion.target import Target
from aicorellator.parsing.ast_normalizer import GraphDelta
from aicorellator.parsing.capability_extractor import FileCapabilities
from aicorellator.parsing.entry_points import EntryPointOutput
from aicorellator.parsing.framework_detectors import DetectionResult
from aicorellator.parsing.llm_extractor import LLMExtractorOutput
from aicorellator.parsing.models import TSASymbol, TSAOutput
from aicorellator.parsing.normalizer_context import (
    NormalizeContext,
    make_context,
)


@dataclass
class Project:
    """A context plus the delta a stage will fill."""

    ctx: NormalizeContext
    delta: GraphDelta = field(default_factory=GraphDelta)

    def nodes(self, kind: str) -> list:
        return [n for n in self.delta.nodes if n.kind == kind]

    def edges(self, kind: str) -> list:
        return [e for e in self.delta.edges if e.kind == kind]

    def named(self, name: str):
        return next(n for n in self.delta.nodes if n.name == name)


@pytest.fixture
def target(tmp_path: Path) -> Target:
    """A target pointing at an empty temp directory."""
    return Target(root=tmp_path, kind="folder")


@pytest.fixture
def project(target: Target):
    """Build a `Project` from a minimal scan input set."""

    def build(
        files: dict[str, str] | None = None,
        symbols: list[TSASymbol] | None = None,
        entry_points: EntryPointOutput | None = None,
        llm_output: LLMExtractorOutput | None = None,
        capabilities: dict[str, FileCapabilities] | None = None,
        detection: DetectionResult | None = None,
        host_id: str = "localhost",
    ) -> Project:
        manifest = FileManifest(target=target)
        for index, (path, source) in enumerate((files or {}).items()):
            abs_path = target.root / path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(source, encoding="utf-8")
            manifest.files.append(FileEntry(
                path=path,
                abs_path=abs_path,
                language="python",
                content_hash=f"h{index}",
                size=len(source),
            ))
        ctx = make_context(
            manifest,
            TSAOutput(symbols=symbols or []),
            entry_points=entry_points,
            llm_output=llm_output,
            capabilities=capabilities,
            framework_detection=detection,
            host_id=host_id,
        )
        return Project(ctx=ctx)

    return build
