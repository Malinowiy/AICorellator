"""The upload context — `user_upload` writers and their read sites.

ADR-0021 §8 (revision 2) adds one producer to the store subsystem: an
entry-point handler that **accepts an upload** is a *context-based
writer*, and the stores it writes into are its stores at base confidence
0.5 instead of 0.3. This package is that producer, and it has six
pieces:

  - `base` — `UploadDetector` and `UploadSite`: the interface a framework
    detector implements (ADR-0011's shape), and the value it returns.
  - `registry` — `UPLOAD_DETECTORS`, the detectors a scan has. Empty
    until a detector is imported (B.3b-user_upload-core's contract), and
    two names in it today: `fastapi` (B.3b-user_upload-fastapi) and
    `streamlit` (B.3b-user_upload-streamlit). A detector nobody imports
    does not run, so the registry holds what the tree shipped, not what
    it could do.
  - `detectors` — the framework modules, one per framework: `fastapi`
    and `streamlit` today, `django` and `flask` in later steps.
    Importing the package registers them; it is imported below, so
    importing the upload context is enough.
  - `context` — `UserUploadContext`, which runs the detectors over one
    file and merges what they report.
  - `writer` — `find_upload_stores`: the BFS from a handler to the `fs`
    write sites it reaches, and the `writes_to` edges that come out.
  - `attribution` — `attribute_source_type`: the `source_type` on the
    read sites in a writer's scope.

The package depends on `graph.models`, `store.confidence`,
`store.families.base` and `store.resolver`'s `STRUCTURAL`, and on
`parsing` only through the deferred import in `writer._graph_ids` — the
same reason the resolver defers it: the stage package imports this one.
"""

from .base import UploadDetector, UploadSite
from .context import UserUploadContext
from .registry import register_upload_detector, registered_detectors
from . import detectors  # noqa: F401 — registers the detectors

__all__ = [
    "UploadDetector",
    "UploadSite",
    "UserUploadContext",
    "register_upload_detector",
    "registered_detectors",
]
