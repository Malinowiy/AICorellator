"""Store-identity fallback — the LLM's hint for an unresolved site (ADR-0023).

ADR-0021 §5 is right and silent: a `Bucket=var` or an `open(path)` names
no store the AST can see, so it produces no store, no edges and no group.
The gap is invisible — the analysis cannot distinguish "no store" from "a
store the AST could not name". This module is the last resort for that
gap, and it is opt-in (`llm_fallback.mode`, `off` by default).

Where it runs. **After** the resolver's families and the upload
attribution pass, in the store resolver stage (19), on the sites those
producers could not name. It is not a family (a family runs per file,
before the resolver, and sees one module's AST) and it is not a resolver
change (the resolver does not know it exists — ADR-0023 §6).

What it fills, exactly:

    families' WriteSite / ReadSite with identity_hint=None
      and in the writer's scope (ADR-0022 §1)
        → LLM (mode auto / required) → identity_hint, or null
          → a store (family from the site, hint from the answer,
             identity_source='llm') + writes_to / reads_from
          → null, or an unusable answer: no store, no edges
             (a warning in `auto`; a `FallbackError` in `required`)

A site the deterministic producers named is never sent (the deterministic
answer wins, ADR-0016), and neither is a site outside the upload writers'
scope: with no upload site at all, nothing is in scope and the fallback
is a no-op — which is why it changes nothing on the corpora today.

The store and its edges. The store is `store_node_id(family, hint)` — the
same id the resolver would have built — with `identity_source='llm'` in
its attributes. The edges are `writes_to` / `reads_from`, `structural`,
`observed_by=["ast"]` (the site is AST; the LLM added the name) and no
`data_flow_confirmed` (ADR-0016). Their `identity_confidence` is
`confidence.LLM_IDENTITY_BASE` (0.2), with the model's own confidence
recorded beside it as `identity_confidence_llm` — two probabilities are
not folded into one number (ADR-0023 §5). 0.2 is below §9's floor, so
these links exist only because of §5's special floor
(`confidence.WEAK_JOIN_FLOOR_LLM`, applied by `graph.query`), and the
group they form is **weak**: real evidence, out of the default
enumeration, `include_weak=True` to see it.

Both ends are resolved the way the resolver resolves them
(`resolver._writer_node_id` / `resolver._read_target_id`, reused rather
than restated): a write site with no component node, and a read site with
no `untrusted_source`, earn no edge. The private names are imported on
purpose — it is the same question, and a copy would drift.

The answer is validated (`parse_answer`) against the prompt's schema: a
non-JSON, non-object, non-string-hint or out-of-range-confidence answer
is dropped and not cached. A `null` hint is a **valid answer** and is
cached; the site stays unresolved, which is the truth about the code.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping, Sequence

import requests

from ...analysis.client import LLMResponseError, parse_json_response
from ...graph.models import Edge, Node
from ...parsing.llm_classifier.classifier import LLMCall
from ..confidence import LLM_IDENTITY_BASE, LLM_IDENTITY_SOURCE
from ..context.upload.attribution import _in_scope
from ..context.upload.base import UploadSite
from ..families.base import ReadSite, WriteSite
from ..resolver import (
    STRUCTURAL,
    _component_index,
    _graph_ids,
    _read_target_id,
    _source_index,
    _writer_node_id,
    store_node_id,
)
from .cache import FallbackCache, identity_hash
from .config import FallbackConfig, load_fallback_config
from .prompt import (
    IDENTITY_SCHEMA,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)


log = logging.getLogger(__name__)


if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...parsing.ast_normalizer import GraphDelta
    from ...parsing.normalizer_context import NormalizeContext
    from ...scan_config import ScanConfig


#: How many lines of the enclosing definition are sent to the model. A
#: window, not a file: the site's own body and its neighbourhood are the
#: evidence, and a whole file would cost tokens for context the model does
#: not need. The cap is part of the cache key (it is inside the window
#: text), so raising it re-asks every site.
WINDOW_LINES = 60

#: How many of the file's import statements are sent. Imports are a hint
#: about *which technology* the file reaches, and the first few name it.
IMPORT_LINES = 40

#: How the answer is constrained on the wire, strongest first. The runner
#: starts at `config.response_format` and steps down when the server
#: refuses a form (HTTP 400), the probe D.2's classifier does.
RESPONSE_FORMATS = ("schema", "object", "none")


class FallbackError(Exception):
    """`mode: required` and a site the fallback could not resolve.

    Raised only under `required`: the analyst asked for the layer, and a
    gap must not pass silently. In `auto` the same failure is a warning
    and the site stays unresolved.
    """


@dataclass(frozen=True)
class FamilySite:
    """One site to consider, with the family that reported it.

    The site itself does not know its family — the resolver iterates the
    families, and a `WriteSite` is one module's call site — so the pair is
    what the fallback needs to name a store. `family` is the technology
    (`fs`, `s3`, `db`), never the model's answer: the prompt's schema has
    no family field, so an answer can pick a wrong hint but cannot invent
    a technology.
    """

    family: str
    site: WriteSite | ReadSite


@dataclass(frozen=True)
class StoreIdentity:
    """A validated answer: a hint (or `null`), a confidence, a reason.

    Constructed by `parse_answer`, so every instance is already inside the
    prompt's schema.
    """

    identity_hint: str | None
    confidence: float = 0.0
    reasoning: str = ""


# ============================================================
# Entry point
# ============================================================

def run(
    *,
    sites: Iterable[FamilySite],
    delta: "GraphDelta",
    upload_sites: Sequence[UploadSite],
    imports_by_file: Mapping[str, list[str]],
    ctx: "NormalizeContext",
    scan: "ScanConfig",
    config_path: str | Path,
    cache_path: str | Path | None = None,
    host_id: str = "localhost",
    llm_call: LLMCall | None = None,
) -> tuple[list[Node], list[Edge]]:
    """Fill the identity gaps and return `(stores, edges)`.

    The stage appends both to the delta (`store_resolver` merges them by
    id, the way it merges the resolver's own output). A tuple rather than
    a mutation: the runner does not know whether the caller wants the
    stores, and the delta is the caller's.

    `sites` is every family's sites with the family that reported them —
    the stage collects them, because "which families run" is the
    resolver's question and not this module's. The filter is here:
    `identity_hint is None` **and** in the writer's scope (ADR-0022 §1,
    `attribution._in_scope`). A deterministic site is never sent.

    `delta` and `ctx` are what the two ends are resolved against: the
    component a write site lives in, the `untrusted_source` a read site
    reads, and the parsed module the code window comes from.

    Returns `([], [])` for `mode: off` (before anything else runs, so the
    pipeline is byte-identical to the resolver's output), and for a scan
    whose sites are all resolved or out of scope.

    Raises:
        FallbackConfigError: the mode asks for the layer and the config
            has no usable `llm_fallback_model` section.
        FallbackError: `mode: required` and a site the fallback could not
            resolve (no answer, or a `null` hint).
    """
    mode = scan.llm_fallback.mode
    if mode == "off":
        return [], []

    # The model is loaded before the filter, not after it: the mode is the
    # analyst's statement that the layer runs, and a section that is
    # missing has to be an error whether or not this particular target
    # happens to have a gap (D.2's rule, and the reason it is loadable at
    # all: a policy that asks for a layer nobody configured is worse than
    # one that refuses to load).
    config = load_fallback_config(config_path)

    pending = _pending_sites(sites, upload_sites, imports_by_file)
    if not pending:
        log.debug("llm_fallback: no unresolved site in a writer's scope")
        return [], []

    cache = FallbackCache(cache_path) if cache_path else None
    caller = _Caller(config, cache, llm_call)
    host_id_, edge_id, _ = _graph_ids()
    components = _component_index(delta)
    by_line, by_type = _source_index(delta)

    stores: dict[str, Node] = {}
    edges: dict[str, Edge] = {}
    named = 0
    try:
        for family_site in pending:
            family, site = family_site.family, family_site.site
            inputs = _prompt_inputs(site, ctx)
            if inputs is None:
                # No parsed module, no window: nothing to ask about. Not a
                # gap the scan should fail on (`required` asks the layer to
                # name what it can, and an unparseable file names nothing).
                log.debug(
                    "llm_fallback: %s:%s has no parsed module — skipped",
                    site.file_path, site.line,
                )
                continue

            answer = caller.identity_for(inputs)
            if answer is None or answer.identity_hint is None:
                _report_gap(mode, family, site, _NO_NAME)
                continue

            named += 1
            edge = _site_edge(
                family_site,
                answer,
                host_id=host_id_,
                edge_id=edge_id,
                ctx=ctx,
                components=components,
                by_line=by_line,
                by_type=by_type,
            )
            if edge is None:
                # The model named a store, and one end of the link is not
                # in the delta. No edge, and no node either: the store
                # exists because a link points at it, and the join is by
                # store id, so a store nothing points at is a node the
                # report cannot show and the join cannot use.
                _report_gap(mode, family, site, _NO_END)
                continue

            node = _store_node(family, answer, host_id_)
            stores.setdefault(node.id, node)
            edges.setdefault(edge.id, edge)
    finally:
        if cache is not None:
            cache.close()

    log.info(
        "llm_fallback: mode=%s model=%s — %d site(s) sent, %d named, "
        "%d store(s), %d edge(s)",
        mode, config.model, len(pending), named, len(stores), len(edges),
    )
    return list(stores.values()), list(edges.values())


# ============================================================
# Which sites are sent
# ============================================================

def _pending_sites(
    sites: Iterable[FamilySite],
    upload_sites: Sequence[UploadSite],
    imports_by_file: Mapping[str, list[str]],
) -> list[FamilySite]:
    """The sites the fallback may ask about, in a deterministic order.

    Two filters, both required (ADR-0023 §1):

      - `identity_hint is None` — a site that names its own store is the
        deterministic answer's, and the fallback never re-names it.
      - in the writer's scope — the upload writers' scope rule of ADR-0022
        §1, applied to the site's file (`attribution._in_scope`, reused).
        A scan with no upload site has no scope, so nothing is sent: the
        fallback is inert on a target with no upload, which is the honest
        reading of "the store identity gap the upload path opened".

    Deduplicated by `(family, file_path, line, kind)` — the kind is the
    site's class, so a `'r+'` call site, which is one write and one read
    (the `fs` family's rule), keeps both — and sorted by file, line and
    family, so the call order — and therefore the cache's contents and
    the log — is a property of the code and not of the walk.
    """
    seen: set[tuple[str, str, int, str]] = set()
    pending: list[FamilySite] = []
    for family_site in sites:
        site = family_site.site
        if site.identity_hint is not None:
            continue
        if not _in_scope(site.file_path, upload_sites, imports_by_file):
            continue
        key = (family_site.family, site.file_path, site.line,
               type(site).__name__)
        if key in seen:
            continue
        seen.add(key)
        pending.append(family_site)
    pending.sort(key=lambda fs: (fs.site.file_path, fs.site.line, fs.family))
    return pending


# ============================================================
# The prompt's inputs
# ============================================================

@dataclass(frozen=True)
class _PromptInputs:
    """One site's prompt inputs, and the cache key they hash to.

    `call_shape` is the dotted target, `window` the enclosing definition's
    code (capped at `WINDOW_LINES`), `imports` the file's import
    statements (capped at `IMPORT_LINES`). `key` is `identity_hash` over
    exactly those plus the file path, so a change in any of them is a
    different question and a cache miss.
    """

    call_shape: str
    window: str
    file_path: str
    imports: list[str]
    key: str


def _prompt_inputs(
    site: WriteSite | ReadSite, ctx: "NormalizeContext"
) -> _PromptInputs | None:
    """The prompt inputs for one site, or None when the file is unreadable.

    The module comes from `ctx.module_for`, the scan-wide parse cache the
    resolver and the attribution have already filled, so a site costs no
    second parse.
    """
    module = ctx.module_for(site.file_path)
    if module is None:
        return None

    call_shape = _call_shape(site)
    window = _window(module, site.line)
    imports = _imports(module)
    return _PromptInputs(
        call_shape=call_shape,
        window=window,
        file_path=site.file_path,
        imports=imports,
        key=identity_hash(call_shape, window, site.file_path, imports),
    )


def _call_shape(site: WriteSite | ReadSite) -> str:
    """The dotted name of the call the site was reported for.

    `s3.put_object`, `open`, `pathlib.Path.write_text`. `ast.unparse` is
    exact when it can render the expression and raises on a node it cannot
    (a hand-built `ast.Call` in a test, a shape the parser never
    produced); the fallback then says `<call>`, which is honest — the
    family recognized a call this module cannot render.
    """
    try:
        return ast.unparse(site.call_site.func)
    except (ValueError, RecursionError):  # pragma: no cover - defensive
        return "<call>"


def _window(module: ast.Module, line: int) -> str:
    """The code around `line`: the innermost definition that spans it.

    A function's body is the evidence for the parameter names and the
    docstring; a class or the module body is the fallback when no function
    spans the line (a module-level call site). The innermost definition
    wins, which is the reading `resolver._enclosing_names` makes for the
    writer, and the text is capped at `WINDOW_LINES`.
    """
    best: ast.AST | None = None
    for node in ast.walk(module):
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        end = getattr(node, "end_lineno", None) or node.lineno
        if node.lineno <= line <= end:
            if best is None or node.lineno > getattr(best, "lineno", 0):
                best = node

    target = best if best is not None else module
    try:
        text = ast.unparse(target)
    except (ValueError, RecursionError):  # pragma: no cover - defensive
        return ""
    lines = text.splitlines()
    if len(lines) > WINDOW_LINES:
        lines = [*lines[:WINDOW_LINES], "... (truncated)"]
    return "\n".join(lines)


def _imports(module: ast.Module) -> list[str]:
    """The file's import statements, in source order, capped.

    Not the dotted names but the statements as written: an import's own
    spelling (`from app import storage`) is what tells the model which
    local module a wrapper lives in. Sorted by the AST's own order, which
    is source order.
    """
    statements: list[str] = []
    for node in module.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        try:
            statements.append(ast.unparse(node))
        except (ValueError, RecursionError):  # pragma: no cover - defensive
            continue
        if len(statements) >= IMPORT_LINES:
            break
    return statements


# ============================================================
# Asking the model
# ============================================================

class _Caller:
    """One run's model call, cache and accepted answer form.

    Holds the three things that are per-run state rather than per-site:
    the cache connection, the injected `llm_call` (so no test needs a
    server) and the answer form the server last accepted.
    """

    def __init__(
        self,
        config: FallbackConfig,
        cache: FallbackCache | None,
        llm_call: LLMCall | None,
    ) -> None:
        self.config = config
        self.cache = cache
        self._llm_call = llm_call
        # Starts at the configured form and steps down as the server
        # refuses them — see `_ask`.
        self._response_format = config.response_format

    def identity_for(self, inputs: _PromptInputs) -> StoreIdentity | None:
        """The validated answer for one site: cache first, then the model.

        A `None` return means "no answer" — a transport failure, a refused
        format, or a malformed answer. None of those are cached, so a
        transient failure does not become permanent. A `null` *hint* is an
        answer and **is** cached.
        """
        if self.cache is not None:
            cached = self.cache.get(
                inputs.key, PROMPT_VERSION, self.config.model
            )
            if cached is not None:
                log.debug(
                    "llm_fallback: cache hit for %s:%s",
                    inputs.file_path, inputs.call_shape,
                )
                return StoreIdentity(
                    identity_hint=cached.identity_hint,
                    confidence=cached.confidence,
                    reasoning=cached.reasoning,
                )

        user_prompt = build_user_prompt(
            inputs.call_shape, inputs.window, inputs.file_path,
            inputs.imports,
        )
        text = self._ask(user_prompt)
        if text is None:
            return None

        answer = parse_answer(text)
        if answer is None:
            log.warning(
                "llm_fallback: unusable answer for %s in %s — the site is "
                "left unresolved and the answer is not cached",
                inputs.call_shape, inputs.file_path,
            )
            return None

        if self.cache is not None:
            self.cache.put(
                inputs.key, PROMPT_VERSION, self.config.model,
                identity_hint=answer.identity_hint,
                confidence=answer.confidence,
                reasoning=answer.reasoning,
            )
        return answer

    def _ask(self, user_prompt: str) -> str | None:
        """The assistant's text for one site, or None.

        An injected `llm_call` wins, so no test needs a server. The real
        path steps the response format down when a server refuses it: one
        that rejects `json_schema` may accept `json_object`, or nothing at
        all. The answer is validated against the schema either way, so a
        weaker format costs strictness on the wire, never correctness.
        """
        if self._llm_call is not None:
            return self._llm_call(SYSTEM_PROMPT, user_prompt)

        start = (
            RESPONSE_FORMATS.index(self._response_format)
            if self._response_format in RESPONSE_FORMATS
            else 0
        )
        for form in RESPONSE_FORMATS[start:]:
            text, refused = self._post(user_prompt, form)
            if refused:
                log.warning(
                    "llm_fallback: %s refused response_format=%r "
                    "(HTTP 400), trying the next form",
                    self.config.model, form,
                )
                continue
            self._response_format = form
            return text
        return None

    def _post(
        self, user_prompt: str, response_format: str
    ) -> tuple[str | None, bool]:
        """One completion request. Returns `(text, format_refused)`.

        `format_refused` is True only for HTTP 400 — the one status that
        means "the request shape is wrong" rather than "the endpoint is
        unavailable". The probe is D.2's, restated here rather than
        imported: `llm_classifier.classifier` does not expose it, and this
        step must not change that module to reach it.
        """
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload: dict = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        if response_format == "schema":
            payload["response_format"] = IDENTITY_SCHEMA
        elif response_format == "object":
            payload["response_format"] = {"type": "json_object"}

        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.config.timeout,
            )
        except requests.Timeout:
            log.warning(
                "llm_fallback: timeout after %ds on %s",
                self.config.timeout, url,
            )
            return None, False
        except requests.RequestException as e:
            log.warning("llm_fallback: request failed: %s", e)
            return None, False

        if response.status_code == 400:
            return None, True
        if response.status_code != 200:
            log.error(
                "llm_fallback: HTTP %d from %s: %s",
                response.status_code, url, response.text[:200],
            )
            return None, False

        try:
            data = response.json()
            return data["choices"][0]["message"]["content"], False
        except (ValueError, KeyError, IndexError, TypeError) as e:
            log.warning("llm_fallback: unusable response shape: %s", e)
            return None, False


# ============================================================
# Answer validation
# ============================================================

def parse_answer(text: str) -> StoreIdentity | None:
    """Validate one assistant message into an answer, or None.

    The schema is the whole contract: a JSON object carrying
    `identity_hint` (a string or `null`), and a `confidence` in 0.0–1.0
    when it carries one. Anything else — non-JSON, a non-object, a missing
    or non-string hint, a confidence outside the range — returns None,
    which the caller turns into "left unresolved" and does not cache.

    A **blank** hint is `null`: a name that is only whitespace names
    nothing, and the honest reading of an empty string is what the
    prompt's `null` already means. `null` itself passes through: it is a
    valid answer, not a failure. An **absent** `identity_hint` key is not
    a null one — the schema requires the key, and a response without it
    answered nothing.

    A function rather than a method: it is the layer's whole contract in
    one readable place, and the piece tests should attack.
    """
    try:
        data = parse_json_response(text)
    except LLMResponseError:
        return None
    if not isinstance(data, dict):
        return None

    # The hint is the answer: the schema requires the key, and a response
    # without it answered nothing. Its *value* may be `null` — that is the
    # prompt's "the code names no store" — but an absent key is a
    # malformed answer, not a null one.
    if "identity_hint" not in data:
        return None
    hint = data.get("identity_hint")
    if hint is not None:
        if not isinstance(hint, str):
            return None
        hint = hint.strip() or None

    confidence = data.get("confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(
        confidence, (int, float)
    ):
        return None
    if not 0.0 <= float(confidence) <= 1.0:
        return None

    reasoning = data.get("reasoning")
    return StoreIdentity(
        identity_hint=hint,
        confidence=float(confidence),
        reasoning=reasoning if isinstance(reasoning, str) else "",
    )


# ============================================================
# The store and its edges
# ============================================================

def _store_node(family: str, answer: StoreIdentity, host_id: str) -> Node:
    """The `store` node for one `(family, hint)` the model named.

    The id is `resolver.store_node_id`'s, so a fallback store and a
    deterministic store that name the same place are the *same* node and
    the join is by id like any other (ADR-0021 §6). The attributes carry
    the identity's two halves plus `identity_source`, which is what says
    this store came from the model rather than from a literal. No
    `confidence`: an entity is in the graph or not (§1), and the
    probability belongs to the links that point here.
    """
    assert answer.identity_hint is not None  # caller checked
    return Node(
        id=store_node_id(family, answer.identity_hint),
        kind="store",
        name=f"{family}:{answer.identity_hint}",
        host_id=host_id,
        attributes={
            "family": family,
            "identity_hint": answer.identity_hint,
            "identity_source": LLM_IDENTITY_SOURCE,
        },
        content_hash=None,
    )


def _site_edge(
    family_site: FamilySite,
    answer: StoreIdentity,
    *,
    host_id: str,
    edge_id,
    ctx: "NormalizeContext",
    components: dict[tuple[str, str], str],
    by_line: dict[tuple[str, int], str],
    by_type: dict[tuple[str, str], list[str]],
) -> Edge | None:
    """The `writes_to` / `reads_from` edge one named site earns, or None.

    The direction is the site's kind: a `WriteSite` gives
    `writer → store`, a `ReadSite` gives `store → source` (ADR-0021 §3).
    Both ends are resolved the way the resolver resolves them, and a site
    whose end is not in the delta earns no edge — a component the graph
    does not know cannot be the first hop of a write chain, and a read of
    nothing cannot carry a `reads_from`.

    `identity_confidence` is `LLM_IDENTITY_BASE` (0.2) with **no** signal
    on top: there is no literal to match and no code context to add — the
    code context is why the fallback ran. The model's own confidence is
    recorded beside it as `identity_confidence_llm` and is not folded
    into the number: two probabilities combined would need their own
    tuning, and the number's meaning should stay stable (ADR-0023 §5).

    `observed_by` stays the `Edge` default (`["ast"]`) and
    `data_flow_confirmed` stays `None`: the site is an AST fact and the
    link is a declaration, not a trace (ADR-0016).
    """
    family, site = family_site.family, family_site.site
    store_id = store_node_id(family, answer.identity_hint)
    attributes = {
        "identity_source": LLM_IDENTITY_SOURCE,
        "identity_confidence_llm": answer.confidence,
    }

    if isinstance(site, WriteSite):
        writer_id = _writer_node_id(site, ctx, components)
        if writer_id is None:
            return None
        return Edge(
            id=edge_id(writer_id, store_id, "writes_to"),
            source_id=writer_id,
            target_id=store_id,
            kind="writes_to",
            host_id=host_id,
            flow_kind=STRUCTURAL,
            identity_confidence=LLM_IDENTITY_BASE,
            attributes=attributes,
        )

    target_id = _read_target_id(site, by_line, by_type)
    if target_id is None:
        return None
    return Edge(
        id=edge_id(store_id, target_id, "reads_from"),
        source_id=store_id,
        target_id=target_id,
        kind="reads_from",
        host_id=host_id,
        flow_kind=STRUCTURAL,
        identity_confidence=LLM_IDENTITY_BASE,
        attributes=attributes,
    )


_NO_NAME = "the model named no store"
_NO_END = "the store the model named has no node in the graph to link to"


def _report_gap(
    mode: str, family: str, site: WriteSite | ReadSite, reason: str
) -> None:
    """Report — or fail on — a site that stayed unresolved.

    Two reasons reach here, and both are gaps: the model answered `null`
    (`_NO_NAME`), or it named a store whose link has no end in the delta
    (`_NO_END` — a write site with no component node, a read site with no
    `untrusted_source`).

    `mode: required` is the analyst asking for the layer, and a gap must
    not pass silently: it raises `FallbackError`, which the stage's caller
    turns into a scan failure. `auto` warns and moves on, because a
    non-deterministic layer that fails a scan by default is a layer nobody
    leaves switched on (the D.2 rule, restated).
    """
    where = f"{family} site {site.file_path}:{site.line}"
    if mode == "required":
        raise FallbackError(
            f"llm_fallback.mode='required' and the {where} is still "
            f"unresolved — {reason} (set llm_fallback.mode to 'auto' to "
            f"leave it a warning)"
        )
    log.warning(
        "llm_fallback: the %s stays unresolved — %s", where, reason,
    )


__all__ = [
    "IMPORT_LINES",
    "RESPONSE_FORMATS",
    "WINDOW_LINES",
    "FallbackError",
    "FamilySite",
    "StoreIdentity",
    "parse_answer",
    "run",
]
