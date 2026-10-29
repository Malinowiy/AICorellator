"""Shared fixtures for the upload-core tests (B.3b-user_upload-core).

The upload subsystem is process-global in exactly one place — the
detector registry — so this conftest empties it for every test in the
directory. A test that registers a fake detector would otherwise leak it
into the test next door, and the empty registry is the state the core
step is accepted on. (No detector ships yet, so this is the tree's state
today as well; the fixture is what keeps it true once
B.3b-user_upload-fastapi lands.)

`project` builds a context over a synthetic target: files written to
`tmp_path` (the writer parses them for the enclosing definitions), a
manifest over them, and an empty delta to fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from aicorellator.ingestion.discovery import FileEntry, FileManifest
from aicorellator.ingestion.target import Target
from aicorellator.parsing.ast_normalizer import GraphDelta
from aicorellator.parsing.models import TSAOutput
from aicorellator.parsing.normalizer_context import (
    NormalizeContext,
    make_context,
)
from aicorellator.store.context.upload import registry


@pytest.fixture(autouse=True)
def empty_detector_registry(monkeypatch: pytest.MonkeyPatch):
    """An empty detector registry, restored after the test."""
    monkeypatch.setattr(registry, "UPLOAD_DETECTORS", {})


@dataclass
class Project:
    """A context and the delta a producer will fill."""

    ctx: NormalizeContext
    delta: GraphDelta = field(default_factory=GraphDelta)

    def nodes(self, kind: str) -> list:
        return [n for n in self.delta.nodes if n.kind == kind]

    def edges(self, kind: str) -> list:
        return [e for e in self.delta.edges if e.kind == kind]

    def node(self, node_id: str):
        return next(n for n in self.delta.nodes if n.id == node_id)


@pytest.fixture
def project(tmp_path: Path):
    """Build a `Project` whose files exist on disk."""

    def build(
        files: dict[str, str] | None = None,
        entry_points=None,
        **kwargs,
    ) -> Project:
        target = Target(root=tmp_path, kind="folder")
        manifest = FileManifest(target=target)
        for index, (path, source) in enumerate((files or {}).items()):
            abs_path = tmp_path / path
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
            TSAOutput(symbols=[]),
            entry_points=entry_points,
            **kwargs,
        )
        return Project(ctx=ctx)

    return build
