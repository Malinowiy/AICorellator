"""Shared fixtures and helpers for the module-level upload tests.

B.3b-user_upload-core-module gives `writer.find_upload_stores` the branch
that answers "which writes does an upload at module level reach". The
harness builds the shape **a detector would hand it** — an entry-point
node whose `entry_kind` is `"streamlit"` or `"cli"`, which is what
`entry_points.EntryPointStage` records for a Streamlit and for a CLI
entry, and no `handler`, which it records for HTTP and WebSocket entries
only — because no detector produces one yet
(B.3b-user_upload-streamlit is the first that will), plus the real `fs`
family's write sites over real files: the writer reads which function a
write lives in, and that is the AST's answer and not a fixture's.

The upload subsystem is process-global in exactly one place — the
detector registry — so this conftest empties it for every test in the
directory, the way `tests/test_store_upload_core` does.

The helpers are shared with the sibling modules the way
`tests/test_llm_classifier` shares its conftest: `from .conftest import`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from aicorellator.graph.models import Node
from aicorellator.ingestion.discovery import FileEntry, FileManifest
from aicorellator.ingestion.target import Target
from aicorellator.parsing.ast_normalizer import GraphDelta
from aicorellator.parsing.models import TSAOutput
from aicorellator.parsing.normalizer_context import (
    NormalizeContext,
    make_context,
)
from aicorellator.store.context.upload import registry
from aicorellator.store.context.upload.base import UploadSite
from aicorellator.store.context.upload.writer import find_upload_stores
from aicorellator.store.families.fs import FsFamily

#: The module the upload is declared in — the site's file, and the file
#: whose body and top-level functions the writer reads.
MODULE = "app/app.py"

#: A helper module the upload's module calls into.
HELPER = "app/storage.py"

#: A module that neither imports nor is imported by `MODULE`.
UNRELATED = "app/other.py"

LITERAL = "/uploads/report.txt"
TEMP = "/tmp/report.txt"


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


# ------------------------------------------------------------------
# The shapes a module-level site is made of
# ------------------------------------------------------------------

def module_entry(
    node_id: str = "entry_point:1",
    file_path: str = MODULE,
    entry_kind: str = "streamlit",
    path: str = "file_uploader",
) -> Node:
    """A module-level entry-point node, as `EntryPointStage` builds it.

    `entry_kind` — `"streamlit"` or `"cli"` — is the structural marker
    `writer._module_level` branches on, and the reason the site is
    module-level at all (B.3b-user_upload-core-module-followup). The node
    carries **no** `handler`: the stage records that attribute for HTTP
    and WebSocket entries only, and a module-level entry has no function
    to name. The node carries no `line_end` either — a module body has no
    range — which is the other half of the same fact.
    """
    return Node(
        id=node_id, kind="entry_point",
        name=f"{entry_kind.upper()}:{file_path}:{path}", host_id="localhost",
        attributes={
            "source_file": file_path, "line_start": 1,
            "method": entry_kind.upper(), "entry_kind": entry_kind,
            "path": path,
        },
    )


def module_site(
    entry_point: Node | None = None,
    file_path: str = MODULE,
    line: int = 1,
    framework: str = "fake",
) -> UploadSite:
    """One module-level `UploadSite` — what a Streamlit detector reports."""
    return UploadSite(
        entry_point=entry_point or module_entry(file_path=file_path),
        file_path=file_path, line=line, framework=framework,
    )


def store_node(node_id: str, hint: str, family: str = "fs") -> Node:
    """A store node, the way the resolver builds one (§1)."""
    return Node(
        id=node_id, kind="store", name=f"{family}:{hint}",
        host_id="localhost",
        attributes={"family": family, "identity_hint": hint},
    )


def fs_writes(built: Project, files: dict[str, str]) -> list:
    """The real `fs` family's write sites, over the scan's files."""
    writes = []
    for entry in built.ctx.manifest.files:
        module = built.ctx.module_for(entry.path)
        writes.extend(FsFamily().detect_writes(module, entry.path))
    return writes


def run_module(
    project,
    files: dict[str, str],
    *,
    site_file: str = MODULE,
    stores: tuple[str, ...] = (LITERAL,),
    store_family: str = "fs",
    call_graph: dict | None = None,
    modules: bool = True,
    entry: Node | None = None,
    sites: list[UploadSite] | None = None,
    threshold: float | None = None,
):
    """One writer run over a module-level site: `(project, edges)`.

    The delta holds the entry point the site names and one store node per
    hint — the two things the resolver would have put there — and the
    write sites come from the real `fs` family reading the real files.

    `sites=None` builds the one module-level site the run is about;
    passing `sites` runs the writer over those instead (the two-site and
    function-level cases). `call_graph` defaults to no calls at all, so a
    fixture that wants a write to be reachable says so itself: which
    functions a call reaches is the call graph's answer and not the
    writer's, and the writer only reads it.
    """
    built = project(files=files)
    entry = entry or module_entry(file_path=site_file)
    built.delta.nodes.append(entry)
    built.delta.nodes.extend(
        store_node(f"store:{index}", hint, store_family)
        for index, hint in enumerate(stores)
    )
    if sites is None:
        sites = [module_site(entry_point=entry, file_path=site_file)]
    modules_by_path = None
    if modules:
        modules_by_path = {
            path: built.ctx.module_for(path) for path in files
        }
    kwargs = {}
    if threshold is not None:
        kwargs["confidence_threshold"] = threshold
    edges = find_upload_stores(
        delta=built.delta,
        upload_sites=sites,
        call_graph=call_graph if call_graph is not None else {},
        fs_writes=fs_writes(built, files),
        store_nodes=[n for n in built.delta.nodes if n.kind == "store"],
        modules=modules_by_path,
        **kwargs,
    )
    return built, edges
