"""The empty registry is a no-op on every scan (P1, P2).

With no family registered the store stage still runs — it is stage 19 of
`normalizer_stages.STAGES` — and it emits nothing and touches nothing.
That is what makes B.3b-core landable: the whole mechanism is in the
tree, and the graph is byte-for-byte what B.3a produced.

Since B.3b-fs the tree *does* ship a family (`fs`), so "no family" here
means "the fixture emptied the registry" (this directory's conftest).
That is still the right thing to test: the stage's no-op is a property
of the registry being empty, not of the tree being empty, and a scan
with no family must behave exactly as B.3a's did. The positive
counterpart — the same pipeline with `fs` registered, over a project
that names a store — is in `tests/test_pipeline.py`.

The check is stronger than "no store node": the pipeline is run twice
over one project, once with the stage and once without it, and the two
deltas are compared node by node and edge by edge. A no-op is a claim
about every scan, not about one field of one fixture.

The corpus half of P2 is not run here — the four corpora are not on this
machine. The expectation is the same one this test states, and the B.3a
pipeline test (`test_pipeline.py::test_the_pipeline_produces_no_store_
and_no_reads_from`) already asserts the graph-level negative on a real
two-file project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicorellator.ingestion import discover, ingest
from aicorellator.parsing import make_context, normalize, run_tsa
from aicorellator.parsing.ast_normalizer import GraphDelta
from aicorellator.parsing.capability_extractor import extract as extract_cap
from aicorellator.parsing.detectors_runner import run as run_detectors
from aicorellator.parsing.entry_points import run as run_entry_points
from aicorellator.parsing.llm_extractor import run as run_llm_extractor
from aicorellator.parsing.normalizer_stages import STAGES
from aicorellator.store import registry
from aicorellator.store.resolver import StoreResolver

# A project the `fs` family deliberately does *not* recognize: a handler
# writes a file into `/uploads/` and a tool reads it back, but both go
# through `write_bytes` / `read_bytes` wrappers rather than through
# `open`, so there is no call shape and no literal to report. The store
# side sees nothing — which is the point: this fixture would produce a
# store only if a family grew into it.
PROJECT_FILES: dict[str, str] = {
    "app/upload.py": '''\
"""Upload handler."""

from app.storage import write_bytes


def handle_upload(payload):
    """Store the upload."""
    return write_bytes("/uploads/report.txt", payload)
''',
    "app/tools.py": '''\
"""Tools."""

from app.storage import read_bytes


def read_report():
    """Read the report back."""
    return read_bytes("/uploads/report.txt")
''',
    "app/storage.py": '''\
"""The store client both sides import."""


def write_bytes(path, payload):
    with open(path, "wb") as fh:
        fh.write(payload)


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()
''',
}


@pytest.fixture
def target(tmp_path: Path) -> Path:
    root = tmp_path / "store_target"
    for rel_path, content in PROJECT_FILES.items():
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return root


def _pipeline(target_root: Path):
    """The scan inputs `run_scan.py` assembles, in one place."""
    target = ingest(target_root, kind="folder")
    manifest = discover(target)
    tsa = run_tsa(manifest, target.root)
    ep = run_entry_points(manifest)
    llm = run_llm_extractor(manifest)
    capabilities = {
        entry.path: extract_cap(entry.abs_path, entry.path)
        for entry in manifest.files
        if entry.language == "python"
    }
    detection = run_detectors(manifest)
    return {
        "manifest": manifest,
        "tsa_output": tsa,
        "entry_points": ep,
        "llm_output": llm,
        "capabilities": capabilities,
        "framework_detection": detection,
    }


def test_the_store_stage_is_in_the_pipeline() -> None:
    """The seam is in `normalize`, not in the CLI: one pipeline, not two."""
    names = [stage.name for stage in STAGES]
    assert "store_resolver" in names
    assert names.index("detector_merge") < names.index("store_resolver")


def test_the_pipeline_emits_no_store_with_an_empty_registry(
    target: Path,
) -> None:
    """P1: the scan runs, the stage runs, and nothing comes out of it."""
    inputs = _pipeline(target)
    delta = normalize(make_context(**inputs))

    assert delta.nodes, "the fixture is meant to produce a graph"
    assert [n for n in delta.nodes if n.kind == "store"] == []
    assert [e for e in delta.edges if e.kind == "reads_from"] == []
    assert all(e.identity_confidence is None for e in delta.edges)
    assert [e for e in delta.edges if e.kind == "writes_to"] == []


def test_the_stage_changes_nothing_with_an_empty_registry(
    target: Path,
) -> None:
    """P2: the same delta with the stage and without it.

    Two contexts over one project, so the comparison is between two
    independent runs of the pipeline and not between a delta and itself.
    """
    inputs = _pipeline(target)

    with_stage = normalize(make_context(**inputs))

    without_stage = GraphDelta()
    for stage in STAGES:
        if stage.name == "store_resolver":
            continue
        without_stage = stage.apply(without_stage, make_context(**inputs))

    def shape(delta):
        return (
            [(n.id, n.kind) for n in delta.nodes],
            [
                (e.id, e.kind, e.source_id, e.target_id, e.identity_confidence)
                for e in delta.edges
            ],
        )

    assert shape(with_stage) == shape(without_stage)


def test_the_resolver_is_the_only_thing_missing(target: Path) -> None:
    """Registering a family is the whole difference (B.3b-fs's seam).

    The registry is empty for this test (the directory's conftest), so
    the pipeline that runs is the B.3a one and the resolver resolves
    nothing. What that pins is the *seam*: the resolver is the only
    place a family would change the scan, so the delta it is handed is
    the delta it hands back.
    """
    inputs = _pipeline(target)
    ctx = make_context(**inputs)
    delta = normalize(ctx)
    before = (len(delta.nodes), len(delta.edges))

    assert registry.FAMILIES == {}
    assert StoreResolver().resolve(delta, ctx) == []
    assert (len(delta.nodes), len(delta.edges)) == before
