"""The upload-core tests (B.3b-user_upload_core).

A package, unlike its sibling directories, because two of the module
names the brief asks for — `test_base` and `test_registry` — are also
`tests/test_store_core`'s, and pytest imports a directory without an
`__init__.py` by bare module name. `tests/` is already a package, so the
marker here is all it takes.
"""
