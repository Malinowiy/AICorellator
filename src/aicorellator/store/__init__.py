"""The store subsystem — store identity as a probabilistic claim.

ADR-0021 makes a store a graph entity (`store`, `writes_to`,
`reads_from`, `identity_confidence`) and this package is what decides
where the stores are. It is a sibling of `parsing/`, `graph/` and
`analysis/`, and it consumes what `parsing/` produced: it runs on the
`GraphDelta` at the end of the normalizer pipeline, once per scan, and
never per file.

The pieces:

  - `families.base` — the interface: a `Family` per store technology,
    reporting `WriteSite`s and `ReadSite`s and nothing else.
  - `families.fs` — the first family; importing `families` registers it
    (`fs`), so a scan sees it without any caller naming it.
  - `registry` — name → family, read by the resolver when it runs.
  - `confidence` — §8's signals and the combination, in one place.
  - `resolver` — `StoreResolver`: asks the families, builds the stores,
    resolves both ends against the delta, and returns the edges.
  - `llm_fallback` — the store-identity LLM fallback (B.3b-llm-fallback,
    ADR-0023): the opt-in last resort for a site no deterministic
    producer could name. It is *not* imported here — the store resolver
    stage imports it when it runs — and it is the one piece of this
    package that reaches outside it: `graph.models` and the standard
    library, plus D.2's cache module (the shared cache file) and
    `analysis.client`'s JSON extraction.
  - `context.upload` — the `user_upload` context (B.3b-user_upload-core):
    an `UploadDetector` per framework, a `UserUploadContext` that runs
    them, and the writer logic that links an upload handler to the `fs`
    stores it writes into. Importing this package imports the detectors,
    so the ones the tree ships (`fastapi`, B.3b-user_upload-fastapi, and
    `streamlit`, B.3b-user_upload-streamlit) are registered without a
    caller naming one.

The package depends on `graph.models` and the standard library, and on
`parsing` only through a deferred import inside the resolver (see
`resolver._graph_ids`), because `parsing.normalizer_stages.store_resolver`
imports this package: one of the two directions has to be lazy, and the
stage is the side that cannot be. The `llm_fallback` subpackage is the
one exception and is deliberately **not** imported by this module: the
stage imports it when it runs, and it is the piece that reads D.2's cache
module and `analysis.client` (ADR-0023 §4 — the shared cache file).
"""

from . import families  # noqa: F401 — importing it registers the families
from .context.upload import (
    UploadDetector,
    UploadSite,
    UserUploadContext,
    register_upload_detector,
)
from .families.base import Context, Family, ModuleFacts, ReadSite, WriteSite
from .registry import register_family, registered_families
from .resolver import StoreResolver

__all__ = [
    "Context",
    "Family",
    "ModuleFacts",
    "ReadSite",
    "StoreResolver",
    "UploadDetector",
    "UploadSite",
    "UserUploadContext",
    "WriteSite",
    "register_family",
    "register_upload_detector",
    "registered_families",
]
