"""The store-family interface (ADR-0021 §12).

A **family** is the technology a store is reached through — `fs`, `s3`,
`db`, `mcp_catalogue` — one module per family, registered in
`store.registry`. The *semantic role* of a store (`user_upload`,
`rag_corpus`, ...) is not a family: it is the `source_type` of the read
site (ADR-0021 §1, revised), and it stays on the `untrusted_source`.

The shape is ADR-0011's framework detector — a class with a `name` and
`detect_*` methods returning a value type — but the invocation model is
not. A framework detector runs *per file*, before `normalize`, and emits
nodes; a family runs *once per scan*, at the end of the pipeline, because
store identity is a fact about **two** call sites and no single file
holds it (ADR-0021 §1). A family therefore reports the call sites it
recognizes and stops there: `StoreResolver` pairs them, computes the
identity confidence from the signals (§8), and builds the nodes and
edges.

What a family must not do, and why:

  - **Decide identity.** `identity_hint` is the literal the call site
    names the store with, and `None` is the honest answer when the
    argument is a parameter, an f-string or an unresolved name — §5
    turns that into "no store" rather than into a guessed one.
  - **Decide the confidence.** `confidence_base` is a starting point
    (§8's 0.3) that a context may raise; the resolver combines the
    signals, so a family never invents a number.
  - **Touch the graph.** A family sees one module's AST and its path,
    never a `GraphDelta`: it cannot invent a node, and it cannot reach
    a node id it would have to resolve.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class Context:
    """A context a family may be handed (ADR-0021 §12).

    B.3b-core declares the *parameter* and not its shape: the resolver
    threads one through when a scan has one, and a family that needs
    nothing reads the default `None`. `UserUploadContext`
    (B.3b-user_upload) is the first concrete one, and it is what decides
    this type's fields — a context exists to carry facts a single file
    does not hold (an upload endpoint's path, a request-scoped sink),
    which is why it is a value type and not a service.
    """


@dataclass(frozen=True)
class ModuleFacts:
    """What one file's imports say, for §8's code-context signal.

    `local_imports` holds the **manifest paths** the file imports — a
    dotted name resolves to a path only when the scan discovered that
    file. That is what makes "both import `storage.py`" mean the same
    `storage.py` in both files (§8's shared wrapper) and what keeps a
    shared standard-library import from counting as one. The facts are
    per file, not per call site: the resolver builds them once from the
    module it already parsed (`confidence.module_facts`) and hands them
    to every site in that file.
    """

    file_path: str
    local_imports: frozenset[str] = frozenset()


@dataclass
class WriteSite:
    """One call site that writes into a store, as the family sees it.

    `call_site` is the `ast.Call` itself so a family can hand the node
    over without re-walking, and so a later family or context can read
    the arguments again. `line` is the call's line, which is what the
    resolver matches against the delta's nodes.

    `facts` is filled by the resolver (`confidence.module_facts`) when
    the family leaves it empty: §8's code-context signal reads the
    file's imports, and a family is not asked to compute them for every
    site it reports.
    """

    call_site: ast.Call
    file_path: str
    line: int
    identity_hint: str | None
    # §8's base. A context may raise it; it is the floor of every link
    # (`confidence.BASE`, pinned by a test).
    confidence_base: float = 0.3
    facts: ModuleFacts | None = None


@dataclass
class ReadSite:
    """One call site that reads from a store, as the family sees it.

    `source_type` is the semantic role of the read — `user_upload`,
    `rag_corpus`, ... — and it is the family's or the context's business
    to know it (ADR-0021 §1). `None` is a legal value and means "this
    read belongs to no untrusted source the scan registered": the site
    is then a read of nothing the graph holds, so it produces no
    `reads_from` edge.

    Everything else matches `WriteSite`; a store is symmetric, and the
    only asymmetry between the two sides is that reads carry the role.
    """

    call_site: ast.Call
    file_path: str
    line: int
    identity_hint: str | None
    confidence_base: float = 0.3
    source_type: str | None = None
    facts: ModuleFacts | None = None


class Family:
    """Base class for store families.

    A family declares `name` and implements the two detectors. Neither
    method may raise — a failure is logged by the resolver and returns
    no sites, which is ADR-0011's rule for detectors and the reason a
    broken family cannot cost a scan its other families.
    """

    #: The family's name — the registry key, the `store` node's
    #: `family` attribute, and half of the store's identity (§1).
    name: str = ""

    def detect_writes(
        self,
        tree: ast.Module,
        file_path: str,
        *,
        context: Context | None = None,
    ) -> list[WriteSite]:
        """The write call sites this family recognizes in one module.

        `tree` is the file's parsed module and `file_path` its
        target-relative path, the same spelling the manifest and TSA
        use. Returns an empty list when the file writes into no store
        of this family.
        """
        raise NotImplementedError

    def detect_reads(
        self,
        tree: ast.Module,
        file_path: str,
        *,
        context: Context | None = None,
    ) -> list[ReadSite]:
        """The read call sites this family recognizes in one module."""
        raise NotImplementedError


__all__ = [
    "Context",
    "Family",
    "ModuleFacts",
    "ReadSite",
    "WriteSite",
]
