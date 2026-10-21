"""Structured call patterns and capability rules (step 3).

`patterns.py` is a single registry of everything the system knows, and
until step 3 its capability entries were tuples of strings in which a
trailing `*` meant "verb family" and a leading underscore on the
receiver was silently ignored. Both rules were implemented as string
surgery inside the matcher, so a pattern's meaning could not be read
off the pattern and a typo (`repo.*_file`, a wildcard with no
qualifier) was a runtime surprise.

This module gives the pattern a type:

    CallPattern      qualifier + verb, with `verb_family` and
                     `normalize_underscores` as explicit flags
    CapabilityRule   a capability, the patterns that evidence it, and
                     the reason the rule exists

This module holds the *semantics*; `patterns.py` holds the *data* (the
rules themselves) and stays free of logic. The split is deliberate: the
module that knows how to match must not also be the file everyone reads
to find out what is recognized.

Matching rules, in one place:

    bare verb        `CallPattern(None, "open")` matches `open` and
                     `foo.open` — the final segment, receiver or not
    qualified        `CallPattern("db", "delete")` requires the final
                     segment to be `delete` and the preceding segment
                     (trailing-segment match, so `self.db.delete` and
                     `session.db.delete` both match) to be `db`
    verb family      `CallPattern("repo", "get", verb_family=True)`
                     requires only that the final segment *starts with*
                     `get` (1.2a.3b); the qualifier still has to match,
                     so a bare `get_file` never does
    underscores      leading underscores on the first matched qualifier
                     segment are stripped before comparing (1.2a.3e),
                     so `_db.delete` and `__db.delete` match
                     `db.delete`; the verb is never normalized
                     (`db._delete` is a private method, not the public
                     ORM `delete`)

`CallPattern.text` renders a pattern back to the familiar
`qualifier.verb` (+ `*` for a family) spelling and `parse` reads that
spelling back. The pair exists for the registry's rendered views, for
messages, and for tests that assert on pattern *text*. Rendering is a
display convention, not a matching rule: nothing in `matches` looks for
`*`, and the classification path in `capability_extractor` passes
objects, never text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# Capabilities that name a *routing* decision rather than a property of
# a node. `db_statement` says "a statement call site: inspect the SQL
# text"; `commit` says "this is a commit" (1.2a.3c). Neither is ever
# written to a node's capabilities — `capability_extractor` is the only
# consumer, and the Flow Analyzer's commit exception is the only other
# reader. Keeping them in the rule that produces them makes the
# registry complete: every call pattern in `patterns.py` belongs to a
# rule with a capability and a reason.
ROUTING_CAPABILITIES: frozenset[str] = frozenset({
    "db_statement",
    "commit",
})



@dataclass(frozen=True)
class CallPattern:
    """One call shape: a qualifier, a verb, and how strictly to match."""

    qualifier: str | None
    verb: str
    # `verb_family` (1.2a.3b): the verb is a family, so the final
    # segment only has to start with it (`repo.get*` covers
    # `repo.get_file`). A family always needs a qualifier — without one
    # every `get_*` method in the project would match — so the
    # combination is rejected at construction.
    verb_family: bool = False
    # `normalize_underscores` (1.2a.3e): leading underscores on the
    # receiver are a visibility convention, not identity, so `_db`
    # matches `db`. Only the first matched qualifier segment is
    # normalized, and never the verb.
    normalize_underscores: bool = True

    def __post_init__(self) -> None:
        if not self.verb:
            raise ValueError("CallPattern needs a verb")
        if self.verb_family and self.qualifier is None:
            raise ValueError(
                "a verb family needs a qualifier: "
                f"{self.verb}* would match every {self.verb}* method"
            )
        if self.qualifier is not None and not self.qualifier:
            raise ValueError("qualifier must be None or a name")

    @property
    def text(self) -> str:
        """The rendered pattern: `db.delete`, `repo.get*`, `open`."""
        star = "*" if self.verb_family else ""
        if self.qualifier is None:
            return f"{self.verb}{star}"
        return f"{self.qualifier}.{self.verb}{star}"

    @classmethod
    def parse(cls, text: str) -> "CallPattern":
        """Read the rendered form back into a pattern.

        A qualified verb (`db.delete`), a family (`repo.get*`) and a
        bare verb (`open`) all round-trip. The inverse of `text`, used
        by the registry's rendered views and by tests that assert on
        pattern text.
        """
        verb_family = text.endswith("*")
        body = text[:-1] if verb_family else text
        if "." in body:
            qualifier, verb = body.rsplit(".", 1)
        else:
            qualifier, verb = None, body
        return cls(qualifier, verb, verb_family=verb_family)

    def matches(self, dotted: str) -> bool:
        """True if `dotted` is this call shape."""
        if self.qualifier is None:
            # A bare verb has no receiver to check: it is the final
            # segment, whether the call is `open(...)` or `foo.open(...)`.
            return dotted == self.verb or dotted.endswith("." + self.verb)

        segments = dotted.split(".")
        if len(segments) < 2:
            return False
        last = segments[-1]
        if self.verb_family:
            if not last.startswith(self.verb):
                return False
        elif last != self.verb:
            return False
        return self._qualifier_matches(segments[:-1])

    def _qualifier_matches(self, head: list[str]) -> bool:
        """True if `head` ends with the pattern's qualifier segments."""
        wanted = self.qualifier.split(".") if self.qualifier else []
        if len(head) < len(wanted):
            return False
        tail = head[-len(wanted):]
        if self.normalize_underscores:
            tail = [_strip_leading_underscores(tail[0]), *tail[1:]]
        return tail == wanted


