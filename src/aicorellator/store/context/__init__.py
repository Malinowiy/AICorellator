"""Store contexts — facts a single file does not hold (ADR-0021 §12).

A context is what a family cannot know from one module: which upload
endpoint a handler belongs to, which request-scoped sink a route
reaches. The interface accepts one (`families.base.Context`); the first
concrete context is `user_upload`, and this package is where it lands:

  - `upload` — `UserUploadContext` (B.3b-user_upload-core) and the
    detectors that feed it. Importing `upload` is enough for the package
    to be loadable; it registers nothing, because the detector registry
    is empty until B.3b-user_upload-fastapi.
"""

from . import upload  # noqa: F401 — the package's public entry point

__all__ = ["upload"]

