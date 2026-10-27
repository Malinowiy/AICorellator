"""Layer 1b — the LLM classifier (ADR-0016).

The last classification layer, and the only non-deterministic one. It
exists because class-A name heuristics were removed (ADR-0016, decision 7)
and a project with a custom SDK has no framework detector to cover it. It
is opt-in because it is expensive, and because a user detector can cover
the same case deterministically.

Where it sits. After the deterministic layers (TSA, framework detectors,
capability extraction) and before `normalize()`. Its `DetectionResult` is
merged into the one `run_detectors` produced and passed to `normalize` as
`framework_detection` — no new stage, no new graph path. It is **not** a
`FrameworkDetector`: a detector walks an AST and recognizes a framework, a
classifier receives symbols and asks a model. The merge point is the same;
the interface is not, and pretending otherwise would put an HTTP call
behind a method the registry resolves by name.

Its input is a list of `UnclassifiedSymbol` — the symbols no deterministic
layer produced a node for, computed by `runner.py`. That computation is the
caller's, not this module's, because "which symbols did the deterministic
layers classify" is exactly the knowledge the caller has (the capability
map, the detector output, `classification.py`'s predicates) and this module
does not.

## The general rules, applied to every node the model produces

  - **Kind is closed.** `prompt.KIND_ENUM` is the whole vocabulary. A
    `kind` outside it is not a classification, it is a malformed answer:
    the symbol is dropped and the answer is not cached.
  - **Kinds that are not nodes.** `unknown` and `part_of_parent` mean "do
    not create a node". They are answers, not failures, and limiter 1 acts
    on `part_of_parent`.
  - **Capabilities are a whitelist.** The answer is intersected with
    `prompt.CAPABILITY_ENUM`, deduplicated and sorted. A capability the
    model invents is dropped, never stored: the vocabulary is shared with
    the deterministic extractor (ADR-0009) and `lethal_trifecta` is
    computed from it, so an invented capability would corrupt a derived
    signal.
  - **Provenance.** Every node carries
    `observed_by = ["ast", "static_llm:<model_id>"]` — `"ast"` because the
    node is anchored to an AST symbol and merged with the AST layer,
    `"static_llm:<model_id>"` because the model, not the AST, decided the
    kind (ADR-0016, §`observed_by`). The model id is in the string, so the
    graph records *which* model claimed it.
  - **Confidence stays at the `Node` default (1.0).** D.2 does not
    calibrate a confidence for an LLM answer, and a made-up 0.8 would look
    like a measurement. Provenance is expressed in `observed_by`, where it
    is a fact rather than a number.

## The three provenance limiters

A limiter stops the model from *overturning* a deterministic reading of the
same code. All three are in `Classifier._limiters`, and each is a rule
about the parent, not about the symbol:

  1. **`part_of_parent` never creates a node.** The symbol's capabilities
     are merged into its enclosing class's node instead, because the
     method's behaviour *is* the class's behaviour. With no parent node to
     merge into, the capabilities are dropped with a debug log.
  2. **A method inside an agent-class is not a new agent.** If the model
     says `agent`, `llm_endpoint` or `mcp_server` for a method whose
     enclosing class is already an agent node, the capabilities are merged
     into the parent and no node is created. The class is the agent; the
     method is its behaviour.
  3. **A tool inside an `mcp_server` without a tool decorator is marked,
     not blocked.** The node is created either way and gains
     `attributes["unverified_tool"] = True`, plus a warning. Blocking would
     be wrong — custom decorators exist — and silence would be wrong too:
     the node's provenance would be unclear to a reader.

     The trigger is narrow, and that is worth knowing: `parent_kind` is
     `"mcp_server"` only when the enclosing class has an `mcp_server` node
     *named like the class*. The built-in detectors name that node after the
     server name from the constructor argument (`FastMCP("filesys")` →
     `filesys`), and `McpServerNodesStage` names it after the
     `mcp/servers/<name>/` directory — neither equals the class name. So in
     practice this limiter fires when the model itself classifies the
     enclosing class as an `mcp_server`, which is exactly the case it was
     written for: the model decided both halves, and only the second one
     wants a declaration behind it.

A fourth limiter (guardrails) is out of scope for D.2.

**Limitation, stated because it is invisible otherwise.** Limiters 1 and 2
merge capabilities into a parent node, and this module can only do that for
a parent node *it created itself*. A deterministic parent node does not
exist yet — it is built later, by `normalize()`, from the capability map
and the detector output — and this module runs before that. Writing the
model's capabilities into that map instead would let the LLM change a
deterministic *classification*, which is the opposite of what a limiter is
for. So: merge when the parent is ours, drop with a debug log when it is
not. Enriching deterministic nodes needs a stage that runs after
classification, which D.2 does not add.

## Transport

The HTTP call is this module's own (`requests`, the same client the
benchmark used), because `analysis/client.py` has no `response_format`
support and so cannot express the schema-constrained request this prompt
needs, nor distinguish "the server rejected the format" (HTTP 400) from
"the endpoint is down". The JSON extraction is *not* duplicated:
`analysis.client.parse_json_response` already handles fenced and
prose-wrapped JSON, and reusing it keeps one implementation of the messy
part. The `llm_call` parameter injects a fake for tests, so no test needs a
server.

## Injecting into the model

Symbol text is code from the scanned target, and a target can contain an
instruction aimed at this prompt. The defence is layered: the text is
wrapped in `[UNTRUSTED_DATA]` markers with an explicit rule that content
inside them is data (prompt rule 1), and the answer is validated against
closed sets. So the worst an injected answer can do is choose a wrong kind
or a wrong capability from the vocabulary the validator allows. It cannot
add a capability, cannot invent a kind, and cannot create a node for a
symbol a deterministic layer already classified (the runner drops those).

"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable

import requests

from ...analysis.client import LLMResponseError, parse_json_response
from ...graph.models import Node
from ..capability_extractor import FileCapabilities, SymbolCapabilities
from ..classification import Classification
from ..framework_detectors import DetectionResult
from ..models import TSASymbol
from ..normalizer_helpers import node_from_class, node_from_method
from .cache import ClassifierCache, content_hash
from .config import ClassifierConfig
from .prompt import (
    CAPABILITY_ENUM,
    CLASSIFICATION_SCHEMA,
    KIND_ENUM,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)


log = logging.getLogger(__name__)


# The closed sets are *derived* from the prompt's enums, not restated: the
# validator must accept exactly what the model was told it may answer. A
# second copy would drift the first time the prompt changes.
LLM_KINDS = frozenset(KIND_ENUM)

# Kinds that are graph nodes. `unknown` and `part_of_parent` are answers
# meaning "no node" (ADR-0016; limiter 1 acts on the latter).
NODE_KINDS = LLM_KINDS - {"unknown", "part_of_parent"}

# The capability vocabulary, for the whitelist intersection.
CAPABILITIES = frozenset(CAPABILITY_ENUM)

# How the answer is constrained on the wire, strongest first. The
# classifier starts at `config.response_format` and steps down when the
# server rejects a form — the same probe `tools/bench_classifier.py` does,
# except that here it happens inline on a real symbol instead of on a
# throwaway first call.
RESPONSE_FORMATS = ("schema", "object", "none")

# Kinds a method may not claim when its enclosing class is already an agent
# node — limiter 2. A method is the class's behaviour, not a second
# component of the same shape.
AGENT_OWNED_KINDS = frozenset({"agent", "llm_endpoint", "mcp_server"})

# The `DetectionResult.framework` label. It is not a framework name: the
# merge stage only reads `nodes` and `edges`, and this string is what a
# reader of the merged result sees when asking where those nodes came from.
FRAMEWORK = "llm_classifier"


# ============================================================
# Input and answer types
# ============================================================

@dataclass
class UnclassifiedSymbol:
    """One symbol the deterministic layers produced no node for.

    Built by `runner.py`, which is the layer that knows what the
    deterministic layers did. `source` is the symbol's text as the
    benchmark sliced it (the `def`/`class` line through `end_lineno` —
    decorators are deliberately not included: the model is asked to judge
    by body, and `has_tool_decorator` carries the decorator fact separately
    for limiter 3).

    `parent_kind` is the enclosing class's kind *as the deterministic
    layers decided it*, or None when they decided nothing. It is the input
    to limiters 1 and 2 when the parent is not a node this module created.
    """

    name: str
    kind: str                       # "class" | "method" (TSA's vocabulary)
    file_path: str
    line_start: int
    source: str
    imports: list[str] = field(default_factory=list)
    class_name: str | None = None   # for methods
    has_tool_decorator: bool = False
    parent_kind: str | None = None


@dataclass(frozen=True)
class SymbolClassification:
    """A validated answer: the only two things the model may claim.

    Constructed by `parse_answer`, so every instance is already inside the
    closed kind set with already-whitelisted capabilities.
    """

    kind: str
    capabilities: tuple[str, ...] = ()
    reasoning: str = ""


class ClassifierError(Exception):
    """Raised when the classifier cannot run at all.

    Configuration problems and a `required` policy that could not be
    satisfied. Deliberately *not* raised for a single unusable answer: one
    symbol the model could not classify is a warning, not a failed scan.
    """


# A fake for tests: `(system_prompt, user_prompt) -> assistant text or None`.
# None means "no answer" — a timeout, a transport error, a refused format.
LLMCall = Callable[[str, str], str | None]


# ============================================================
# Classifier
# ============================================================

class Classifier:
    """Classifies the unclassified symbols of one file at a time.

    One instance per scan run: it holds the cache connection, the answer
    format the server accepted, and the injected `llm_call`.
    """

    def __init__(
        self,
        config: ClassifierConfig,
        cache: ClassifierCache | None = None,
        llm_call: LLMCall | None = None,
        host_id: str = "localhost",
    ) -> None:
        self.config = config
        self.cache = cache
        self.host_id = host_id
        self._llm_call = llm_call
        # Starts at the configured form and steps down as the server
        # refuses them — see `_ask`. The runner always passes a cache; None
        # is for tests that exercise the limiters without one.
        self._response_format = config.response_format

    @property
    def provenance(self) -> str:
        """The `observed_by` value every node this classifier makes carries."""
        return f"static_llm:{self.config.model}"

    def classify_file(
        self,
        file_path: str,
        unclassified_symbols: list[UnclassifiedSymbol],
    ) -> DetectionResult:
        """Classify one file's unclassified symbols.

        Returns a `DetectionResult` holding the nodes the model justified —
        possibly none. Never raises because of one bad answer: an unusable
        answer drops its symbol, and whether that is fatal is the caller's
        policy (`mode: required`, `unclassified.action`), not this method's.

        Two passes. The first asks the model, in the caller's order. The
        second builds nodes, **classes before methods**, because limiters 1
        and 2 need the parent's node to exist and a parent is always a
        class. Both passes are order-stable, so a run is reproducible and
        the cache is consulted in corpus order.
        """
        result = DetectionResult(framework=FRAMEWORK)
        if not unclassified_symbols:
            return result

        answers = [
            (symbol, self._classify(symbol))
            for symbol in unclassified_symbols
        ]

        nodes_by_name: dict[str, Node] = {}
        for symbol, answer in sorted(
            answers, key=lambda pair: pair[0].kind != "class"
        ):
            if answer is None:
                continue
            parent = (
                nodes_by_name.get(symbol.class_name)
                if symbol.class_name
                else None
            )
            parent_kind = parent.kind if parent else symbol.parent_kind

            # Limiter 1 — the method's behaviour is the class's behaviour.
            if answer.kind == "part_of_parent":
                self._merge_into_parent(
                    parent, symbol, answer, "part_of_parent"
                )
                continue
            if answer.kind not in NODE_KINDS:
                # `unknown`: the body did not show enough to decide. This is
                # an answer, not an error — and not a node.
                log.debug(
                    "llm_classifier: %s in %s is unknown to the model",
                    symbol.name, file_path,
                )
                continue
            # Limiter 2 — the class is the agent, the method is behaviour.
            if parent_kind == "agent" and answer.kind in AGENT_OWNED_KINDS:
                self._merge_into_parent(parent, symbol, answer, "agent-owned")
                continue

            node = self._node_for(symbol, answer)
            # Limiter 3 — marked, not blocked: the node is created either
            # way, and the attribute says why a reader should look twice.
            if (
                parent_kind == "mcp_server"
                and answer.kind == "tool"
                and not symbol.has_tool_decorator
            ):
                node.attributes["unverified_tool"] = True
                log.warning(
                    "llm_classifier: unverified tool %s in %s — the model "
                    "called it a tool and no tool decorator was found",
                    symbol.name, file_path,
                )
            result.nodes.append(node)
            if symbol.kind == "class":
                # Registered whatever its kind: limiter 1 needs the node to
                # exist, limiter 2 needs its kind.
                nodes_by_name[symbol.name] = node

        return result

    # --------------------------------------------------------
    # Limiters 1 and 2 — merging into a parent
    # --------------------------------------------------------

    def _merge_into_parent(
        self,
        parent: Node | None,
        symbol: UnclassifiedSymbol,
        answer: SymbolClassification,
        reason: str,
    ) -> None:
        """Merge the answer's capabilities into a parent node, or drop them.

        Only a node this classifier created can be written to: a
        deterministic parent is built later, by `normalize()`, and writing
        the model's capabilities into the capability map that build reads
        would let the LLM change a deterministic classification. See the
        module docstring, §"The three provenance limiters".
        """
        if parent is None:
            log.debug(
                "llm_classifier: %s capabilities dropped (%s): no parent "
                "node for %s in %s",
                symbol.name, reason, symbol.class_name or symbol.name,
                symbol.file_path,
            )
            return
        parent.capabilities = sorted(
            set(parent.capabilities) | set(answer.capabilities)
        )
        log.debug(
            "llm_classifier: %s capabilities merged into %s (%s)",
            symbol.name, parent.name, reason,
        )

    # --------------------------------------------------------
    # Node construction
    # --------------------------------------------------------

    def _node_for(
        self, symbol: UnclassifiedSymbol, answer: SymbolClassification
    ) -> Node:
        """The node for a validated answer.

        Built through `node_from_class` / `node_from_method` — the same
        constructors the deterministic stages use — so the id is derived
        identically. One symbol cannot end up with two nodes, and the
        capabilities reach the node by the same route the extractor's do
        (`caps_for_symbol`), rather than by a second, parallel path.
        """
        capabilities = sorted(set(answer.capabilities))
        file_capabilities = {
            symbol.file_path: FileCapabilities(
                file_path=symbol.file_path,
                symbols=[
                    SymbolCapabilities(
                        symbol_name=symbol.name,
                        file_path=symbol.file_path,
                        capabilities=set(capabilities),
                    )
                ],
            )
        }
        attributes: dict = {}
        if symbol.class_name:
            attributes["parent_class"] = symbol.class_name

        tsa_symbol = TSASymbol(
            name=symbol.name,
            kind=symbol.kind,
            file_path=symbol.file_path,
            line_start=symbol.line_start,
            line_end=symbol.line_start,
            class_name=symbol.class_name,
        )
        classification = Classification(kind=answer.kind, attributes=attributes)
        if symbol.kind == "class":
            node = node_from_class(
                tsa_symbol, classification, file_capabilities
            )
        else:
            node = node_from_method(
                tsa_symbol, classification, file_capabilities
            )
        node.observed_by = ["ast", self.provenance]
        return node

    # --------------------------------------------------------
    # Asking the model
    # --------------------------------------------------------

    def _classify(
        self, symbol: UnclassifiedSymbol
    ) -> SymbolClassification | None:
        """The validated answer for one symbol: cache first, then the model.

        A `None` return means "no answer" — a transport failure, a refused
        format, or a malformed answer. None of those are cached, so a
        transient failure does not become permanent.
        """
        key = content_hash(symbol.source, symbol.file_path, symbol.imports)
        if self.cache is not None:
            cached = self.cache.get(key, PROMPT_VERSION, self.config.model)
            if cached is not None:
                log.debug(
                    "llm_classifier: cache hit for %s in %s",
                    symbol.name, symbol.file_path,
                )
                return SymbolClassification(
                    kind=cached.kind,
                    capabilities=cached.capabilities,
                    reasoning=cached.reasoning,
                )

        user_prompt = build_user_prompt(
            symbol.source, symbol.file_path, symbol.imports
        )
        text = self._ask(user_prompt)
        if text is None:
            return None

        answer = parse_answer(text)
        if answer is None:
            log.warning(
                "llm_classifier: unusable answer for %s in %s — the symbol "
                "is left unclassified and the answer is not cached",
                symbol.name, symbol.file_path,
            )
            return None

        if self.cache is not None:
            self.cache.put(
                key, PROMPT_VERSION, self.config.model,
                kind=answer.kind,
                capabilities=answer.capabilities,
                reasoning=answer.reasoning,
            )
        return answer

    def _ask(self, user_prompt: str) -> str | None:
        """The assistant's text for one symbol, or None.

        An injected `llm_call` wins, so no test needs a server. The real
        path steps the response format down when a server refuses it: one
        that rejects `json_schema` may accept `json_object`, or nothing at
        all. The answer is validated against closed sets either way, so a
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
                    "llm_classifier: %s refused response_format=%r "
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
        unavailable". That distinction is why this is not
        `analysis.client.complete`, which returns None for every non-200
        and so cannot tell the two apart.
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
            payload["response_format"] = CLASSIFICATION_SCHEMA
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
                "llm_classifier: timeout after %ds on %s",
                self.config.timeout, url,
            )
            return None, False
        except requests.RequestException as e:
            log.warning("llm_classifier: request failed: %s", e)
            return None, False

        if response.status_code == 400:
            return None, True
        if response.status_code != 200:
            log.error(
                "llm_classifier: HTTP %d from %s: %s",
                response.status_code, url, response.text[:200],
            )
            return None, False

        try:
            data = response.json()
            return data["choices"][0]["message"]["content"], False
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
            log.warning("llm_classifier: unusable response shape: %s", e)
            return None, False


