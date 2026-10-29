"""How `identity_confidence` is computed (ADR-0021 §8).

Three signals, each independent, combined by `1 − Π(1 − sᵢ)` starting
from a base of 0.3:

  1. **Family match** is a gate, not a signal. Writer and reader in
     different families produce no link at all; the resolver never
     calls this module for such a pair.
  2. **Literal match** is a signal: two equal literals +0.6, one side
     literal +0.2, two different literals **no link**, neither side
     literal **no store** (§5).
  3. **Code context** is a signal: the same module +0.2, a shared local
     module (a wrapper both files import) +0.3, one file importing the
     other +0.1, nothing shared 0.

The constants are a policy choice, stated in the ADR and re-tunable
there; the *mechanism* — independent signals, a product, a base of 0.3,
a cap short of certainty — is the decision. They live here, next to the
compute, and not in `config.yaml`: the configurable parts are §9's
thresholds, and they govern the join, not the signal.

One number is worth stating outright, because it is what the default
threshold means. A pair whose literals are equal scores `1 − 0.7 × 0.4
= 0.72`, which is just above §9's default `0.7`. **The default
enumeration is therefore "the two call sites named the same store"**,
and every weaker pairing — one side unresolved at 0.44, code context
alone at 0.44, both at 0.55 — lands in the weak band. That is the
intended shape, not an accident of the constants.

The bound is exact, and it is worth having: the strongest combination
*without* equal literals is the one-side literal plus the shared
wrapper, `1 − 0.7 × 0.8 × 0.7 = 0.608`, still under `0.7`. So no amount
of code-context agreement can put a pair in the default enumeration —
the literal is what names the store, and this is the arithmetic that
says so. `test_confidence.py` pins it.

`literal_match` returns `None` for "no link": its signature would like a
float, and a float cannot say "these name different stores, do not pair
them". `None` is that answer, and the resolver drops the pair on it.
"""

from __future__ import annotations

import ast
from typing import Iterable, Mapping

from .families.base import ModuleFacts, ReadSite, WriteSite

#: §8's base: family match opens the possibility, and this is what a
#: link is worth before any signal. It is also §9's floor — a link
#: below it is no link — so the base is the one constant the weights
#: cannot take below `graph.query.WEAK_JOIN_FLOOR` (pinned by a test).
BASE = 0.3

#: §8's literal weights.
LITERAL_EQUAL = 0.6
LITERAL_ONE_SIDE = 0.2

#: §8's code-context weights, in the order the ADR lists them. The
#: rules are *alternatives*, not independent signals: the first that
#: applies is the value (`code_context`).
CODE_CONTEXT_SAME_MODULE = 0.2
CODE_CONTEXT_SHARED_WRAPPER = 0.3
CODE_CONTEXT_SHARED_IMPORT = 0.1

#: The cap. Two independent signals are still signals; a combination
#: short of certainty is the honest ceiling (ADR-0021 §7), and
#: `graph.repositories.edges` caps the stored value at the same number.
IDENTITY_CONFIDENCE_CAP = 0.99


def combine_signals(base: float, signals: Iterable[float]) -> float:
    """`1 − (1 − base) × Π(1 − sᵢ)`, capped (§8).

    The form is the complement of the probability that *no* signal
    fired, which is why a signal strengthens a link instead of
    averaging with it: two independent observations of the same store
    are stronger than either one, and never weaker than the base.

    A `0` signal is a no-op and a signal of `1` would mean certainty —
    the cap turns that into `IDENTITY_CONFIDENCE_CAP`, deliberately, so
    no combination of signals can claim a fact.
    """
    complement = 1.0 - base
    for signal in signals:
        complement *= 1.0 - signal
    return min(IDENTITY_CONFIDENCE_CAP, 1.0 - complement)


def literal_match(
    write_hint: str | None, read_hint: str | None
) -> float | None:
    """§8's literal signal for a pair of hints.

    `LITERAL_EQUAL` when both sides name the same literal,
    `LITERAL_ONE_SIDE` when exactly one does, and `None` — no link —
    when they name different stores or when neither names one. The
    second `None` is §5: two call sites of the same family that name no
    store are not evidence of a shared store, and pairing them would
    link every call site of the family.

    Prefix containment (a writer's bucket, a reader's key prefix) is
    deliberately absent: it is a family-level decision, and it lands
    with the family that motivates it (B.3b-fs), not in the core.
    """
    if write_hint is None and read_hint is None:
        return None
    if write_hint is None or read_hint is None:
        return LITERAL_ONE_SIDE
    if write_hint != read_hint:
        return None
    return LITERAL_EQUAL


