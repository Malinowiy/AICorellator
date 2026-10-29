"""The store-family interface (B.3b-core, acceptance C1).

ADR-0021 §12 gives a family the framework-detector shape (ADR-0011): a
class with a `name` and `detect_*` methods, and a value type per side.
What is pinned here is the *shape* — the fields a family fills, the
defaults it may leave alone, and the fact that the base class does not
pretend to detect anything.
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields

import pytest

from aicorellator.store import (
    Context,
    Family,
    ModuleFacts,
    ReadSite,
    StoreResolver,
    WriteSite,
    register_family,
    registered_families,
)


def _call(source: str = "write('/uploads/a')") -> ast.Call:
    """The `ast.Call` in a one-expression snippet."""
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.Expr)
    assert isinstance(node.value, ast.Call)
    return node.value


# ============================================================
# Value types
# ============================================================

def test_write_site_has_the_documented_fields() -> None:
    """The five §12 fields, plus the facts the resolver fills."""
    names = [f.name for f in fields(WriteSite)]
    assert names == [
        "call_site",
        "file_path",
        "line",
        "identity_hint",
        "confidence_base",
        "facts",
    ]


def test_read_site_adds_the_source_type() -> None:
    """A read carries the semantic role; a write does not (ADR-0021 §1)."""
    names = [f.name for f in fields(ReadSite)]
    assert names == [
        "call_site",
        "file_path",
        "line",
        "identity_hint",
        "confidence_base",
        "source_type",
        "facts",
    ]


def test_site_defaults() -> None:
    """`confidence_base` is §8's base, `source_type` may be absent.

    A site is built by a family with as little as possible: the base is
    the default a context raises, the facts are the resolver's job, and
    a read that belongs to no source says so with `None`.
    """
    write = WriteSite(
        call_site=_call(), file_path="app/handler.py", line=7,
        identity_hint="/uploads/",
    )
    assert write.confidence_base == 0.3
    assert write.facts is None

    read = ReadSite(
        call_site=_call(), file_path="app/handler.py", line=9,
        identity_hint="/uploads/",
    )
    assert read.confidence_base == 0.3
    assert read.source_type is None
    assert read.facts is None


def test_module_facts_are_immutable_and_empty_by_default() -> None:
    """The facts are per file and a file with no local import has none."""
    facts = ModuleFacts(file_path="app/routes.py")
    assert facts.local_imports == frozenset()
    with pytest.raises(FrozenInstanceError):
        facts.file_path = "other.py"  # type: ignore[misc]


def test_context_is_a_placeholder_a_family_may_ignore() -> None:
    """B.3b-core declares the parameter, not the shape (§12)."""
    assert Context() is not None


# ============================================================
# The base class
# ============================================================

def test_family_declares_name_and_the_two_detectors() -> None:
    assert Family.name == ""
    assert callable(Family.detect_writes)
    assert callable(Family.detect_reads)


def test_base_detectors_raise() -> None:
    """A family that implements nothing detects nothing — loudly.

    Silent empty lists would make a half-written family look like a
    family that recognized nothing, which is the harder bug to see.
    """
    family = Family()
    tree = ast.parse("write('/uploads/a')")
    with pytest.raises(NotImplementedError):
        family.detect_writes(tree, "app/handler.py")
    with pytest.raises(NotImplementedError):
        family.detect_reads(tree, "app/handler.py")


def test_a_subclass_only_implements_what_it_needs() -> None:
    class FsFamily(Family):
        name = "fs"

        def detect_writes(self, tree, file_path, *, context=None):
            return []

    register_family("fs", FsFamily)
    assert [type(f) for f in registered_families()] == [FsFamily]


# ============================================================
# The package's public surface
# ============================================================

def test_the_package_exports_the_documented_names() -> None:
    """The interface the subsystem publishes: C1's entry points."""
    import aicorellator.store as store

    for name in (
        "Family",
        "WriteSite",
        "ReadSite",
        "StoreResolver",
        "register_family",
        "registered_families",
    ):
        assert hasattr(store, name)
