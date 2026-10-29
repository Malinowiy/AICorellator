"""The upload context — `user_upload` writers and their read sites.

ADR-0021 §8 (revision 2) adds one producer to the store subsystem: an
entry-point handler that **accepts an upload** is a *context-based
writer*, and the stores it writes into are its stores at base confidence
0.5 instead of 0.3. This package is that producer, and it has five
pieces:

  - `base` — `UploadDetector` and `UploadSite`: the interface a framework
    detector implements (ADR-0011's shape), and the value it returns.
  - `registry` — `UPLOAD_DETECTORS`, the detectors a scan has. **Empty in
    B.3b-user_upload-core**: no detector, no site, no edge, no
    attribution, on every scan.
  - `detectors` — the framework modules (fastapi, streamlit, django,
    flask), each a later step.
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

__all__ = [
    "UploadDetector",
    "UploadSite",
    "UserUploadContext",
    "register_upload_detector",
    "registered_detectors",
]
