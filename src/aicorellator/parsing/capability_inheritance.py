"""Capability inheritance from a scanned base class (B.10b, ADR-0020).

ADR-0009 derives a class's capabilities from the call sites in its own
body, so a subclass whose body only calls methods of its base carries
nothing — even when the base is the class that calls the LLM. After
ADR-0016 removed the class-name heuristics that was the only way a class
could become an `agent`, so the subclass disappeared from the graph.
ADR-0020 closes the gap:

    caps(cls) = caps(cls's own body) ∪ caps(cls's direct base)

`extract` is unchanged: it still produces one file's own-body
capabilities. This module is the second pass, over the whole manifest,
because a base class may live in another file:

    capabilities = {p: extract(abs_path, p) for p in python files}
    capabilities = apply_inheritance(capabilities, manifest)
    delta = normalize(make_context(..., capabilities=capabilities))

What inherits (ADR-0020 §1): the class's **single** direct base,
resolved by name against the classes the scan parsed — same file first,
then a unique class of that name elsewhere in the scan — and only the
base's **own-body** capabilities, exactly the set `extract` credited it.
Inheritance is one level, so a grandparent's capabilities never reach a
grandchild.

What does not (ADR-0020 §2, §4):

- a base outside the scanned tree (`langchain.agents.AgentExecutor`).
  The name resolves to nothing, so nothing is inherited: there is no
  name table, no import and no suffix match, only the `ClassDef` the
  scan already produced (ADR-0016, decision 7). A string base
  (`class A("B")`) is a forward reference and counts as no base — it is
  not `literal_eval`'d, because resolving it would widen the rule from
  `ClassDef.bases` to `ClassDef.bases`-or-its-string-value;
- a class with no base, or with more than one. Multiple inheritance is
  not supported, and neither "first base" nor "union of bases" is
  guessed: two bases inherit from neither (ADR-0020 §4);
- a base's *inherited* capabilities (one level, above);
- method-level inheritance. Only the class's `SymbolCapabilities` entry
  grows; method entries, in the subclass and in the base, are untouched;
- an override. A subclass that overrides the base's LLM-calling method
  still inherits: the AST sees the override, not what the override
  calls, and refusing would be a false negative on *enumeration* —
  worse than the accepted false positive on *kind* (ADR-0020 §3).

Cycle safety (ADR-0020 §5) is structural rather than a guard: every
inherited set is read from the *input* capabilities, so `A(B)` and
`B(A)` are two independent one-level unions, not a recursive walk.
There is no chain to follow and nothing to memoise, so the pass
terminates by construction, credits a capability to a class at most
once (a set union), and is deterministic — manifest order in, sets out.

The pass is pure: the input dict and its entries are never mutated. A
file with nothing to add is passed through as it is; a file with a
changed class gets a new `FileCapabilities` whose changed class entries
are new `SymbolCapabilities`.
"""


from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .capability_extractor import FileCapabilities, SymbolCapabilities
from .normalizer_helpers import parse_python_file

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ingestion.discovery import FileManifest


# A class is identified by (file, name): the name alone is not unique
# across a scan, and one file holds several classes.
ClassKey = tuple[str, str]


@dataclass(frozen=True)
class _ScannedClass:
    """One class the scan parsed, and what it inherits from (B.10b).

    `own_caps` is the *own body* of this class — the capabilities
    `extract` credited to it, never the set it holds after its own
    inheritance. That is what makes "one level" a property of the data
    flow here rather than a depth counter.
    """

    name: str
    file_path: str
    base_name: str | None       # the single resolvable-looking base
    own_caps: frozenset[str]


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def apply_inheritance(
    capabilities: dict[str, FileCapabilities],
    manifest: "FileManifest",
) -> dict[str, FileCapabilities]:
    """Add each class's direct base's capabilities to the class's own.

    Args:
        capabilities: what `extract` produced, keyed by the manifest's
                      relative path. Not mutated.
        manifest: the scan's file list — the pass parses its Python
                  files to read `ClassDef.bases`, because the base list
                  is an AST fact and not a `TSASymbol` field.

    Returns:
        A new dict with the same keys, in the same order. A class whose
        base is in the scanned tree has the base's own-body capabilities
        unioned into its entry; every other entry is the input's.
    """
    classes = _index_classes(capabilities, manifest)
    inherited = _inherited_caps(classes)
    return _extend(capabilities, inherited)


# ------------------------------------------------------------
# Index
# ------------------------------------------------------------

def _index_classes(
    capabilities: dict[str, FileCapabilities],
    manifest: "FileManifest",
) -> dict[ClassKey, _ScannedClass]:
    """Join the parsed classes with the capabilities `extract` produced.

    One parse per Python file per call, cached in `modules` the way
    `NormalizeContext.module_cache` caches per scan. A file that is not
    Python, does not parse, or has no capability entry contributes no
    classes — which is exactly "the scan did not produce this base", and
    therefore "nothing inherits from it" (ADR-0020 §2, and open
    question 4 for the file the scan read but could not parse).

    The first definition of a name in a file wins: `index_classes` keys
    on `(name, line_start)` precisely because a name can be defined
    twice, and `extract` emits one name-keyed entry per definition.
    """
    modules: dict[str, ast.Module | None] = {}
    index: dict[ClassKey, _ScannedClass] = {}

    for entry in manifest.files:
        if entry.language != "python":
            continue
        file_caps = capabilities.get(entry.path)
        if file_caps is None:
            continue
        module = _module_for(entry.path, entry.abs_path, modules)
        if module is None:
            continue
        known = {s.symbol_name for s in file_caps.symbols}
        for node in module.body:
            if not isinstance(node, ast.ClassDef):
                continue
            key = (entry.path, node.name)
            if key in index:
                continue
            if node.name not in known:
                # A class `extract` emitted no entry for: the two passes
                # would disagree about which entry to extend, so this
                # class inherits nothing and nothing inherits from it.
                continue
            index[key] = _ScannedClass(
                name=node.name,
                file_path=entry.path,
                base_name=_single_base_name(node),
                own_caps=frozenset(_own_caps(file_caps, node.name)),
            )

    return index


