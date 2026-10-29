"""An empty detector registry is a no-op (B.3b-user_upload-core, N1).

The core step registers no detector, so on every scan the upload step
identifies nothing, emits no `writes_to` edge and attributes no
`source_type`. What is pinned here is that invariant **through the
stage** — the pipeline's own seam — on a fixture that has everything an
upload would need except a detector: an entry point with a handler, an
`fs` write and read of one literal, and a reader in the same file.

The fixture is the B.3b-fs one (a write and a read of one literal in one
module), so the comparison is exact: with an empty registry the stage's
delta is the delta the resolver alone produced, node for node and edge
for edge. With a detector registered — the last test — the same fixture
earns the extra edge and the attribution, which is what makes the
no-op a difference and not just a quiet path.
"""

from __future__ import annotations

import pytest

from aicorellator.graph.models import Node
from aicorellator.parsing.entry_points import EntryPointOutput
from aicorellator.parsing.normalizer_stages import StoreResolverStage
from aicorellator.store.context.upload import registry
from aicorellator.store.context.upload.base import UploadDetector, UploadSite
from aicorellator.store.context.upload.context import UserUploadContext
from aicorellator.store.families.fs import FsFamily
from aicorellator.store.registry import register_family
from aicorellator.store.resolver import StoreResolver

UPLOAD = "app/upload.py"
LITERAL = "/uploads/report.txt"
HANDLER = "handle"
READ_LINE = 6

# A handler that accepts an upload and writes it, and a reader of the
# same literal — the `fs` family's motivating pair, with an upload on top.
SOURCE = (
    "def handle(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
    "\n"
    "def read_report():\n"
    f'    return open("{LITERAL}", "rb").read()\n'
)


@pytest.fixture(autouse=True)
def fs_family() -> None:
    """The real `fs` family, explicitly (registering it twice is a no-op)."""
    register_family("fs", FsFamily)


def _built(project, *, with_tool: bool = True):
    """The pipeline's nodes, and the entry-point output behind them.

    The `tool` node is the component the write lives in — the shape
    `tests/test_store_core/test_fs_resolver.py` uses — and it is put
    first, so the resolver's own `writes_to` edge is the tool's. With
    `with_tool=False` the only node the write's enclosing function
    resolves to is the entry point itself, which is what a plain route
    handler is in a real scan (`entry_to_sink`'s docstring, and
    `resolver._component_index`), and that is the collision case.
    """
    entry_points = EntryPointOutput(
        call_graph={HANDLER: set(), "read_report": set()},
        imports={UPLOAD: []},
    )
    built = project(files={UPLOAD: SOURCE}, entry_points=entry_points)
    if with_tool:
        built.delta.nodes.append(Node(
            id="tool:handle", kind="tool", name=HANDLER, host_id="localhost",
            attributes={"source_file": UPLOAD, "line_start": 1},
        ))
    built.delta.nodes.extend([
        Node(
            id="entry_point:1", kind="entry_point", name="POST /upload",
            host_id="localhost",
            attributes={
                "source_file": UPLOAD, "line_start": 1, "handler": HANDLER,
            },
        ),
        Node(
            id="untrusted_source:1", kind="untrusted_source",
            name="user_upload:open", host_id="localhost",
            attributes={"source_file": UPLOAD, "line_start": READ_LINE},
        ),
    ])
    return built


def _shape(built):
    """The delta's identity, without the timestamped dataclass fields."""
    return (
        [(node.id, node.kind) for node in built.delta.nodes],
        [
            (
                edge.id, edge.kind, edge.source_id, edge.target_id,
                edge.identity_confidence, edge.flow_kind,
            )
            for edge in built.delta.edges
        ],
    )


class FakeDetector(UploadDetector):
    """One upload per entry point it is handed — the shape a real one has."""

    name = "fake"

    def detect(self, tree, file_path, entry_points):
        return [
            UploadSite(
                entry_point=node, file_path=file_path, line=1,
                framework=self.name,
            )
            for node in entry_points
        ]


# ============================================================
# N1 — the empty registry
# ============================================================

def test_the_registry_is_empty_and_the_stage_leaves_it_that_way(
    project,
) -> None:
    """The step registers nothing; only a detector module may."""
    assert registry.UPLOAD_DETECTORS == {}
    assert registry.all_names() == []

    built = _built(project)
    StoreResolverStage().apply(built.delta, built.ctx)

    assert registry.UPLOAD_DETECTORS == {}
    assert registry.all_names() == []


