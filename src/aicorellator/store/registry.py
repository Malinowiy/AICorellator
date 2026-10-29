"""The store-family registry (ADR-0021 §12).

Adding a family is a module plus a line here — ADR-0011's rule for
framework detectors, and the reason the registry is a plain dict rather
than a loader: an import that failed is not a family, and a name that
resolves to nothing is a config mistake to report, not a crash.

The registry is **empty until a family is imported**, and that is
B.3b-core's contract: `FAMILIES` starts as a plain dict, every scan
still runs the resolver, a registry holding no family yields no store
edge rather than a special case, and the pipeline needs no branch for
either state. The tree ships three names today — `fs` (B.3b-fs), `s3`
(B.3b-s3) and `db` (B.3b-db) — and they arrive by importing
`families/__init__.py`, which is where the registration lines live
(`register_family("fs", FsFamily)` and the two below it). A family that
nobody imports does not run (the same rule the detector registry
follows).
"""

from __future__ import annotations

import logging

from .families.base import Family

log = logging.getLogger(__name__)


#: Registered families, by name. Insertion order is the resolver's
#: iteration order, so the pipeline stays deterministic without the
#: resolver sorting anything.
FAMILIES: dict[str, type[Family]] = {}


def register_family(name: str, cls: type[Family]) -> None:
    """Register a family under `name`.

    Re-registering the *same* class under the same name is allowed and
    does nothing (a module imported twice is not a bug); registering a
    different class under a name already taken raises, because two
    families claiming one name would make a store's identity ambiguous
    (§1 keys on the family name).

    A class whose `name` differs from the key it is registered under is
    a warning: the key is what config names and what the resolver
    reports, and the mismatch would put one family in the graph under
    two names.
    """
    existing = FAMILIES.get(name)
    if existing is cls:
        return
    if existing is not None:
        raise ValueError(
            f"store family {name!r} is already registered as "
            f"{existing.__name__}"
        )
    if cls.name != name:
        log.warning(
            "store family %r declares name %r", name, cls.name,
        )
    FAMILIES[name] = cls


def resolve(names: list[str] | None = None) -> list[Family]:
    """Instances for `names`, in the order given (`None` = all).

    Unknown names are logged and skipped, the detector registry's rule:
    a config that asks for a family this build does not have should
    cost the scan that family, not the scan.
    """
    if names is None:
        names = list(FAMILIES)
    families: list[Family] = []
    for name in names:
        cls = FAMILIES.get(name)
        if cls is None:
            log.warning("unknown store family: %s", name)
            continue
        families.append(cls())
    return families


def registered_families() -> list[Family]:
    """Every registered family, in registration order.

    The resolver's default input: an instance per registered family,
    fresh per call, so a family that keeps state keeps it for one scan
    and not across scans.
    """
    return resolve(None)


def all_names() -> list[str]:
    """The registered family names, sorted — for logs and for config."""
    return sorted(FAMILIES)


__all__ = [
    "FAMILIES",
    "all_names",
    "register_family",
    "registered_families",
    "resolve",
]
