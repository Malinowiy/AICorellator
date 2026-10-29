"""Upload detectors, one module per framework (ADR-0011's shape).

Empty in B.3b-user_upload-core. The first detector —
`fastapi.py` — lands in B.3b-user_upload-fastapi, and each module
registers itself in `store.context.upload.registry` when it is imported,
the same rule `store.families` follows.

Nothing imports this package's modules for you yet, and that is the
invariant the core step relies on: an empty registry is a no-op, and a
detector that nobody imports does not run.
"""
