"""Classifier runner — when Layer 1b runs, and what it is given (D.2).

`run_scan.py` calls this after the deterministic layers and before
`normalize()`, then merges the result into the `DetectionResult` it passes
as `framework_detection`. The runner owns the three things the classifier
itself has no business knowing.

**1. Which symbols are unclassified.** A symbol is classified when *any*
deterministic layer produced a node for it, so the runner asks the same
questions those layers ask:

  - framework detectors: is there a node for `(name, source_file)`? That
    pair is the identity `DetectorMergeStage` merges on, so it is the only
    identity "already classified" can mean.
  - capability and layout classification: does `classify_class` /
    `classify_method` return a kind for this symbol with its extracted
    capabilities? Those are the calls `ClassNodeStage` and `MethodNodeStage`
    make.

A name the deterministic layers *excluded* is not unclassified: they
decided, deliberately, that it gets no node (`excluded_class_name`,
`excluded_method_name`). Asking the model about it would invite the model
to create the node the deterministic layer refused. Those symbols are never
sent, in any mode.

**2. When the model is asked at all** — `scan.llm_classifier.mode`:

  - `off` — never called. Nothing is sent, no request is made, the cache is
    not opened. This is the default: ADR-0016 makes the layer opt-in because
    it is expensive.
  - `auto` — called for the unclassified symbols only.
  - `required` — called for **every** symbol (ADR-0016, §Classification
    layers: "invokes it for every symbol"), because "required" is a
    statement about the whole corpus, not just the gap. A node is still
    created only where the deterministic layers produced none: **the
    deterministic answer wins on conflict.** Where both answered, the
    deterministic node stands and the model's is dropped. That is not a
    preference, it is the merge's mechanics — nodes in
    `framework_detection` *replace* nodes sharing their `(name,
    source_file)` (`DetectorMergeStage`), so an LLM node for an
    already-classified symbol would overwrite a deterministic one. The
    filter in `run_classifier` is what keeps that from happening.

    `required` also fails the scan when a symbol is still unclassified
    afterwards — that is what the mode is for, and it is why the mode is not
    the default. `auto` never fails a scan over a symbol the model could not
    resolve: a non-deterministic failure must not be fatal by default.

**3. What `unclassified.action` does with what is left.** The action is the
policy for symbols that end up without a node:

  - `ask` — a warning naming the count and a sample. D.2 deliberately does
    not prompt: a scan is non-interactive (CI, a pipeline, a scheduler), and
    a scan that blocks on `input()` is a scan that hangs. "Ask" is recorded
    as a warning for a human to read afterwards. The BACKLOG's
    `interactive.py` is not part of D.2.
  - `skip` — an info line.
  - `llm` — nothing to do: the classifier *is* the answer to unclassified
    symbols, so this action only warns when `mode: off` makes it
    unsatisfiable.
  - `error` — a `ClassifierError`, exactly like `mode: required`.

The action is evaluated in every mode, `off` included (there, every
unclassified symbol is unresolved): it is a statement about the outcome,
not about the layer.

**Deterministic wins, in one sentence:** the model may only fill gaps. It
can add a node where there was none; it can never change, replace or
contradict a node a deterministic layer produced.

## What the runner needs from its caller

`manifest`, `tsa_output`, `capabilities` and `framework_detection` — the
same four inputs `normalize()` gets — plus the scan policy, the config path
(for `llm_classifier_model`) and a cache path. The cache lives in the
workspace's `cache/` directory, so `--clean` clears it with the rest of the
state. Tests inject `llm_call` and get no network access at all.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from ...ingestion.discovery import FileManifest
from ..capability_extractor import FileCapabilities
from ..classification import (
    classify_class,
    classify_method,
    excluded_class_name,
    excluded_method_name,
)
from ..framework_detectors import DetectionResult
from ..models import TSASymbol, TSAOutput
from ..normalizer_helpers import caps_for_symbol
from ...scan_config import ScanConfig
from .cache import ClassifierCache
from .classifier import (
    FRAMEWORK,
    Classifier,
    ClassifierError,
    LLMCall,
    UnclassifiedSymbol,
)
from .config import load_classifier_config


log = logging.getLogger(__name__)


# A symbol's identity across the two producers: the pair
# `DetectorMergeStage` merges on. Named here so the runner and the filter
# cannot drift apart from the stage.
SymbolKey = tuple[str, str]


def run_classifier(
    *,
    manifest: FileManifest,
    tsa_output: TSAOutput,
    capabilities: dict[str, FileCapabilities],
    framework_detection: DetectionResult,
    scan: ScanConfig,
    config_path: str | Path,
    cache_path: str | Path | None = None,
    host_id: str = "localhost",
    llm_call: LLMCall | None = None,
) -> DetectionResult:
    """Run Layer 1b and return the nodes the model justified.

    Returns an empty `DetectionResult` for `mode: off`. The caller merges
    whatever comes back into `framework_detection` either way, so it does
    not have to branch on the mode.

    Raises:
        ClassifierConfigError: the mode asks for the layer and the config
            has no usable `llm_classifier_model` section.
        ClassifierError: `mode: required` (or `unclassified.action: error`)
            and symbols are still unclassified afterwards.
    """
    mode = scan.llm_classifier.mode
    action = scan.unclassified.action

    detector_kinds = _detector_kinds(framework_detection)
    classified = _classified_keys(tsa_output, capabilities, detector_kinds)
    unclassified = _pending_symbols(tsa_output, classified)

    if mode == "off":
        if action == "llm":
            log.warning(
                "scan policy: unclassified.action='llm' needs "
                "llm_classifier.mode='auto' or 'required' — the mode is "
                "'off', so no symbol is sent to the model"
            )
        _report_leftovers(unclassified, mode, action)
        return DetectionResult(framework=FRAMEWORK)

    config = load_classifier_config(config_path)
    cache = ClassifierCache(cache_path) if cache_path else None
    classifier = Classifier(
        config, cache=cache, llm_call=llm_call, host_id=host_id,
    )

    merged = DetectionResult(framework=FRAMEWORK)
    sent = 0
    dropped = 0
    cached_entries = 0
    try:
        for entry in manifest.files:
            if entry.language != "python":
                continue
            symbols = _symbols_for_file(
                entry,
                tsa_output,
                capabilities,
                detector_kinds,
                classified,
                include_classified=(mode == "required"),
            )
            if not symbols:
                continue
            sent += len(symbols)
            result = classifier.classify_file(entry.path, symbols)
            for node in result.nodes:
                if _key(node) in classified:
                    # The deterministic answer wins on conflict — see the
                    # module docstring. Only reachable in `required`, where
                    # classified symbols are sent too.
                    dropped += 1
                    continue
                merged.nodes.append(node)
    finally:
        if cache is not None:
            cached_entries = cache.count()
            cache.close()

    resolved = {_key(node) for node in merged.nodes}
    log.info(
        "llm_classifier: mode=%s model=%s — %d symbol(s) sent, %d node(s) "
        "created, %d dropped in favour of the deterministic layers, "
        "%d cached",
        mode, config.model, sent, len(merged.nodes), dropped, cached_entries,
    )
    _report_leftovers(unclassified - resolved, mode, action)
    return merged


# ============================================================
# What the deterministic layers decided
# ============================================================

def _key(node) -> SymbolKey:
    """A node's identity for the merge: `(name, source_file)`.

    The same pair `DetectorMergeStage` keys on. A node without
    `source_file` cannot be matched against anything, and an empty string
    is what the stage would compute for it too.
    """
    return (node.name, node.attributes.get("source_file", ""))


def _detector_kinds(detection: DetectionResult) -> dict[SymbolKey, str]:
    """Framework detector nodes, indexed by their merge identity."""
    return {_key(node): node.kind for node in detection.nodes}


def _classified_keys(
    tsa_output: TSAOutput,
    capabilities: dict[str, FileCapabilities],
    detector_kinds: dict[SymbolKey, str],
) -> set[SymbolKey]:
    """Every symbol the deterministic layers produced a node for.

    Two sources, because there are two ways a symbol gets a node: a
    detector emitted one (keyed by `(name, source_file)`), or the
    capability-and-layout predicates returned a kind — the same calls
    `ClassNodeStage` and `MethodNodeStage` make, with the same capability
    lookup.
    """
    classified: set[SymbolKey] = set(detector_kinds)

    for symbol in tsa_output.symbols:
        if symbol.kind not in ("class", "method"):
            continue
        caps = caps_for_symbol(capabilities, symbol.file_path, symbol.name)
        if symbol.kind == "class":
            result = classify_class(
                symbol.name, symbol.file_path, capabilities=caps
            )
        else:
            result = classify_method(
                symbol.name, symbol.file_path, symbol.class_name,
                capabilities=caps,
            )
        if result is not None:
            classified.add((symbol.name, symbol.file_path))

    return classified


def _pending_symbols(
    tsa_output: TSAOutput, classified: set[SymbolKey]
) -> set[SymbolKey]:
    """Symbols with no deterministic node — the layer's whole reason to run.

    Withheld names are not in here: the deterministic layers decided they
    get no node, and that decision is not the model's to revisit.
    """
    pending: set[SymbolKey] = set()
    for symbol in tsa_output.symbols:
        if symbol.kind not in ("class", "method"):
            continue
        if _is_excluded(symbol):
            continue
        key = (symbol.name, symbol.file_path)
        if key not in classified:
            pending.add(key)
    return pending


def _is_excluded(symbol: TSASymbol) -> bool:
    """True if the deterministic layers withhold a node for this symbol."""
    if symbol.kind == "class":
        return excluded_class_name(symbol.name)
    return excluded_method_name(symbol.name)


# ============================================================
# Building the classifier's input
# ============================================================

def _symbols_for_file(
    entry,
    tsa_output: TSAOutput,
    capabilities: dict[str, FileCapabilities],
    detector_kinds: dict[SymbolKey, str],
    classified: set[SymbolKey],
    include_classified: bool,
) -> list[UnclassifiedSymbol]:
    """The classifier's input for one file.

    `include_classified` is `mode: required`, which sends every symbol and
    keeps only the answers for the ones the deterministic layers missed.

    Returns [] when the file cannot be read or parsed: the prompt is built
    from the symbol's text, so a file the AST rejects has nothing to send.
    TSA tolerates syntax this parser does not, so that is a real case rather
    than a theoretical one — and the symbols stay unclassified, which
    `unclassified.action` then reports.
    """
    symbols = [s for s in tsa_output.symbols if s.file_path == entry.path]
    if not symbols:
        return []

    parsed = _parse(entry.abs_path)
    if parsed is None:
        return []
    module, lines = parsed
    index = _index_module(module)
    imports = _collect_imports(module)

    built: list[UnclassifiedSymbol] = []
    for symbol in symbols:
        if symbol.kind not in ("class", "method"):
            continue
        if _is_excluded(symbol):
            continue
        key = (symbol.name, symbol.file_path)
        if not include_classified and key in classified:
            continue
        definition = _find_definition(index, symbol)
        if definition is None:
            log.debug(
                "llm_classifier: no AST definition for %s in %s (skipped)",
                symbol.name, entry.path,
            )
            continue
        built.append(UnclassifiedSymbol(
            name=symbol.name,
            kind=symbol.kind,
            file_path=symbol.file_path,
            line_start=symbol.line_start,
            source=_symbol_source(lines, definition),
            imports=imports,
            class_name=symbol.class_name,
            has_tool_decorator=_has_tool_decorator(definition),
            parent_kind=_parent_kind(
                symbol.class_name, symbol.file_path, capabilities,
                detector_kinds,
            ),
        ))
    return built


def _parse(path: Path) -> tuple[ast.Module, list[str]] | None:
    """Read and parse a file: `(module, lines)`, or None on any failure.

    The lines are kept because the prompt shows the symbol's text, sliced
    the way `tools/bench_classifier.py` sliced it. Reading the file here
    duplicates a read the normalizer performs later; a per-scan source cache
    would be a larger change than this layer is worth, and the detector
    runner already reads every Python file the same way.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return ast.parse(text, filename=str(path)), text.splitlines()
    except (OSError, SyntaxError, ValueError) as e:
        log.debug("llm_classifier: cannot parse %s: %s", path, e)
        return None


