"""The upload-detector registry (ADR-0011's rule, ADR-0021 §8).

Adding a detector is a module plus a line here — `store.registry`'s rule
for families, and the reason both registries are plain dicts rather than
loaders: an import that failed is not a detector, and a name that
resolves to nothing is a config mistake to report, not a crash.

The registry is **empty until a detector is imported**, and that is
B.3b-user_upload-core's contract: `UPLOAD_DETECTORS` starts as a plain
dict, `UserUploadContext.identify` then finds no detector, no
`UploadSite` is identified, and the writer emits nothing — every scan
still runs the context step, and the step is a no-op with no branch for
it. The tree ships two names today: `fastapi` (B.3b-user_upload-fastapi)
and `streamlit` (B.3b-user_upload-streamlit), both registered by the line
in `detectors/__init__.py`, so a detector nobody imports does not run
(the same rule the family registry follows).

This registry is **separate** from `store.registry.FAMILIES`, and the
separation is the point: a family is a store technology, a detector is a
framework, and the two sets do not overlap (ADR-0021 §1, revised).
"""

from __future__ import annotations

import logging

from .base import UploadDetector

log = logging.getLogger(__name__)


#: Registered detectors, by name. Insertion order is the order `resolve`
#: iterates in, so a scan stays deterministic without anyone sorting.
UPLOAD_DETECTORS: dict[str, type[UploadDetector]] = {}


def register_upload_detector(name: str, cls: type[UploadDetector]) -> None:
    """Register a detector under `name`.

    Re-registering the *same* class under the same name is allowed and
    does nothing (a module imported twice is not a bug); registering a
    different class under a name already taken raises, because two
    detectors claiming one name would make a site's `framework`
    ambiguous (`UploadSite.framework` is the key the context dedups on).

    A class whose `name` differs from the key it is registered under is a
    warning: the key is what config names, and the mismatch would put one
    detector in a scan under two names.
    """
    existing = UPLOAD_DETECTORS.get(name)
    if existing is cls:
        return
    if existing is not None:
        raise ValueError(
            f"upload detector {name!r} is already registered as "
            f"{existing.__name__}"
        )
    if cls.name != name:
        log.warning(
            "upload detector %r declares name %r", name, cls.name,
        )
    UPLOAD_DETECTORS[name] = cls


def resolve(names: list[str] | None = None) -> list[UploadDetector]:
    """Instances for `names`, in the order given (`None` = all).

    Unknown names are logged and skipped, the family registry's rule: a
    config that asks for a detector this build does not have should cost
    the scan that detector, not the scan. A name asked for twice resolves
    once — two instances of one detector would report every site twice and
    leave the dedup to the context, which is work for nothing.
    """
    if names is None:
        names = list(UPLOAD_DETECTORS)
    detectors: list[UploadDetector] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        cls = UPLOAD_DETECTORS.get(name)
        if cls is None:
            log.warning("unknown upload detector: %s", name)
            continue
        detectors.append(cls())
    return detectors


def registered_detectors() -> list[UploadDetector]:
    """Every registered detector, in registration order.

    `UserUploadContext`'s default input, read when `identify` runs and
    not when the context is built: a context constructed before a
    detector module was imported must still see it.
    """
    return resolve(None)


def all_names() -> list[str]:
    """The registered detector names, sorted — for logs and for config."""
    return sorted(UPLOAD_DETECTORS)


__all__ = [
    "UPLOAD_DETECTORS",
    "all_names",
    "register_upload_detector",
    "registered_detectors",
    "resolve",
]
