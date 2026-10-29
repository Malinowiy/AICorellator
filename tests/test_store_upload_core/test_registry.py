"""The upload-detector registry (B.3b-user_upload-core, C2).

The registry is the seam a framework detector lands on, and it is
**empty in this step** — the state the core is accepted on. What is
pinned here is the empty default, the registration rules (which mirror
`store.registry`'s), the two halves of the lookup, and one property the
brief calls out: this registry is **independent** of
`store.families.registry.FAMILIES`.

"Empty" means the registry *as this test sees it*: the directory's
conftest empties it, and the tree ships no detector yet.
"""

from __future__ import annotations

import logging

import pytest

from aicorellator.store import registry as family_registry
from aicorellator.store.context.upload import registry
from aicorellator.store.context.upload.base import UploadDetector


class FakeDetector(UploadDetector):
    name = "fake"


class OtherDetector(UploadDetector):
    name = "other"


class Mismatched(UploadDetector):
    """Registered under a name it does not declare."""

    name = "not_the_key"


# ============================================================
# C2 — the empty default
# ============================================================

def test_the_registry_starts_empty() -> None:
    assert registry.UPLOAD_DETECTORS == {}
    assert registry.all_names() == []
    assert registry.resolve() == []
    assert registry.registered_detectors() == []


def test_the_upload_registry_is_not_the_family_registry() -> None:
    """Two registries, two questions: a family is not a detector.

    `fs` is a store technology (B.3b-fs) and is registered in
    `store.registry.FAMILIES` by importing the store package; no
    framework detector exists, so the upload registry is still empty.
    """
    assert "fs" in family_registry.FAMILIES
    assert registry.UPLOAD_DETECTORS == {}


# ============================================================
# C2 — registration
# ============================================================

def test_registering_a_detector_adds_it() -> None:
    registry.register_upload_detector("fake", FakeDetector)
    assert registry.UPLOAD_DETECTORS == {"fake": FakeDetector}
    assert registry.all_names() == ["fake"]


def test_registering_the_same_class_twice_is_a_no_op() -> None:
    """A module imported twice is not a bug."""
    registry.register_upload_detector("fake", FakeDetector)
    registry.register_upload_detector("fake", FakeDetector)
    assert registry.UPLOAD_DETECTORS == {"fake": FakeDetector}


def test_two_detectors_cannot_share_a_name() -> None:
    """One name, one `framework`: the key is what a site is stamped with."""
    registry.register_upload_detector("fake", FakeDetector)
    with pytest.raises(ValueError, match="already registered"):
        registry.register_upload_detector("fake", OtherDetector)


def test_a_mismatched_name_is_a_warning(caplog) -> None:
    """The key is what config names, so the mismatch is worth saying."""
    with caplog.at_level(logging.WARNING):
        registry.register_upload_detector("fake", Mismatched)
    assert "declares name" in caplog.text
    assert registry.UPLOAD_DETECTORS == {"fake": Mismatched}


# ============================================================
# C2 — the lookup
# ============================================================

def test_resolve_returns_instances_in_the_order_asked() -> None:
    registry.register_upload_detector("fake", FakeDetector)
    registry.register_upload_detector("other", OtherDetector)

    resolved = registry.resolve(["other", "fake"])
    assert [type(detector) for detector in resolved] == [
        OtherDetector, FakeDetector,
    ]
    assert all(
        isinstance(detector, UploadDetector) for detector in resolved
    )


def test_resolve_builds_a_fresh_instance_each_time() -> None:
    """A detector that keeps state keeps it for one scan."""
    registry.register_upload_detector("fake", FakeDetector)
    first, = registry.resolve(["fake"])
    second, = registry.resolve(["fake"])
    assert first is not second


def test_a_name_asked_for_twice_resolves_once() -> None:
    """Dedup by name: two instances would report every site twice."""
    registry.register_upload_detector("fake", FakeDetector)
    assert len(registry.resolve(["fake", "fake"])) == 1


def test_an_unknown_name_is_logged_and_skipped(caplog) -> None:
    registry.register_upload_detector("fake", FakeDetector)
    with caplog.at_level(logging.WARNING):
        resolved = registry.resolve(["nope", "fake"])
    assert [type(detector) for detector in resolved] == [FakeDetector]
    assert "unknown upload detector: nope" in caplog.text


def test_all_names_is_sorted() -> None:
    registry.register_upload_detector("zeta", OtherDetector)
    registry.register_upload_detector("fake", FakeDetector)
    assert registry.all_names() == ["fake", "zeta"]


def test_registered_detectors_is_the_registry_read_now() -> None:
    """`UserUploadContext`'s default: read at call time, not at build."""
    assert registry.registered_detectors() == []
    registry.register_upload_detector("fake", FakeDetector)
    assert [type(d) for d in registry.registered_detectors()] == [
        FakeDetector
    ]
