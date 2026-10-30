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
from dataclasses import replace
from pathlib import Path

import pytest

from aicorellator.graph.models import Edge, Node
from aicorellator.parsing.entry_points import EntryPointOutput
from aicorellator.parsing.normalizer_stages import (
    STAGES,
    StoreResolverStage,
    store_resolver as store_resolver_stage,
)
from aicorellator.parsing.normalizer_stages.base import NormalizerStage
from aicorellator.parsing.normalizer_stages.store_resolver import _modules_for
from aicorellator.scan_config import default_scan_config
from aicorellator.store import register_family, registry
from aicorellator.store.context.upload import registry as upload_registry
from aicorellator.store.context.upload.base import (
    UploadDetector,
    UploadSite,
)
from aicorellator.store.families.base import Family, ReadSite, WriteSite
from aicorellator.store.families.fs import FsFamily
from aicorellator.store.families.s3 import S3Family
from aicorellator.store.llm_fallback import FallbackError
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


def test_the_stamped_node_is_not_appended_twice(project) -> None:
    """A stamped node is already in the delta; the stage adds it once.

    The pass returns the nodes it gave the role to — the created ones *and*
    the stamped ones — and the caller appends only the ones the delta does
    not already hold (`_merge_nodes`). A second row for one node id is what
    the repository's primary key rejects, so this is the assertion that
    keeps the return value and the append from disagreeing.
    """
    register_family("fs", FsFamily)
    upload_registry.register_upload_detector("fake", FakeDetector)
    built = _upload_delta(project)

    StoreResolverStage().apply(built.delta, built.ctx)

    sources = [n for n in built.delta.nodes if n.kind == "untrusted_source"]
    assert [node.id for node in sources] == ["untrusted_source:1"]


def test_the_stage_creates_the_source_and_its_links(project) -> None:
    """The read side, at the seam (B.3b-user_upload-attribution).

    The `fs` fixture's read is in the handler's own file and names the
    store's literal, and nothing in the delta is a source for it: the
    stage creates the node, appends it (the pass returns it and does not
    append it itself), and appends the store's `reads_from` and the
    source's `has_access_to` to the reader — `tool:handle`, the node the
    read's enclosing function names.

    The reader's link is §8's reader side: base 0.3, an equal literal and
    the same module, `1 − 0.7 × 0.4 × 0.8 = 0.776`.
    """
    register_family("fs", FsFamily)
    upload_registry.register_upload_detector("fake", FakeDetector)
    built = _upload_delta(project)
    built.delta.nodes = [
        node for node in built.delta.nodes
        if node.id != "untrusted_source:1"
    ]

    StoreResolverStage().apply(built.delta, built.ctx)

    sources = [n for n in built.delta.nodes if n.kind == "untrusted_source"]
    assert [node.name for node in sources] == ["user_upload:open"]
    source = sources[0]
    assert source.attributes == {
        "source_type": "user_upload",
        "call_target": "open",
        "source_file": FS_HANDLER,
        "line_start": FS_READ_LINE,
        "reader_kind": "function",
        "reader": "handle",
    }

    store = [n for n in built.delta.nodes if n.kind == "store"][0]
    reads_from = [e for e in built.delta.edges if e.kind == "reads_from"]
    assert [(e.source_id, e.target_id) for e in reads_from] == [
        (store.id, source.id)
    ]
    assert round(reads_from[0].identity_confidence, 4) == 0.776

    access = [e for e in built.delta.edges if e.kind == "has_access_to"]
    assert [(e.source_id, e.target_id) for e in access] == [
        (source.id, "tool:handle")
    ]


# ============================================================
# The module map (B.3b-user_upload-attribution-followup-2)
# ============================================================
#
# `_modules_for` is what the writer and the attribution may read, by path.
# It holds the sites' files — the ones the identify pass and `_fs_sites`
# already parsed — and, since this step, the modules those files import:
# §8's shared-wrapper row is about a client both files reach through a
# *third* module, and that module has no body in the map until this parses
# it. The import table is the entry-point pass's; the resolution is
# `cross_file._module_matches_file`, the trailing-segment match the
# cross-file detectors already use.

#: The writer's file: it writes the store and imports the wrapper from a
#: third module, which is the shape the wrapper row is about.
MODULE_WRITER = "app/routes.py"
MODULE_WRITER_SOURCE = '''\
from app.storage import s3


def handle(payload):
    with open("/uploads/report.txt", "wb") as fh:
        fh.write(payload)
'''

