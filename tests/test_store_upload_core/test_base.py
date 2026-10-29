"""The upload-detector interface (B.3b-user_upload-core, C1).

`UploadDetector` is ADR-0011's shape — a class with a `name` and a
`detect` method returning a value type — and `UploadSite` is the value.
What is pinned here is that the value is a value (frozen, comparable,
carrying the entry-point node itself) and that the interface is abstract
(the base `detect` raises; a subclass's does not).

The detectors themselves are later steps: this file defines no framework
and registers nothing.
"""

from __future__ import annotations

import ast
import dataclasses

import pytest

from aicorellator.graph.models import Node
from aicorellator.store.context.upload.base import UploadDetector, UploadSite

HANDLER = "app/routes.py"


def _entry_node() -> Node:
    return Node(
        id="entry_point:1", kind="entry_point", name="POST /upload",
        host_id="localhost",
        attributes={"source_file": HANDLER, "handler": "upload_file"},
    )


def _site(entry_point: Node | None = None) -> UploadSite:
    return UploadSite(
        entry_point=entry_point or _entry_node(), file_path=HANDLER,
        line=4, framework="fake",
    )


# ============================================================
# C1 — the value type
# ============================================================

def test_a_site_carries_the_entry_point_node() -> None:
    """The node, not its id: the writer needs the id *and* the handler."""
    site = _site()
    assert site.entry_point is not None
    assert site.entry_point.kind == "entry_point"
    assert site.entry_point.attributes["handler"] == "upload_file"
    assert site.file_path == HANDLER
    assert site.line == 4
    assert site.framework == "fake"


def test_a_site_is_frozen() -> None:
    """A fact about a file, not state: nothing edits a site afterwards."""
    site = _site()
    with pytest.raises(dataclasses.FrozenInstanceError):
        site.line = 5


def test_two_equal_sites_are_the_same_value() -> None:
    """Value semantics, so a detector can be asked twice and compared."""
    entry = _entry_node()
    assert _site(entry) == _site(entry)
    assert _site(entry) != dataclasses.replace(_site(entry), line=5)


# ============================================================
# C1 — the interface
# ============================================================

def test_the_base_detect_is_abstract() -> None:
    """A detector that implements nothing is not a detector."""
    with pytest.raises(NotImplementedError):
        UploadDetector().detect(ast.parse(""), HANDLER, [_entry_node()])


def test_a_detector_declares_a_name_and_returns_sites() -> None:
    """ADR-0011's shape: `name` plus a `detect` that returns values."""
    class FakeDetector(UploadDetector):
        name = "fake"

        def detect(self, tree, file_path, entry_points):
            return [_site(entry_points[0])]

    entry = _entry_node()
    assert UploadDetector.name == ""
    assert FakeDetector.name == "fake"
    assert FakeDetector().detect(ast.parse(""), HANDLER, [entry]) == [
        _site(entry)
    ]
