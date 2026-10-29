"""An empty detector registry is still a no-op (B.3b-…-module, N1).

B.3b-user_upload-core-module adds a branch to the writer and no detector:
nothing in the tree produces a module-level `UploadSite` yet
(B.3b-user_upload-streamlit is the first that will), so on every corpus
the branch is unreachable and the scan is the scan
B.3b-user_upload-core and B.3b-fs produced.

What is pinned here is that invariant **through the stage** — the
pipeline's own seam — on a fixture shaped for the branch: a module-level
entry point (`entry_kind="streamlit"`), a write directly in the module
body, and a read of the same literal. With an empty registry the stage's
delta is the delta the resolver alone produced, node for node and edge
for edge; with a detector registered — the last two tests — the same
fixture earns the edge and the attribution, which is what makes the no-op
a difference and not just a quiet path.

The baseline is not empty, and that is deliberate: the resolver builds the
store and the `reads_from` edge on its own, and it builds **no**
`writes_to` for the module body, because the writer of a write in no
function is no node (`resolver._writer_node_id`). So the fixture has
something to compare and something for the branch to add.
"""

from __future__ import annotations

import pytest

from aicorellator.graph.models import Node
from aicorellator.parsing.entry_points import EntryPointOutput
from aicorellator.parsing.normalizer_stages import StoreResolverStage
from aicorellator.store.confidence import (
    CODE_CONTEXT_SAME_MODULE,
    LITERAL_EQUAL,
    combine_signals,
)
from aicorellator.store.context.upload import registry
from aicorellator.store.context.upload.base import UploadDetector, UploadSite
from aicorellator.store.context.upload.context import UserUploadContext
from aicorellator.store.context.upload.writer import UPLOAD_WRITER_BASE_MODULE
from aicorellator.store.families.fs import FsFamily
from aicorellator.store.registry import register_family
from aicorellator.store.resolver import StoreResolver

from .conftest import MODULE, module_entry

LITERAL = "/uploads/report.txt"

#: The line the read call is on — the source node's `line_start`, which is
#: how the resolver pairs a read site with its `untrusted_source`.
READ_LINE = 5

# A module body that writes the upload, and a reader of the same literal
# in one of the module's functions: the module-level shape of the core
# step's fixture, with the write where only this branch can reach it.
SOURCE = (
    f'with open("{LITERAL}", "wb") as fh:\n'
    "    fh.write('x')\n"
    "\n"
    "def read_report():\n"
    f'    return open("{LITERAL}", "rb").read()\n'
)


@pytest.fixture(autouse=True)
def fs_family() -> None:
    """The real `fs` family, explicitly (registering it twice is a no-op)."""
    register_family("fs", FsFamily)


def _built(project):
    """The pipeline's nodes: a module-level entry, a write, and a read."""
    entry_points = EntryPointOutput(
        call_graph={"read_report": set()}, imports={MODULE: []},
    )
    built = project(files={MODULE: SOURCE}, entry_points=entry_points)
    built.delta.nodes.extend([
        module_entry(),
        Node(
            id="untrusted_source:1", kind="untrusted_source",
            name="user_upload:open", host_id="localhost",
            attributes={"source_file": MODULE, "line_start": READ_LINE},
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


class ModuleLevelDetector(UploadDetector):
    """One site per module-level entry point — the Streamlit shape.

    Its selector is the writer's own: `entry_kind` in the closed set. A
    detector that read the node's `handler` would find none on a real CLI
    or Streamlit node, which is what the follow-up fixed.
    """

    name = "fake-module"

    def detect(self, tree, file_path, entry_points):
        return [
            UploadSite(
                entry_point=node, file_path=file_path,
                line=node.attributes.get("line_start", 1),
                framework=self.name,
            )
            for node in entry_points
            if node.attributes.get("entry_kind") in {"cli", "streamlit"}
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


def test_no_module_level_writer_edge_with_an_empty_registry(project) -> None:
    """No edge from the module-level entry point: no detector, no writer."""
    built = _built(project)
    StoreResolverStage().apply(built.delta, built.ctx)

    writes_to = [e for e in built.delta.edges if e.kind == "writes_to"]
    assert writes_to == []
    assert not [
        edge for edge in built.delta.edges
        if edge.source_id == "entry_point:1"
    ]
    # The baseline is not empty: the resolver's own read edge is there.
    assert [edge.kind for edge in built.delta.edges] == ["reads_from"]


def test_no_attribution_with_an_empty_registry(project) -> None:
    """No upload, no scope, no role."""
    built = _built(project)
    StoreResolverStage().apply(built.delta, built.ctx)

    assert "source_type" not in built.node("untrusted_source:1").attributes
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


# ============================================================
# N1's contrast — the same fixture, with a module-level detector
# ============================================================

def test_a_registered_module_level_detector_makes_the_step_run(
    project,
) -> None:
    """One detector and the same fixture earns the edge and the role.

    The writer's edge is the module body's, at the module base, and the
    read in the same file becomes `user_upload`. The resolver's own
    `reads_from` edge is still there beside it: this step adds links, it
    does not replace the resolver's.
    """
    registry.register_upload_detector("fake-module", ModuleLevelDetector)
    built = _built(project)

    StoreResolverStage().apply(built.delta, built.ctx)

    writes_to = [e for e in built.delta.edges if e.kind == "writes_to"]
    assert [edge.source_id for edge in writes_to] == ["entry_point:1"]
    store = next(iter(built.nodes("store")))
    assert [edge.target_id for edge in writes_to] == [store.id]

    expected = combine_signals(
        UPLOAD_WRITER_BASE_MODULE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
    )
    assert [round(edge.identity_confidence, 4) for edge in writes_to] == [
        round(expected, 4)
    ]
    assert round(writes_to[0].identity_confidence, 4) == 0.808

    reader = built.node("untrusted_source:1")
    assert reader.attributes["source_type"] == "user_upload"
    assert [edge.kind for edge in built.delta.edges].count("reads_from") == 1


def test_the_stage_takes_the_upload_context_as_a_parameter(project) -> None:
    """A caller may hand in a context built with explicit detectors."""
    built = _built(project)
    stage = StoreResolverStage(uploads=UserUploadContext(
        detectors=[ModuleLevelDetector()]
    ))

    stage.apply(built.delta, built.ctx)

    assert [
        edge.source_id for edge in built.delta.edges
        if edge.kind == "writes_to"
    ] == ["entry_point:1"]
    assert built.node("untrusted_source:1").attributes["source_type"] == (
        "user_upload"
    )