#: The wrapper: a module-level client, which is the signal §8 reads.
MODULE_WRAPPER = "app/storage.py"
MODULE_WRAPPER_SOURCE = '''\
import boto3

s3 = boto3.client("s3")
'''


def _fs_write() -> WriteSite:
    """The write site `_fs_sites` would report for `MODULE_WRITER_SOURCE`."""
    return WriteSite(
        call_site=_call(), file_path=MODULE_WRITER, line=5,
        identity_hint="/uploads/report.txt",
    )


def test_the_module_map_parses_an_imported_module(project) -> None:
    """M1 — the third-file wrapper gets a body.

    `app/storage.py` is nobody's site file, and it is in the map because
    `app/routes.py` imports it. That is what lets
    `attribution._defines_a_client` find the constructor — a module with
    no body in the map cannot be a wrapper however it is written.
    """
    built = project(
        files={
            MODULE_WRITER: MODULE_WRITER_SOURCE,
            MODULE_WRAPPER: MODULE_WRAPPER_SOURCE,
        },
        entry_points=EntryPointOutput(imports={
            MODULE_WRITER: ["app.storage"],
        }),
    )

    modules = _modules_for(built.ctx, [], [_fs_write()], [])

    assert sorted(modules) == [MODULE_WRITER, MODULE_WRAPPER]
    assert "boto3.client" in ast.unparse(modules[MODULE_WRAPPER])


def test_an_import_that_matches_two_files_is_skipped(project) -> None:
    """M2 — `storage` matches `storage.py` and `pkg/storage.py`.

    A dotted name matches by trailing path segments, so one name can name
    two manifest files. The table says which *name* a file imports and not
    which file it means, so the name is dropped and **neither** file is
    parsed: guessing would put an unrelated module's constructor in front
    of the attribution, and the wrapper row then simply does not fire.
    """
    built = project(
        files={
            MODULE_WRITER: MODULE_WRITER_SOURCE,
            "storage.py": MODULE_WRAPPER_SOURCE,
            "pkg/storage.py": MODULE_WRAPPER_SOURCE,
        },
        entry_points=EntryPointOutput(imports={MODULE_WRITER: ["storage"]}),
    )

    modules = _modules_for(built.ctx, [], [_fs_write()], [])

    assert sorted(modules) == [MODULE_WRITER]


def test_an_import_that_names_no_manifest_file_is_skipped(project) -> None:
    """The other side of the same rule: no match, nothing to parse.

    A third-party module is not part of the target, so the name resolves
    to no manifest file and the map is the sites' files alone.
    """
    built = project(
        files={MODULE_WRITER: MODULE_WRITER_SOURCE},
        entry_points=EntryPointOutput(imports={MODULE_WRITER: ["boto3"]}),
    )

    modules = _modules_for(built.ctx, [], [_fs_write()], [])

    assert sorted(modules) == [MODULE_WRITER]


def test_imports_of_imports_are_not_parsed(project) -> None:
    """M3 — one hop only: the wrapper is the module the sites name.

    `app/routes.py` imports `app.storage`, and `app.storage` imports
    `app.client`: only the first is in the map. A two-hop chain — a
    wrapper that imports the client from yet another module — is a longer
    walk with its own false-positive surface, so it is a separate step.
    """
    built = project(
        files={
            MODULE_WRITER: MODULE_WRITER_SOURCE,
            MODULE_WRAPPER: MODULE_WRAPPER_SOURCE,
            "app/client.py": "import boto3\n",
        },
        entry_points=EntryPointOutput(imports={
            MODULE_WRITER: ["app.storage"],
            MODULE_WRAPPER: ["app.client"],
        }),
    )

    modules = _modules_for(built.ctx, [], [_fs_write()], [])

    assert sorted(modules) == [MODULE_WRITER, MODULE_WRAPPER]



# ============================================================
# The store-identity fallback (B.3b-llm-fallback)
# ============================================================

def _fallback_ctx(built, mode: str):
    """The context a scan that offered the fallback builds (`run_scan.py`).

    The three fields are what the stage's third step needs and has no
    other way to reach: the policy, the config file and the cache file.
    `run_scan.py` fills them from the workspace; a test fills them from
    the manifest's target root.
    """
    policy = default_scan_config()
    policy.llm_fallback.mode = mode
    root = Path(built.ctx.manifest.target.root)
    return replace(
        built.ctx,
        scan_config=policy,
        config_path=root / "config.yaml",
        cache_path=root / "cache" / "llm_classifier.sqlite3",
    )


