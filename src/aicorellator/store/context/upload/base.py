"""The upload-detector interface (ADR-0021 §8, revision 2).

An **upload detector** answers one question about one file: does this
module hold an entry point that *accepts* an upload — a request body, a
form field, a file? The answer is an `UploadSite`, and it is what makes
the handler a **context-based writer** (§8's revision): a handler that
accepts an upload is evidence the family-only rules do not have, and the
stores it writes into are its stores at base 0.5 instead of 0.3.

Why a second interface, next to `store.families.base.Family`:

  - **The unit is different.** A family is a store *technology* — one
    module per `fs`, `s3`, `db` — and it runs once per scan, on the whole
    delta. A detector is a *framework* — one module per `fastapi`,
    `streamlit`, `django`, `flask` — and it runs per file, on that file's
    AST, the way ADR-0011's framework detectors do.
  - **The question is different.** A family reports call sites that name
    a store. A detector reports a *handler* and says nothing about
    stores: which stores the handler writes into is the writer logic's
    question (`writer.find_upload_stores`), and answering it needs the
    call graph, which one file does not hold.

`upload` is not a family, and the distinction is ADR-0021 §1 (revised): a
family is the technology a store is reached through, and `user_upload` is
the *semantic role* of a read site — the `source_type` on the
`untrusted_source`, which `attribution.attribute_source_type` sets. An
upload into `/uploads/` is a `store('fs', '/uploads/')`; that the pair is
an upload is the source type.

What a detector must not do: decide identity or confidence (the writer
and `store.confidence` do), and touch the graph (it sees one module's AST
and returns values, exactly as a family does).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ....graph.models import Node


@dataclass(frozen=True)
class UploadSite:
    """One entry point that accepts an upload, as a detector sees it.

    `entry_point` is the `entry_point` **node** that accepts the upload —
    the node stages 5 and 10 built, carrying the `handler` attribute the
    writer's BFS starts from. A node and not an id, because the writer
    needs both halves: the node's id is the edge's source, and its
    `handler` is where reachability begins. A detector that was handed
    the node should not have to re-derive either.

    `file_path` is the file the site was detected in — the entry point's
    own file, and the file whose scope `attribution` claims. `line` is
    the line of the call that accepts the upload (the `File(...)`
    parameter, the `st.file_uploader` call), which is what a report shows
    and what the tests pin.

    `framework` is the detector's own name — `'fastapi'`, `'streamlit'`,
    `'django'`, `'flask'` — and not the entry point's `method`: two
    frameworks can expose one method, and the context deduplicates by
    `(entry_point.id, framework)`, so the two must not be conflated.

    Frozen, because a site is a fact about a file and not state: a
    detector returns them, the context merges them, and nothing edits one
    afterwards.
    """

    entry_point: Node
    file_path: str
    line: int
    framework: str


class UploadDetector:
    """Base class for upload detectors (ADR-0011's shape, ADR-0021 §8).

    A detector declares `name` and implements `detect`. Neither may
    raise — `UserUploadContext` logs a raising detector and skips it,
    which is ADR-0011's rule for detectors and the reason a broken
    detector cannot cost a scan its other frameworks.

    `detect` is given one file's AST and the entry-point nodes the scan
    built for that file; it returns the sites it recognizes and stops
    there. Whether a site's handler writes into anything, and with what
    confidence, is not a detector's question.
    """

    #: The detector's name — the registry key, and the `framework` it
    #: stamps on every site it reports.
    name: str = ""

    def detect(
        self,
        tree: ast.Module,
        file_path: str,
        entry_points: list[Node],
    ) -> list[UploadSite]:
        """The uploads this module's entry points accept.

        `tree` is the file's parsed module and `file_path` its
        target-relative path, the same spelling the manifest and the
        stages use. `entry_points` are the `entry_point` nodes of *this*
        file (a file with none is not offered to a detector at all), so
        a site always names one of them.

        Returns an empty list when the file accepts no upload of this
        detector's framework.
        """
        raise NotImplementedError


__all__ = [
    "UploadDetector",
    "UploadSite",
]