def _module_for(
    file_path: str,
    abs_path: Path,
    cache: dict[str, ast.Module | None],
) -> ast.Module | None:
    """Parsed module for a manifest file, cached for this call."""
    if file_path not in cache:
        cache[file_path] = parse_python_file(abs_path)
    return cache[file_path]


def _own_caps(file_caps: FileCapabilities, class_name: str) -> set[str]:
    """The capabilities `extract` credited to a class's own body.

    `FileCapabilities.symbols` is name-keyed — one entry per class, one
    per method — so this is a name lookup. A file that defines a method
    with a class's name is ambiguous; the first entry in file order
    wins, and `extract` appends a class before its own methods.
    """
    for symbol in file_caps.symbols:
        if symbol.symbol_name == class_name:
            return set(symbol.capabilities)
    return set()


def _single_base_name(node: ast.ClassDef) -> str | None:
    """The class's single base name, or None if it does not have one.

    ADR-0020 §1 is single-base and structural. No base, two bases, a
    qualified base (`mod.Base`), a call base (`Base()`) and a string
    base (`class A("B")`, a forward reference the AST stores as a
    `Constant`) all mean "inherit nothing" — none of them is a plain
    name that can be resolved against the scan's own classes.
    """
    if len(node.bases) != 1:
        return None
    base = node.bases[0]
    if isinstance(base, ast.Name):
        return base.id
    return None


# ------------------------------------------------------------
# Resolution
# ------------------------------------------------------------

def _inherited_caps(
    classes: dict[ClassKey, _ScannedClass],
) -> dict[ClassKey, frozenset[str]]:
    """Map every class to the capabilities its base contributes.

    Empty for a class with no base, an unresolvable base, or a base
    whose own body credits nothing. The union with the class's own
    capabilities happens in `_extend`, so a class that inherits nothing
    keeps exactly the entry `extract` produced.
    """
    by_name: dict[str, list[str]] = {}
    for file_path, name in classes:
        by_name.setdefault(name, []).append(file_path)

    inherited: dict[ClassKey, frozenset[str]] = {}
    for key, cls in classes.items():
        base_key = _resolve_base(cls, classes, by_name)
        if base_key is None:
            inherited[key] = frozenset()
        else:
            inherited[key] = classes[base_key].own_caps
    return inherited


def _resolve_base(
    cls: _ScannedClass,
    classes: dict[ClassKey, _ScannedClass],
    by_name: dict[str, list[str]],
) -> ClassKey | None:
    """The class's base, as a key into `classes`, or None.

    Same file first: that is the resolution a reader of the file gets,
    and it keeps a project-local `BaseAgent` from being shadowed by an
    unrelated class of the same name elsewhere in the scan.

    Then the cross-file case ADR-0020 §1 adds — the base resolves when
    exactly one *other* file in the scan defines that name. Two files
    defining the same name is ambiguous, and an ambiguous base inherits
    nothing: the same answer as an external base, and the only answer
    that does not guess between two unrelated classes.
    """
    base = cls.base_name
    if base is None or base == cls.name:
        # No base, or a class that names itself (`class A(A)`).
        return None
    if (cls.file_path, base) in classes:
        return (cls.file_path, base)
    candidates = [
        (file_path, base)
        for file_path in by_name.get(base, [])
        if file_path != cls.file_path
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


# ------------------------------------------------------------
# Output
# ------------------------------------------------------------

def _extend(
    capabilities: dict[str, FileCapabilities],
    inherited: dict[ClassKey, frozenset[str]],
) -> dict[str, FileCapabilities]:
    """Union the inherited capabilities into the class entries.

    A file is rebuilt only when one of its classes gains something; the
    rest of the file's entries — methods, functions, and classes that
    inherit nothing — are the input's own objects, unmodified.
    """
    gains: dict[str, dict[str, frozenset[str]]] = {}
    for (file_path, name), caps in inherited.items():
        if caps:
            gains.setdefault(file_path, {})[name] = caps

    out: dict[str, FileCapabilities] = {}
    for file_path, file_caps in capabilities.items():
        added = gains.get(file_path)
        if not added:
            out[file_path] = file_caps
            continue
        out[file_path] = FileCapabilities(
            file_path=file_caps.file_path,
            symbols=[
                _with_inherited(symbol, added)
                for symbol in file_caps.symbols
            ],
            module_capabilities=set(file_caps.module_capabilities),
        )
    return out


def _with_inherited(
    symbol: SymbolCapabilities,
    added: dict[str, frozenset[str]],
) -> SymbolCapabilities:
    """The class entry with its base's capabilities unioned in.

    Only the first entry of a name is extended — the class entry, since
    `extract` appends a class before its own methods — and `added` is
    consumed as it goes, so a method that shares a class's name keeps
    its own capabilities.
    """
    inherited = added.pop(symbol.symbol_name, None)
    if inherited is None:
        return symbol
    return SymbolCapabilities(
        symbol_name=symbol.symbol_name,
        file_path=symbol.file_path,
        capabilities=set(symbol.capabilities) | set(inherited),
    )
