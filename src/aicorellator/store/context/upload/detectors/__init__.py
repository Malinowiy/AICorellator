"""Upload detectors, one module per framework (ADR-0011's shape).

Importing this package is what registers the detectors: each module
registers itself in `store.context.upload.registry` when it is imported,
the same rule `store.families` follows, and this file is where the
modules are named. `store.context.upload` imports this package, so
importing the upload context is enough for the detectors to be
registered — and a detector nobody imports is a detector that does not
run.

  - `fastapi` — `FastAPIUploadDetector` (B.3b-user_upload-fastapi): a
    route handler whose parameters accept a file.
  - `streamlit` — `StreamlitUploadDetector` (B.3b-user_upload-streamlit):
    a file whose `st.file_uploader` is its entry point, and the first
    **module-level** detector — the site it reports is the module, which
    is what `writer._module_level` reads. `django` and `flask` are
    separate steps.
"""

from ..registry import register_upload_detector
from .fastapi import FastAPIUploadDetector
from .streamlit import StreamlitUploadDetector

# Registered by the literal name config names, and not by
# `FastAPIUploadDetector.name`: a drift between the two is what
# `register_upload_detector`'s warning exists to report, and passing the
# class's own attribute would silence it.
register_upload_detector("fastapi", FastAPIUploadDetector)
register_upload_detector("streamlit", StreamlitUploadDetector)

__all__ = ["FastAPIUploadDetector", "StreamlitUploadDetector"]