# ============================================================
# Answer validation
# ============================================================

def parse_answer(text: str) -> SymbolClassification | None:
    """Validate one assistant message into an answer, or None.

    The two claims are checked against the closed sets the prompt
    advertised: `kind` must be in `KIND_ENUM`, and `capabilities` is
    intersected with `CAPABILITY_ENUM`. Anything else — non-JSON, a missing
    or non-string `kind`, an invented capability — returns None, which the
    caller turns into "left unclassified", and does not cache.

    A function rather than a method: it is the layer's whole contract in
    one readable place, and the piece tests should attack.
    """
    try:
        data = parse_json_response(text)
    except LLMResponseError:
        return None
    if not isinstance(data, dict):
        return None

    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in LLM_KINDS:
        return None

    raw = data.get("capabilities")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        return None
    capabilities = tuple(sorted({
        capability
        for capability in raw
        if isinstance(capability, str) and capability in CAPABILITIES
    }))

    reasoning = data.get("reasoning")
    return SymbolClassification(
        kind=kind,
        capabilities=capabilities,
        reasoning=reasoning if isinstance(reasoning, str) else "",
    )


# ============================================================
# Convenience entry point
# ============================================================

def classify_file(
    file_path: str,
    unclassified_symbols: list[UnclassifiedSymbol],
    *,
    config: ClassifierConfig,
    cache: ClassifierCache | None = None,
    llm_call: LLMCall | None = None,
    host_id: str = "localhost",
) -> DetectionResult:
    """Classify one file's unclassified symbols with a throwaway Classifier.

    For callers that have no scan to hang a `Classifier` on: a test, a tool,
    one file by hand. The runner builds a single `Classifier` per scan
    instead, so the accepted response format is probed once and the cache
    connection is opened once.
    """
    return Classifier(
        config=config, cache=cache, llm_call=llm_call, host_id=host_id,
    ).classify_file(file_path, unclassified_symbols)
