"""StoreResolverStage — the seam a family plugs into (B.3b).

The stage is the pipeline's half of ADR-0021 §12: the resolver owns the
work, and this is where the pipeline hands it the delta and takes the
edges back. Three things are pinned here:

  - with the registry empty the stage is a no-op — the delta comes back
    as the same object with the same lists;
  - with a family it produces the store node and the two edges (the
    same shapes `tests/test_store_core/test_resolver.py` pins on the
    resolver itself);
  - the registry is read when the stage *runs*, not when it is built —
    `normalizer_stages.STAGES` is a module-level tuple built at import,
    so a family registered after that must still be seen.

The four corpora are not part of this test: no family exists yet, so a
corpus run cannot differ from B.3a's, and the pipeline-level negative is
asserted in `tests/test_store_core/test_no_edges.py`.
"""

from __future__ import annotations

import ast

import pytest

from aicorellator.graph.models import Node
from aicorellator.parsing.normalizer_stages import STAGES, StoreResolverStage
from aicorellator.parsing.normalizer_stages.base import NormalizerStage
from aicorellator.store import register_family, registry
from aicorellator.store.families.base import Family, ReadSite, WriteSite
from aicorellator.store.resolver import StoreResolver

HANDLER = "app/handler.py"


@pytest.fixture(autouse=True)
def empty_registry(monkeypatch: pytest.MonkeyPatch):
    """The registry is process-global, and these tests register into it.

    Without this, the fake family registered below would be visible to
    every later test in the session — including the pipeline tests that
    assert what an empty registry does (tests run in directory order,
    and `test_normalizer_stages` sorts before `test_store_core`).
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
    """The stage does not register anything; only a family module does."""
    assert registry.FAMILIES == {}
    assert registry.all_names() == []
