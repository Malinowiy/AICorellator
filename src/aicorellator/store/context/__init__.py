"""Store contexts — facts a single file does not hold (ADR-0021 §12).

A context is what a family cannot know from one module: which upload
endpoint a handler belongs to, which request-scoped sink a route
reaches. The interface accepts one (`families.base.Context`); the first
concrete context (`user_upload`) is B.3b-user_upload, and this package
is where it lands.

Empty in B.3b-core: the parameter exists, its shape does not.
"""
