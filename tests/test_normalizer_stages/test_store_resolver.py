"""StoreResolverStage — the seam a family plugs into (B.3b).

The stage is the pipeline's half of ADR-0021 §12: the resolver owns the
work, and this is where the pipeline hands it the delta and takes the
edges back. Four things are pinned here:

  - with the registry empty the stage is a no-op — the delta comes back
    as the same object with the same lists;
  - with a family it produces the store node and the two edges (the
    same shapes `tests/test_store_core/test_resolver.py` pins on the
    resolver itself);
  - with the real `fs` family (B.3b-fs) it produces them from a file
    that names a store, which is the whole point of the seam;
  - the registry is read when the stage *runs*, not when it is built —
    `normalizer_stages.STAGES` is a module-level tuple built at import,
    so a family registered after that must still be seen.

The registry is emptied for every test in this file (see the fixture
below), because the tree now ships `fs` and these tests are about what
the *stage* does with whatever it finds. That the real import registers
`fs` is pinned in `tests/test_store_families/test_fs.py`, and the
pipeline-level negative and positive are in `tests/test_pipeline.py`.
"""

from __future__ import annotations

import ast

import pytest

from aicorellator.graph.models import Node
from aicorellator.parsing.normalizer_stages import STAGES, StoreResolverStage
from aicorellator.parsing.normalizer_stages.base import NormalizerStage
from aicorellator.store import register_family, registry
from aicorellator.store.families.base import Family, ReadSite, WriteSite
from aicorellator.store.families.fs import FsFamily
from aicorellator.store.resolver import StoreResolver

HANDLER = "app/handler.py"

# B.3b-fs's own shapes: one write and one read of the same literal in
# one module, at lines 2 and 4 of a definition that starts on line 1.
FS_HANDLER = "app/upload.py"
FS_SOURCE = '''\
def handle(payload):
    with open("/uploads/report.txt", "wb") as fh:
        fh.write(payload)
    with open("/uploads/report.txt", "rb") as fh:
        return fh.read()
'''
FS_WRITE_LINE = 2
FS_READ_LINE = 4



@pytest.fixture(autouse=True)
def empty_registry(monkeypatch: pytest.MonkeyPatch):
    """The registry is process-global, and these tests register into it.

    Two reasons to empty it: the fake family registered by one test would
    otherwise be visible to the next, and the tree now ships `fs`
    (B.3b-fs) while the tests below assert what the stage does with an
    empty one. A test that wants a family registers it explicitly.
    """
    monkeypatch.setattr(registry, "FAMILIES", {})


class FakeFamily(Family):
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def detect_writes(self, tree, file_path, *, context=None):
        self.calls.append(("writes", file_path))
        return [WriteSite(
            call_site=_call(), file_path=file_path, line=2,
            identity_hint="/uploads",
        )]

    def detect_reads(self, tree, file_path, *, context=None):
        self.calls.append(("reads", file_path))
        return [ReadSite(
            call_site=_call(), file_path=file_path, line=3,
            identity_hint="/uploads", source_type="user_upload",
        )]


def _call() -> ast.Call:
    node = ast.parse("storage.write('/uploads', data)").body[0]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    return node.value


def _fs_delta(project):
    """A project the real `fs` family recognizes, with its two nodes.

    One write and one read of the same literal in one module: the store
    the family will name is `fs:/uploads/report.txt`, the writer is the
    `handle` function the write lives in, and the read is the source the
    graph built for its line.
    """
    built = project(files={FS_HANDLER: FS_SOURCE})
    built.delta.nodes.extend([
        Node(
            id="tool:handle", kind="tool", name="handle", host_id="localhost",
            attributes={"source_file": FS_HANDLER, "line_start": 1},
        ),
        Node(
            id="untrusted_source:1", kind="untrusted_source",
            name="user_upload:open", host_id="localhost",
            attributes={
                "source_file": FS_HANDLER,
                "source_type": "user_upload",
                "line_start": FS_READ_LINE,
            },
        ),
    ])
    return built


def _delta(project):
    """A project with the two nodes a store link needs."""
    handler = '''\
def handle(payload):
    storage.write("/uploads", payload)
    return storage.read("/uploads")
'''
    built = project(files={HANDLER: handler})
    built.delta.nodes.extend([
        Node(
            id="tool:handle", kind="tool", name="handle", host_id="localhost",
            attributes={"source_file": HANDLER, "line_start": 1},
        ),
        Node(
            id="untrusted_source:1", kind="untrusted_source",
            name="user_upload:storage.read", host_id="localhost",
            attributes={
                "source_file": HANDLER,
                "source_type": "user_upload",
                "line_start": 3,
            },
        ),
    ])
    return built