class FallbackSpy:
    """A stand-in for `store.llm_fallback.run`, recording its call."""

    def __init__(self, stores=None, edges=None, raises=None) -> None:
        self.stores = stores or []
        self.edges = edges or []
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.stores, self.edges


def _fake_store() -> Node:
    return Node(
        id="store:fake", kind="store", name="fs:/uploads/",
        host_id="localhost",
        attributes={
            "family": "fs", "identity_hint": "/uploads/",
            "identity_source": "llm",
        },
    )


def _fake_edge() -> Edge:
    return Edge(
        id="edge:fake", source_id="tool:handle", target_id="store:fake",
        kind="writes_to", host_id="localhost", flow_kind="structural",
        identity_confidence=0.2,
    )


def test_without_a_policy_the_fallback_step_is_a_no_op(
    project, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N1 — a context with no policy is a scan that did not offer it."""
    register_family("fs", FsFamily)
    spy = FallbackSpy(raises=AssertionError("the fallback must not run"))
    monkeypatch.setattr(store_resolver_stage, "run_fallback", spy)
    built = _fs_delta(project)

    StoreResolverStage().apply(built.delta, built.ctx)

    assert spy.calls == []
    assert [e.kind for e in built.delta.edges] == [
        "writes_to", "reads_from",
    ]


def test_with_the_mode_off_the_fallback_step_is_a_no_op(
    project, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`off` returns before the families are walked, so it costs nothing."""
    register_family("fs", FsFamily)
    spy = FallbackSpy(raises=AssertionError("the fallback must not run"))
    monkeypatch.setattr(store_resolver_stage, "run_fallback", spy)
    built = _fs_delta(project)
    ctx = _fallback_ctx(built, "off")

    StoreResolverStage().apply(built.delta, ctx)

    assert spy.calls == []


def test_the_stage_merges_the_fallback_store_and_edges(
    project, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step's output goes into the delta the way the resolver's does."""
    register_family("fs", FsFamily)
    spy = FallbackSpy(stores=[_fake_store()], edges=[_fake_edge()])
    monkeypatch.setattr(store_resolver_stage, "run_fallback", spy)
    built = _fs_delta(project)
    ctx = _fallback_ctx(built, "auto")

    StoreResolverStage().apply(built.delta, ctx)

    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["scan"].llm_fallback.mode == "auto"
    assert call["config_path"] == ctx.config_path
    assert call["cache_path"] == ctx.cache_path
    assert call["host_id"] == "localhost"
    assert call["upload_sites"] == []
    assert call["sites"]

    assert "store:fake" in [n.id for n in built.delta.nodes]
    assert "edge:fake" in [e.id for e in built.delta.edges]


def test_the_stage_hands_the_fallback_every_familys_sites(
    project, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`s3` is asked about too, not only `fs`."""
    register_family("fs", FsFamily)
    register_family("s3", S3Family)
    spy = FallbackSpy()
    monkeypatch.setattr(store_resolver_stage, "run_fallback", spy)
    built = project(files={
        FS_HANDLER: FS_SOURCE,
        "app/store.py": "s3.put_object(Bucket=bucket, Key=name, Body=b)\n",
    })
    ctx = _fallback_ctx(built, "auto")

    StoreResolverStage().apply(built.delta, ctx)

    families = {site.family for site in spy.calls[0]["sites"]}
    assert families == {"fs", "s3"}


def test_a_fallback_error_fails_the_stage(
    project, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`mode: required` and a gap: the stage's caller reports the failure."""
    register_family("fs", FsFamily)
    spy = FallbackSpy(raises=FallbackError("required and unresolved"))
    monkeypatch.setattr(store_resolver_stage, "run_fallback", spy)
    built = _fs_delta(project)
    ctx = _fallback_ctx(built, "required")

    with pytest.raises(FallbackError, match="required"):
        StoreResolverStage().apply(built.delta, ctx)


def test_the_stage_hands_over_the_upload_sites_it_identified(
    project, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scope rule is the upload sites', and they are passed on."""
    register_family("fs", FsFamily)
    upload_registry.register_upload_detector("fake", FakeDetector)
    spy = FallbackSpy()
    monkeypatch.setattr(store_resolver_stage, "run_fallback", spy)
    built = _upload_delta(project)
    ctx = _fallback_ctx(built, "auto")

    StoreResolverStage().apply(built.delta, ctx)

    assert [site.framework for site in spy.calls[0]["upload_sites"]] == [
        "fake",
    ]

