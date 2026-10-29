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

The package depends on `graph.models` and the standard library, and on
`parsing` only through a deferred import inside the resolver (see
`resolver._graph_ids`), because `parsing.normalizer_stages.store_resolver`
imports this package: one of the two directions has to be lazy, and the
stage is the side that cannot be.
"""

from . import families  # noqa: F401 — importing it registers the families
from .families.base import Context, Family, ModuleFacts, ReadSite, WriteSite
from .registry import register_family, registered_families
from .resolver import StoreResolver

__all__ = [
    "Context",
    "Family",
    "ModuleFacts",
    "ReadSite",
    "StoreResolver",
    "WriteSite",
    "register_family",
    "registered_families",
]