def test_no_upload_writer_edge_with_an_empty_registry(project) -> None:
    """No edge from the entry point: no detector, no writer."""
    built = _built(project)
    StoreResolverStage().apply(built.delta, built.ctx)

    writes_to = [e for e in built.delta.edges if e.kind == "writes_to"]
    assert [edge.source_id for edge in writes_to] == ["tool:handle"]
    assert not [
        edge for edge in built.delta.edges
        if edge.source_id == "entry_point:1"
    ]


def test_no_attribution_with_an_empty_registry(project) -> None:
    """No upload, no scope, no role."""
    built = _built(project)
    StoreResolverStage().apply(built.delta, built.ctx)

    assert not [
        node for node in built.delta.nodes
        if node.attributes.get("source_type") == "user_upload"
    ]


def test_the_delta_is_the_one_the_resolver_alone_produced(project) -> None:
    """Byte-identical to B.3b-fs: same nodes, same edges, same numbers."""
    through_stage = _built(project)
    StoreResolverStage().apply(through_stage.delta, through_stage.ctx)

    through_resolver = _built(project)
    through_resolver.delta.edges.extend(
        StoreResolver([FsFamily()]).resolve(
            through_resolver.delta, through_resolver.ctx
        )
    )

    assert _shape(through_stage) == _shape(through_resolver)
    # And the pair the stage did produce is the `fs` one, at §8's 0.776.
    assert [
        round(edge.identity_confidence, 4)
        for edge in through_stage.delta.edges
    ] == [0.776, 0.776]


# ============================================================
# N1 — the same fixture, with a detector
# ============================================================

def test_a_registered_detector_makes_the_step_run(project) -> None:
    """The contrast: one detector and the same fixture earns the edge.

    This is the non-empty path of the same code, and the reason the
    no-op above is a no-op: the only thing missing is a detector. The
    resolver's own edge (the tool writes the store, base 0.3) is still
    there beside it — two links, two writers.
    """
    registry.register_upload_detector("fake", FakeDetector)
    built = _built(project)

    StoreResolverStage().apply(built.delta, built.ctx)

    stores = {store.name: store.id for store in built.nodes("store")}
    writes_to = [e for e in built.delta.edges if e.kind == "writes_to"]
    assert [edge.source_id for edge in writes_to] == [
        "tool:handle", "entry_point:1",
    ]
    assert [edge.target_id for edge in writes_to] == [
        stores[f"fs:{LITERAL}"], stores[f"fs:{LITERAL}"],
    ]
    assert [round(edge.identity_confidence, 4) for edge in writes_to] == [
        0.776, 0.872,
    ]

    reader = built.node("untrusted_source:1")
    assert reader.attributes["source_type"] == "user_upload"


def test_an_upload_edge_and_a_resolver_edge_for_one_link_are_one_edge(
    project,
) -> None:
    """Two producers naming one link produce one id, so one row.

    A plain route handler is not a `tool` node, so the resolver's writer
    for a write inside it is the `entry_point` node itself — and then the
    upload writer names the *same* `(entry_point, store, writes_to)`. The
    edge id is the graph's primary key, so the stage merges them and
    keeps the stronger claim: the upload writer's, because the handler
    accepting the upload is evidence the resolver's family-only rule does
    not have (§8, revision 2).
    """
    registry.register_upload_detector("fake", FakeDetector)
    built = _built(project, with_tool=False)

    StoreResolverStage().apply(built.delta, built.ctx)

    writes_to = [e for e in built.delta.edges if e.kind == "writes_to"]
    assert len(writes_to) == 1
    assert writes_to[0].source_id == "entry_point:1"
    assert round(writes_to[0].identity_confidence, 4) == 0.872


def test_the_stage_takes_the_upload_context_as_a_parameter(project) -> None:
    """A caller may hand in a context built with explicit detectors."""
    built = _built(project)
    stage = StoreResolverStage(uploads=UserUploadContext(
        detectors=[FakeDetector()]
    ))

    stage.apply(built.delta, built.ctx)

    assert [
        edge.source_id for edge in built.delta.edges
        if edge.kind == "writes_to"
    ] == ["tool:handle", "entry_point:1"]

