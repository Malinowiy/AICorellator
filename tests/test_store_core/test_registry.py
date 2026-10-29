"""The family registry (B.3b-core, acceptance C2).

The registry is the seam every family lands on, and the one piece of the
store subsystem that holds process-global state. What is pinned here is
the empty default, the registration rules, and the two halves of the
lookup: `resolve` (instances, in the order asked) and `all_names`
(sorted, for config and logs).

"The empty default" means the registry *as this test sees it*: the
directory's conftest empties it, because the tree ships `fs` (B.3b-fs)
and a test about registration rules needs a registry it controls.
"""

from __future__ import annotations

import logging

import pytest

from aicorellator.store import registry
from aicorellator.store.families.base import Family


class FakeFamily(Family):
    name = "fake"


class OtherFamily(Family):
    name = "other"


class ZetaFamily(Family):
    name = "zeta"


class AlphaFamily(Family):
    name = "alpha"


class Mismatched(Family):
    """Registered under a name it does not declare."""

    name = "not_the_key"


# ============================================================
# The empty default
# ============================================================

def test_the_registry_starts_empty() -> None:
    """The registry has no hidden default; the conftest makes it empty.

    The tree ships `fs`, so "empty" here is the fixture's doing. What
    this pins is that nothing else fills the registry behind a caller's
    back: with no registration there is no family to resolve.
    """
    assert registry.FAMILIES == {}
    assert registry.all_names() == []
    assert registry.resolve() == []
    assert registry.registered_families() == []


def test_resolve_of_unknown_names_is_empty() -> None:
    """Names the registry does not hold resolve to nothing.

    `fs` is deliberately not one of them: the tree ships it (B.3b-fs),
    and a name that is real but not registered in this fixture would
    test the fixture rather than the lookup. These are names no family
    in the tree declares yet.
    """
    assert registry.resolve(["s3", "db"]) == []


# ============================================================
# Registration
# ============================================================

def test_register_family_adds_it() -> None:
    registry.register_family("fake", FakeFamily)
    assert registry.FAMILIES == {"fake": FakeFamily}
    assert registry.all_names() == ["fake"]
    assert [type(f) for f in registry.registered_families()] == [FakeFamily]


def test_registering_the_same_class_twice_is_a_no_op() -> None:
    """A module imported twice is not a bug."""
    registry.register_family("fake", FakeFamily)
    registry.register_family("fake", FakeFamily)
    assert list(registry.FAMILIES) == ["fake"]


def test_registering_a_different_class_under_a_taken_name_raises() -> None:
    """Two families under one name would make a store's identity ambiguous."""
    registry.register_family("fake", FakeFamily)
    with pytest.raises(ValueError, match="already registered"):
        registry.register_family("fake", OtherFamily)


def test_a_name_mismatch_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The key is what config names; a class that disagrees is a bug."""
    with caplog.at_level(logging.WARNING, logger=registry.__name__):
        registry.register_family("key", Mismatched)
    assert "declares name" in caplog.text
    assert list(registry.FAMILIES) == ["key"]


# ============================================================
# Lookup
# ============================================================

def test_all_names_is_sorted() -> None:
    registry.register_family("zeta", ZetaFamily)
    registry.register_family("alpha", AlphaFamily)
    assert registry.all_names() == ["alpha", "zeta"]


def test_resolve_keeps_the_registration_order() -> None:
    """The resolver iterates what it is given; the default is the dict."""
    registry.register_family("zeta", ZetaFamily)
    registry.register_family("alpha", AlphaFamily)
    assert [type(f) for f in registry.resolve()] == [ZetaFamily, AlphaFamily]
    assert [type(f) for f in registry.resolve(["alpha", "zeta"])] == [
        AlphaFamily, ZetaFamily,
    ]


def test_resolve_returns_fresh_instances() -> None:
    """A family's state lives for one scan, not across scans."""
    registry.register_family("fake", FakeFamily)
    first = registry.resolve(["fake"])[0]
    second = registry.resolve(["fake"])[0]
    assert first is not second


def test_unknown_names_are_logged_and_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A build without a family costs that family, not the scan."""
    registry.register_family("fake", FakeFamily)
    with caplog.at_level(logging.WARNING, logger=registry.__name__):
        families = registry.resolve(["nope", "fake"])
    assert [type(f) for f in families] == [FakeFamily]
    assert "unknown store family: nope" in caplog.text