def test_the_stage_is_the_pipelines_stage() -> None:
    stage = next(s for s in STAGES if s.name == "store_resolver")
    assert isinstance(stage, StoreResolverStage)
    assert isinstance(stage, NormalizerStage)


def test_with_no_family_the_delta_is_untouched(project) -> None:
    built = _delta(project)
    before_nodes = list(built.delta.nodes)
    before_edges = list(built.delta.edges)

    stage = StoreResolverStage()
    result = stage.apply(built.delta, built.ctx)

    assert result is built.delta
    assert built.delta.nodes == before_nodes
    assert built.delta.edges == before_edges


def test_a_family_puts_a_store_in_the_delta(project) -> None:
    built = _delta(project)
    stage = StoreResolverStage(StoreResolver([FakeFamily()]))
    stage.apply(built.delta, built.ctx)

    stores = [n for n in built.delta.nodes if n.kind == "store"]
    assert [s.name for s in stores] == ["fake:/uploads"]
    assert [e.kind for e in built.delta.edges] == ["writes_to", "reads_from"]

    writes_to = [e for e in built.delta.edges if e.kind == "writes_to"][0]
    reads_from = [e for e in built.delta.edges if e.kind == "reads_from"][0]
    assert (writes_to.source_id, writes_to.target_id) == (
        "tool:handle", stores[0].id,
    )
    assert (reads_from.source_id, reads_from.target_id) == (
        stores[0].id, "untrusted_source:1",
    )


def test_the_registry_is_read_when_the_stage_runs(project) -> None:
    """`STAGES` is built at import; a family registered later still counts.

    This is why `StoreResolver(families=None)` resolves the registry at
    call time rather than at construction: the stage instances in
    `STAGES` are created before any family module is imported.
    """
    stage = StoreResolverStage()
    built = _delta(project)

    stage.apply(built.delta, built.ctx)
    assert [n for n in built.delta.nodes if n.kind == "store"] == []

    register_family("fake", FakeFamily)
    stage.apply(built.delta, built.ctx)
    assert [n.name for n in built.delta.nodes if n.kind == "store"] == [
        "fake:/uploads"
    ]


def test_the_stage_does_not_duplicate_a_store_node(project) -> None:
    """A store is one node per identity (§1), however often it is asked."""
    stage = StoreResolverStage(StoreResolver([FakeFamily()]))
    built = _delta(project)

    stage.apply(built.delta, built.ctx)
    stage.apply(built.delta, built.ctx)

    assert len([n for n in built.delta.nodes if n.kind == "store"]) == 1


def test_the_registry_is_still_empty_for_the_next_scan() -> None:
    """The stage registers nothing; only the family package does.

    The registry is empty here because the fixture emptied it — what
    this pins is that running the stage does not put anything in it, so
    a scan cannot change what the next one sees.
    """
    assert registry.FAMILIES == {}
    assert registry.all_names() == []


def test_the_real_family_puts_a_store_in_the_delta(project) -> None:
    """B.3b-fs: register `fs` and the stage emits its edges.

    The family is registered explicitly because this file's fixture
    empties the registry (the tests above assert what an empty one
    does). Everything else is the real thing: the real family reading a
    real file, and the stage appending the edges the resolver returned.
    """
    register_family("fs", FsFamily)
    stage = StoreResolverStage()
    built = _fs_delta(project)

    stage.apply(built.delta, built.ctx)

    stores = [n for n in built.delta.nodes if n.kind == "store"]
    assert [s.name for s in stores] == ["fs:/uploads/report.txt"]
    assert [s.attributes["family"] for s in stores] == ["fs"]

    assert [e.kind for e in built.delta.edges] == [
        "writes_to", "reads_from",
    ]
    writes_to = built.delta.edges[0]
    reads_from = built.delta.edges[1]
    assert (writes_to.source_id, writes_to.target_id) == (
        "tool:handle", stores[0].id,
    )
    assert (reads_from.source_id, reads_from.target_id) == (
        stores[0].id, "untrusted_source:1",
    )
    # Equal literals and the same module: 1 − 0.7 × 0.4 × 0.8.
    assert [round(e.identity_confidence, 4) for e in built.delta.edges] == [
        0.776, 0.776,
    ]


def test_a_target_that_names_no_store_gets_no_store(project) -> None:
    """The negative the family makes true: no literal, no store.

    `storage.write` is not a call shape `fs` recognizes, so the project
    the fake family was written for produces nothing with the real one —
    which is what "a family decides what it sees" means in practice.
    """
    register_family("fs", FsFamily)
    stage = StoreResolverStage()
    built = _delta(project)

    stage.apply(built.delta, built.ctx)

    assert [n for n in built.delta.nodes if n.kind == "store"] == []
    assert built.delta.edges == []

