"""The Flask upload detector's tests (B.3b-user_upload-flask).

A package, unlike most of its sibling directories, because `test_detector`,
`test_registration`, `test_end_to_end` and `test_attribution` are also
`tests/test_store_upload_fastapi`'s, `tests/test_store_upload_streamlit`'s
and `tests/test_store_upload_django`'s module names and pytest imports a
directory without an `__init__.py` by bare module name. `tests/` is already
a package, so the marker here is all it takes.
"""