def code_context(write_site: WriteSite, read_site: ReadSite) -> float:
    """§8's code-context signal between a write site and a read site.

    The first rule that applies decides, in the ADR's order:

      - **same module** (`file_path` equal) → `CODE_CONTEXT_SAME_MODULE`;
      - **shared wrapper** — both files import the same local module, the
        `storage.py` / `s3_client.py` the store is reached through →
        `CODE_CONTEXT_SHARED_WRAPPER`;
      - **shared import path** — one file imports the other →
        `CODE_CONTEXT_SHARED_IMPORT`;
      - otherwise `0.0`.

    "Local" is what makes the wrapper rule mean anything: the facts
    hold the *manifest* paths a file imports, so two files that both
    `import os` share nothing this signal can see.

    The rules are ranked, not summed, and the ranking is the ADR's: the
    same module is the strongest thing a *code layout* can say, a
    shared client module is the next, and a one-way import is the
    weakest. The wrapper rule needs both files' facts and the import
    rule needs one side's, which is what makes a half-filled pair score
    on what it does know; the resolver fills both sides, so the
    distinction only shows up in a hand-built test.
    """
    if write_site.file_path == read_site.file_path:
        return CODE_CONTEXT_SAME_MODULE

    write_facts = write_site.facts
    read_facts = read_site.facts
    if (
        write_facts is not None
        and read_facts is not None
        and write_facts.local_imports & read_facts.local_imports
    ):
        return CODE_CONTEXT_SHARED_WRAPPER
    if (
        write_facts is not None
        and read_site.file_path in write_facts.local_imports
    ) or (
        read_facts is not None
        and write_site.file_path in read_facts.local_imports
    ):
        return CODE_CONTEXT_SHARED_IMPORT
    return 0.0


def module_facts(
    file_path: str,
    tree: ast.Module,
    path_by_dotted: Mapping[str, str],
) -> ModuleFacts:
    """The import-level facts of one module (`base.ModuleFacts`).

    `path_by_dotted` maps a dotted module name to the manifest path it
    names, which is what turns `from app import storage` into
    `app/storage.py` and keeps a name the scan did not discover (`os`,
    a third-party package) out of the facts. The resolver builds it once
    per scan (`resolver._path_by_dotted`).

    Candidate spellings from one statement: `import a.b` names `a.b`;
    `from a.b import c, d` can name `a.b`, `a.b.c` and `a.b.d` — the
    AST cannot say whether `c` is a submodule or an attribute, so both
    are offered and whichever the manifest holds is the one that
    counts. A relative import's base is derived from the file's own
    package (`from .storage import x` in `app/routes.py` names
    `app.storage`), because the AST records the level and not the
    package.
    """
    local: set[str] = set()
    for node in ast.walk(tree):
        for name in _imported_module_names(file_path, node):
            path = path_by_dotted.get(name)
            if path is not None:
                local.add(path)
    return ModuleFacts(file_path=file_path, local_imports=frozenset(local))


def _imported_module_names(file_path: str, node: ast.AST) -> list[str]:
    """The dotted names one `import` / `from ... import` can name."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []

    base = _import_base(file_path, node)
    names: list[str] = []
    if base:
        names.append(base)
    for alias in node.names:
        names.append(f"{base}.{alias.name}" if base else alias.name)
    return names


def _import_base(file_path: str, node: ast.ImportFrom) -> str:
    """The dotted module a `from ... import` reads from, absolute.

    An absolute import carries it; a relative one carries only a level,
    so the file's own directory supplies the rest: level 1 is the
    file's package, level 2 its parent, and so on. A level that climbs
    above the target root has no resolvable base and yields `""`, which
    leaves the alias names as the only candidates.
    """
    module = node.module
    if not node.level:
        return module or ""

    parts = file_path.split("/")[:-1]
    keep = len(parts) - (node.level - 1)
    package = ".".join(parts[:keep]) if keep > 0 else ""
    if not module:
        return package
    return f"{package}.{module}" if package else module


__all__ = [
    "BASE",
    "CODE_CONTEXT_SAME_MODULE",
    "CODE_CONTEXT_SHARED_IMPORT",
    "CODE_CONTEXT_SHARED_WRAPPER",
    "IDENTITY_CONFIDENCE_CAP",
    "LITERAL_EQUAL",
    "LITERAL_ONE_SIDE",
    "code_context",
    "combine_signals",
    "literal_match",
    "module_facts",
]
