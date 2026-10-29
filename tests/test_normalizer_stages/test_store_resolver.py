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

B.3b-user_upload-core adds a second step to the same stage, and the last
two tests here pin what it does at this seam: with no detector
registered it is a no-op on the `fs` fixture, and with one registered for
the test it emits the upload writer's edge and stamps the read site.
"""

from __future__ import annotations

import ast

import pytest

from aicorellator.graph.models import Node
from aicorellator.parsing.entry_points import EntryPointOutput
from aicorellator.parsing.normalizer_stages import STAGES, StoreResolverStage
from aicorellator.parsing.normalizer_stages.base import NormalizerStage
from aicorellator.store import register_family, registry
from aicorellator.store.context.upload import registry as upload_registry
from aicorellator.store.context.upload.base import (
    UploadDetector,
    UploadSite,
)
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


@pytest.fixture(autouse=True)
def empty_detector_registry(monkeypatch: pytest.MonkeyPatch):
    """The *upload* registry, on the same rule (B.3b-user_upload-core).

    The upload step's detectors are process-global too, and the tests
    below say what the stage does with none registered — which is the
    state the core step is accepted on, and the state the tree is in
    until B.3b-user_upload-fastapi lands.
    """
    monkeypatch.setattr(upload_registry, "UPLOAD_DETECTORS", {})


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


# ============================================================
# The upload step (B.3b-user_upload-core)
# ============================================================

#: The handler's call graph, as `entry_points` would have built it.
UPLOAD_CALL_GRAPH = {"handle": set(), "save": set()}


class FakeDetector(UploadDetector):
    """One upload per entry point it is handed."""

    name = "fake"

    def detect(self, tree, file_path, entry_points):
        return [
            UploadSite(
                entry_point=node, file_path=file_path, line=1,
                framework=self.name,
            )
            for node in entry_points
        ]


def _upload_delta(project):
    """The `fs` fixture, plus the entry point an upload site names.

    The read site has **no** `source_type` here, unlike `_fs_delta`'s:
    what the upload step is being asked is whether it stamps one. The
    `tool` node comes first so the resolver's own `writes_to` edge is the
    tool's, which is a different link from the upload writer's.
    """
    built = project(
        files={FS_HANDLER: FS_SOURCE},
        entry_points=EntryPointOutput(call_graph=dict(UPLOAD_CALL_GRAPH)),
    )
    built.delta.nodes.extend([
        Node(
            id="tool:handle", kind="tool", name="handle", host_id="localhost",
            attributes={"source_file": FS_HANDLER, "line_start": 1},
        ),
        Node(
            id="entry_point:1", kind="entry_point", name="POST /upload",
            host_id="localhost",
            attributes={
                "source_file": FS_HANDLER, "line_start": 1,
                "handler": "handle", "method": "POST", "path": "/upload",
            },
        ),
        Node(
            id="untrusted_source:1", kind="untrusted_source",
            name="open:/uploads/report.txt", host_id="localhost",
            attributes={
                "source_file": FS_HANDLER, "line_start": FS_READ_LINE,
            },
        ),
    ])
    return built


def test_the_upload_step_is_a_no_op_with_no_detector(project) -> None:
    """B.3b-user_upload-core: the `fs` fixture earns nothing extra."""
    register_family("fs", FsFamily)
    built = _upload_delta(project)

    StoreResolverStage().apply(built.delta, built.ctx)

    assert [e.kind for e in built.delta.edges] == [
        "writes_to", "reads_from",
    ]
    assert built.delta.edges[0].source_id == "tool:handle"
    assert not [
        node for node in built.delta.nodes
        if node.attributes.get("source_type") == "user_upload"
    ]


def test_a_detector_makes_the_stage_emit_the_upload_writer_edge(
    project,
) -> None:
    """A test-local registration, cleaned up by the fixture.

    The handler writes the store itself, so the writer's claim is the
    strongest one it has: base 0.5, an equal literal and the write in the
    handler's own body, `1 − 0.5 × 0.4 × 0.8 × 0.8 = 0.872`.
    """
    register_family("fs", FsFamily)
    upload_registry.register_upload_detector("fake", FakeDetector)
    built = _upload_delta(project)

    StoreResolverStage().apply(built.delta, built.ctx)

    store = [n for n in built.delta.nodes if n.kind == "store"][0]
    writes_to = [e for e in built.delta.edges if e.kind == "writes_to"]
    assert [edge.source_id for edge in writes_to] == [
        "tool:handle", "entry_point:1",
    ]

    upload_edge = writes_to[1]
    assert upload_edge.target_id == store.id
    assert upload_edge.flow_kind == "structural"
    assert upload_edge.observed_by == ["ast"]
    assert round(upload_edge.identity_confidence, 4) == 0.872

    source = [n for n in built.delta.nodes if n.kind == "untrusted_source"][0]
    assert source.attributes["source_type"] == "user_upload"

