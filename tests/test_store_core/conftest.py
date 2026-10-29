"""Shared fixtures for the store-core tests (B.3b-core).

The store subsystem is process-global in exactly one place — the family
registry — so this conftest empties it for every test in the directory.
A test that registers a fake family would otherwise leak it into the
pipeline test next door, which asserts what an empty registry does.
Since B.3b-fs the tree ships `fs`, so the empty registry is the fixture's
doing and not the tree's; a test that wants the real family registers it
explicitly.

`scan` builds a context over a synthetic target: files written to
`tmp_path` (the resolver parses them for the enclosing definitions and
the code-context signal), a manifest over them, and an empty delta to
fill.
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
from aicorellator.store import registry


@pytest.fixture(autouse=True)
def empty_registry(monkeypatch: pytest.MonkeyPatch):
    """An empty family registry, restored after the test."""
    monkeypatch.setattr(registry, "FAMILIES", {})


@dataclass
class Scan:
    """A context and the delta a resolver will fill."""

    ctx: NormalizeContext
    delta: GraphDelta = field(default_factory=GraphDelta)

    def nodes(self, kind: str) -> list:
        return [n for n in self.delta.nodes if n.kind == kind]

    def edges(self, kind: str) -> list:
        return [e for e in self.delta.edges if e.kind == kind]

    def node(self, node_id: str):
        return next(n for n in self.delta.nodes if n.id == node_id)


@pytest.fixture
def scan(tmp_path: Path):
    """Build a `Scan` whose files exist on disk."""

    def build(files: dict[str, str] | None = None, **kwargs) -> Scan:
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
        ctx = make_context(manifest, TSAOutput(symbols=[]), **kwargs)
        return Scan(ctx=ctx)

    return build
