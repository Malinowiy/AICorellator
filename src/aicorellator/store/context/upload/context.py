"""`UserUploadContext` — the uploads a scan's files declare (ADR-0021 §8).

The context is the piece ADR-0021 §12 reserves for facts one file does not
hold. For an upload it is small on purpose: it runs the registered
detectors over one file, merges what they found, and stops. Everything
downstream — which stores the handler writes into, and what that is worth
— is the writer logic's (`writer.find_upload_stores`) and the stage's.

It is the concrete `families.base.Context` (that type is a declared
parameter whose shape this class decides), which is why it is a frozen
value type and not a service: the resolver's `context` parameter is where
a scan would hand one to a family, and a scan that has none passes `None`
and nothing changes.

Empty by default. With no detector registered, `identify` returns `[]`,
the writer has nothing to start from, and the scan is the scan B.3b-fs
produced — which is the invariant B.3b-user_upload-core is accepted on.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from typing import Sequence

from ....graph.models import Node
from ...families.base import Context
from .base import UploadDetector, UploadSite
from .registry import registered_detectors

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserUploadContext(Context):
    """The detectors a scan identifies uploads with.

    `detectors=None` means **the registry, read when `identify` runs**
    and not when the context is built — `StoreResolverStage`'s default
    context is created once, while `normalizer_stages.STAGES` is built at
    import, so a context that captured instances at construction would
    never see a detector registered later in the same process. That is
    the same rule `StoreResolver`'s `families=None` follows.
    `detectors=[]` is the other, explicit answer — no detectors, whatever
    the registry holds — and it is what a test uses to keep a scan's
    uploads under its own control.

    A list is accepted and stored as a tuple, so the context stays
    hashable and cannot be changed after construction.
    """

    detectors: Sequence[UploadDetector] | None = None

    def __post_init__(self) -> None:
        """Store the detectors as a tuple (`__init__` takes a sequence)."""
        if isinstance(self.detectors, list):
            object.__setattr__(self, "detectors", tuple(self.detectors))

    def identify(
        self,
        tree: ast.Module,
        file_path: str,
        entry_points: list[Node],
    ) -> list[UploadSite]:
        """The uploads `file_path`'s entry points accept, deduplicated.

        Each detector is asked once, for this file, with the entry-point
        nodes the scan built for it. The results are merged in detector
        order and deduplicated by `(entry_point.id, framework)`: two
        detectors of one framework reporting one handler are one site —
        the same handler accepting one upload — while two frameworks
        reporting one handler stay two, because they are two pieces of
        evidence for the same fact and the writer is free to weigh them
        apart.

        First-seen order is kept, so the sites a scan reports are a
        function of the file and the registry and not of a walk.
        """
        found: list[UploadSite] = []
        seen: set[tuple[str, str]] = set()
        for detector in self._detectors():
            for site in self._detect(
                detector, tree, file_path, entry_points
            ):
                key = (site.entry_point.id, site.framework)
                if key in seen:
                    continue
                seen.add(key)
                found.append(site)
        return found

    # --------------------------------------------------------
    # Internals
    # --------------------------------------------------------

    def _detectors(self) -> Sequence[UploadDetector]:
        """The configured detectors, or the registry read **now**.

        `None` is the registry, at call time — see the class docstring.
        An explicit empty sequence stays empty.
        """
        if self.detectors is None:
            return registered_detectors()
        return self.detectors

    def _detect(
        self,
        detector: UploadDetector,
        tree: ast.Module,
        file_path: str,
        entry_points: list[Node],
    ) -> list[UploadSite]:
        """One `detect` call, with a raising detector logged and skipped.

        ADR-0011's rule, and the reason a detector is not asked to be
        careful: a framework that cannot be read costs its own sites and
        nothing else — not the file's other detectors, and not the other
        files.
        """
        try:
            return list(detector.detect(tree, file_path, entry_points))
        except Exception:
            log.exception(
                "upload detector %s failed on %s", detector.name, file_path,
            )
            return []


__all__ = ["UserUploadContext"]