def _index_module(
    module: ast.Module,
) -> dict[tuple[str, str | None, str], list[ast.AST]]:
    """Index definitions by `(name, enclosing class, kind)`.

    The kind is TSA's vocabulary (`"class"` / `"method"`), so a TSA symbol
    indexes directly. The value is a list because a name can be reused — a
    module-level function and a class may share one, and a file may define
    the same method in two classes — so the index never silently loses a
    definition; `_find_definition` then picks by line.
    """
    index: dict[tuple[str, str | None, str], list[ast.AST]] = {}

    def walk(node: ast.AST, class_name: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                index.setdefault((child.name, None, "class"), []).append(child)
                walk(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                index.setdefault(
                    (child.name, class_name, "method"), []
                ).append(child)
                walk(child, class_name)

    walk(module, None)
    return index


def _find_definition(
    index: dict[tuple[str, str | None, str], list[ast.AST]],
    symbol: TSASymbol,
) -> ast.AST | None:
    """The AST definition for a TSA symbol, or None.

    TSA is an external tool (`uvx`): its `line_range` and `ast`'s `lineno`
    agree in practice but are not the same promise, and a decorated function
    may be reported from the decorator's line or from the `def` line. So the
    lookup is by `(name, enclosing class, kind)` first — the identity both
    sides have — and then by nearest line, which picks the right definition
    when a name is reused.
    """
    container = symbol.class_name if symbol.kind == "method" else None
    candidates = index.get((symbol.name, container, symbol.kind))
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda node: abs(getattr(node, "lineno", 0) - symbol.line_start),
    )


def _symbol_source(lines: list[str], node: ast.AST) -> str:
    """The symbol's text: its first line through its last.

    Decorators are deliberately outside the slice — the benchmark's
    extractor sliced the same way, and the prompt was measured on bodies. The
    decorator fact travels separately, as `has_tool_decorator`, and limiter 3
    is its only reader.
    """
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", None) or start
    return "\n".join(lines[start - 1:end])


def _collect_imports(module: ast.Module) -> list[str]:
    """The file's imports, in the format the benchmark collected them.

    Module names only (`subprocess`, `finbot.agents.base`), sorted and
    deduplicated — not the `from x import y` statements. Same shape as
    `tools/bench_classifier.py._collect_imports`, because the prompt was
    measured with it.
    """
    found: list[str] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return sorted(set(found))


def _has_tool_decorator(node: ast.AST) -> bool:
    """True if the symbol is declared with a `tool` decorator.

    A declaration check, not a name heuristic (ADR-0016): it reads a
    decorator the code spells out, and it only ever *marks* a node
    (limiter 3) — it never grants a kind and never withholds one. `@tool`,
    `@mcp.tool`, `@mcp.tool()` and `@server.tool(...)` all match; the test is
    the decorator's last dotted segment, the same rule
    `framework_detectors/langchain.py` uses.
    """
    for decorator in getattr(node, "decorator_list", []):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            if target.attr == "tool":
                return True
        elif isinstance(target, ast.Name) and target.id == "tool":
            return True
    return False


def _parent_kind(
    class_name: str | None,
    file_path: str,
    capabilities: dict[str, FileCapabilities],
    detector_kinds: dict[SymbolKey, str],
) -> str | None:
    """The enclosing class's kind, as the deterministic layers decided it.

    Detector output first (it is authoritative for kind, `detector_merge`),
    then the capability-and-layout predicate `classify_class` — the same call
    `ClassNodeStage` makes. None means "no deterministic node", which is what
    limiters 1 and 2 need to know.
    """
    if not class_name:
        return None
    kind = detector_kinds.get((class_name, file_path))
    if kind:
        return kind
    result = classify_class(
        class_name,
        file_path,
        capabilities=caps_for_symbol(capabilities, file_path, class_name),
    )
    return result.kind if result else None


# ============================================================
# Reporting and enforcement
# ============================================================

def _report_leftovers(
    unresolved: set[SymbolKey],
    mode: str,
    action: str,
) -> None:
    """Report — or fail on — the symbols that ended up without a node.

    `mode: required` and `action: error` are the same demand: do not come
    back with a gap silently. Everything else reports and moves on, because
    a non-deterministic layer that fails a scan by default is a layer nobody
    leaves switched on.
    """
    if not unresolved:
        return

    count = len(unresolved)
    sample = ", ".join(
        f"{name} ({file})" for name, file in sorted(unresolved)[:5]
    )
    if count > 5:
        sample = f"{sample}, …"

    if mode == "required" or action == "error":
        raise ClassifierError(
            f"{count} symbol(s) left unclassified: {sample} "
            f"(llm_classifier.mode={mode!r}, unclassified.action={action!r})"
        )

    if action == "ask":
        if mode == "off":
            log.warning(
                "llm_classifier: %d symbol(s) left unclassified: %s — set "
                "llm_classifier.mode to 'auto' to have the LLM classifier "
                "decide them",
                count, sample,
            )
        else:
            log.warning(
                "llm_classifier: %d symbol(s) the model could not classify: "
                "%s — they stay without a node",
                count, sample,
            )
        return

    log.info(
        "llm_classifier: %d symbol(s) left unclassified: %s", count, sample
    )
