"""Store contexts — facts a single file does not hold (ADR-0021 §12).

A context is what a family cannot know from one module: which upload
endpoint a handler belongs to, which request-scoped sink a route
reaches. The interface accepts one (`families.base.Context`); the first
concrete context is `user_upload`, and this package is where it lands:

  - `upload` — `UserUploadContext` (B.3b-user_upload-core) and the
    detectors that feed it. Importing `upload` registers the four
    detectors the tree ships — `fastapi` (B.3b-user_upload-fastapi),
    `streamlit` (B.3b-user_upload-streamlit), `django`
    (B.3b-user_upload-django) and `flask` (B.3b-user_upload-flask) — so
    a scan that imports the store subsystem has them without naming one.
"""

from . import upload  # noqa: F401 — the package's public entry point

__all__ = ["upload"]

