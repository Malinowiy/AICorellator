"""`UserUploadContext.identify` (B.3b-user_upload-core, C3).

The context runs the registered detectors over one file and merges what
they report. What is pinned here: the empty registry is `[]`, a detector
is asked once per file, two detectors reporting one handler under one
framework are one site (and under two frameworks two), a raising
detector costs only its own sites, and `detectors=None` means the
registry **read when `identify` runs** — the rule `StoreResolver`'s
`families=None` follows, and the reason `STAGES` may build the context
at import time.
"""

from __future__ import annotations

import ast
import logging

from aicorellator.graph.models import Node
from aicorellator.store.context.upload import registry
from aicorellator.store.context.upload.base import UploadDetector, UploadSite
from aicorellator.store.context.upload.context import UserUploadContext
from aicorellator.store.families.base import Context

HANDLER = "app/routes.py"
TREE = ast.parse("def upload_file(request):\n    return request\n")


def _entry_node(node_id: str = "entry_point:1") -> Node:
    return Node(
        id=node_id, kind="entry_point", name="POST /upload",
        host_id="localhost",
        attributes={"source_file": HANDLER, "handler": "upload_file"},
    )


def _site(
    framework: str = "fake", node_id: str = "entry_point:1"
) -> UploadSite:
    return UploadSite(
        entry_point=_entry_node(node_id), file_path=HANDLER, line=1,
        framework=framework,
    )


def _keys(sites) -> list[tuple[str, str, int, str]]:
    """A site's identity without its node's timestamps.

    `Node` is a dataclass with creation timestamps, so two nodes built
    from the same id are not `==` across a second boundary; the
    projection is what makes these assertions stable.
    """
    return [
        (site.entry_point.id, site.file_path, site.line, site.framework)
        for site in sites
    ]


class FakeDetector(UploadDetector):
    name = "fake"

    def __init__(self, sites=None) -> None:
        self.sites = sites if sites is not None else [_site()]
        self.calls: list[tuple[str, int]] = []

    def detect(self, tree, file_path, entry_points):
        self.calls.append((file_path, len(entry_points)))
        return list(self.sites)


class OtherDetector(FakeDetector):
    name = "other"


class BrokenDetector(UploadDetector):
    name = "broken"

    def detect(self, tree, file_path, entry_points):
        raise RuntimeError("boom")


# ============================================================
# C3 — the empty registry
# ============================================================

def test_with_no_detector_nothing_is_identified() -> None:
    """The core's invariant: empty registry, empty answer."""
    assert UserUploadContext().identify(TREE, HANDLER, [_entry_node()]) == []


def test_an_explicit_empty_list_ignores_the_registry() -> None:
    registry.register_upload_detector("fake", FakeDetector)
    context = UserUploadContext(detectors=[])
    assert context.identify(TREE, HANDLER, [_entry_node()]) == []


# ============================================================
# C3 — the registry, read at call time
# ============================================================

def test_the_registry_is_read_when_identify_runs() -> None:
    """A detector registered after the context was built still counts."""
    context = UserUploadContext()
    assert context.identify(TREE, HANDLER, [_entry_node()]) == []

    registry.register_upload_detector("fake", FakeDetector)
    assert _keys(context.identify(TREE, HANDLER, [_entry_node()])) == _keys(
        [_site()]
    )


def test_the_context_is_the_concrete_context_type() -> None:
    """`families.base.Context` is a declared parameter; this decides it."""
    assert isinstance(UserUploadContext(), Context)
    context = UserUploadContext(detectors=[FakeDetector()])
    assert isinstance(context.detectors, tuple)
    assert [type(d) for d in context.detectors] == [FakeDetector]


# ============================================================
# C3 — the detectors
# ============================================================

def test_a_detector_is_asked_once_per_file() -> None:
    detector = FakeDetector()
    context = UserUploadContext(detectors=[detector])

    context.identify(TREE, HANDLER, [_entry_node()])

    assert detector.calls == [(HANDLER, 1)]


def test_two_detectors_reporting_one_handler_are_one_site() -> None:
    """Dedup by `(entry_point.id, framework)`: one handler, one upload."""
    first = FakeDetector()
    second = OtherDetector(sites=[_site()])
    context = UserUploadContext(detectors=[first, second])

    sites = context.identify(TREE, HANDLER, [_entry_node()])

    assert _keys(sites) == _keys([_site()])


def test_two_frameworks_reporting_one_handler_stay_two() -> None:
    """Two frameworks are two pieces of evidence, not one site."""
    context = UserUploadContext(detectors=[
        FakeDetector(), OtherDetector(sites=[_site(framework="other")]),
    ])

    sites = context.identify(TREE, HANDLER, [_entry_node()])

    assert [site.framework for site in sites] == ["fake", "other"]


def test_two_handlers_are_two_sites() -> None:
    context = UserUploadContext(detectors=[FakeDetector(sites=[
        _site(node_id="entry_point:1"), _site(node_id="entry_point:2"),
    ])])

    sites = context.identify(
        TREE, HANDLER, [_entry_node(), _entry_node("entry_point:2")],
    )

    assert [site.entry_point.id for site in sites] == [
        "entry_point:1", "entry_point:2",
    ]


def test_a_raising_detector_costs_only_its_own_sites(caplog) -> None:
    """ADR-0011's rule: a broken detector is logged, not fatal."""
    context = UserUploadContext(detectors=[BrokenDetector(), FakeDetector()])

    with caplog.at_level(logging.ERROR):
        sites = context.identify(TREE, HANDLER, [_entry_node()])

    assert _keys(sites) == _keys([_site()])
    assert "upload detector broken failed on" in caplog.text


def test_the_sites_arrive_in_detector_order() -> None:
    context = UserUploadContext(detectors=[
        OtherDetector(sites=[_site(framework="other")]), FakeDetector(),
    ])
    sites = context.identify(TREE, HANDLER, [_entry_node()])
    assert [site.framework for site in sites] == ["other", "fake"]


def test_identify_is_per_file_and_says_so() -> None:
    """A detector is handed the file it is being asked about."""
    detector = FakeDetector(sites=[])
    UserUploadContext(detectors=[detector]).identify(
        TREE, "app/other.py", [],
    )
    assert detector.calls == [("app/other.py", 0)]

