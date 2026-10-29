"""The module-level upload tests (B.3b-user_upload-core-module).

A package, unlike most of its sibling directories, because `test_no_op`
is also `tests/test_store_upload_core`'s module name and pytest imports a
directory without an `__init__.py` by bare module name. `tests/` is
already a package, so the marker here is all it takes.
"""