@dataclass(frozen=True)
class CapabilityRule:
    """A capability, the call shapes that evidence it, and the reason.

    `reason` is required and non-empty: it is a sentence for the log and
    for whoever reads the registry to decide whether a new pattern
    belongs here. An empty rule is legal when the reason explains why
    there is nothing to match (`http_ingress`).
    """

    capability: str
    patterns: tuple[CallPattern, ...]
    reason: str

    def __post_init__(self) -> None:
        if not self.capability:
            raise ValueError("CapabilityRule needs a capability")
        if not self.reason.strip():
            raise ValueError(
                f"CapabilityRule {self.capability!r} needs a reason: the "
                "registry is what a reviewer reads to decide whether a "
                "pattern belongs"
            )

    def matches(self, dotted: str) -> bool:
        """True if any of the rule's patterns matches `dotted`."""
        return matches_any(dotted, self.patterns)

    @property
    def text_patterns(self) -> tuple[str, ...]:
        """The rule's patterns in rendered form."""
        return tuple(pattern.text for pattern in self.patterns)

    @property
    def is_routing(self) -> bool:
        """True when the capability routes rather than describes a node."""
        return self.capability in ROUTING_CAPABILITIES


def matches_any(
    dotted: str,
    patterns: Iterable[CallPattern | str],
) -> bool:
    """True if `dotted` matches any pattern.

    A `CallPattern` is matched directly. A `str` is the rendered form of
    one (`"repo.get*"`) and is parsed first: the registry's rendered
    views are strings, and so are the pattern literals in the existing
    tests. The classification path passes objects.
    """
    for pattern in patterns:
        if isinstance(pattern, str):
            pattern = CallPattern.parse(pattern)
        if pattern.matches(dotted):
            return True
    return False


def _strip_leading_underscores(segment: str) -> str:
    """Return `segment` without its leading underscores (1.2a.3e).

    `_db` and `__db` both become `db`; `not_db` stays `not_db`.
    """
    return segment.lstrip("_")


__all__ = [
    "ROUTING_CAPABILITIES",
    "CallPattern",
    "CapabilityRule",
    "matches_any",
]

