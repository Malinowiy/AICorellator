"""Unit tests for the call-pattern registry (step 3).

Three things are pinned here:

  1. `CallPattern.matches` — the trailing-segment, verb-family and
     underscore rules, one test each, including the shapes that must
     *not* match (a non-cursor receiver, a private verb, a foreign
     qualifier).
  2. `CapabilityRule` — the reason is required, and the rule set holds
     the invariants the registry relies on.
  3. The registry as a whole — every rule is registered, the rendered
     views are derived from the rules, and no two rules disagree about
     a name they both match unless the overlap is a documented one.

The classification behaviour itself is tested in
`test_capability_extractor.py`, unchanged from before step 3.
"""

from __future__ import annotations

import pytest

from aicorellator import patterns
from aicorellator.patterns_registry import (
    ROUTING_CAPABILITIES,
    CallPattern,
    CapabilityRule,
    matches_any,
)


# ------------------------------------------------------------
# CallPattern: bare verbs
# ------------------------------------------------------------

def test_bare_verb_matches_with_and_without_a_receiver() -> None:
    """A one-segment pattern is the final segment, receiver or not."""
    pattern = CallPattern(None, "open")
    assert pattern.matches("open")
    assert pattern.matches("foo.open")
    assert not pattern.matches("myopen")
    assert not pattern.matches("opened")
    assert not pattern.matches("open_file")


def test_bare_verb_never_matches_a_longer_dotted_tail() -> None:
    """`open` is the whole final segment — `x.open.y` is not a match."""
    assert not CallPattern(None, "open").matches("x.open.y")


# ------------------------------------------------------------
# CallPattern: qualifier + verb
# ------------------------------------------------------------

def test_qualified_pattern_matches_trailing_segments() -> None:
    """The qualifier is matched on the segments before the verb."""
    pattern = CallPattern("db", "delete")
    assert pattern.matches("db.delete")
    assert pattern.matches("self.db.delete")
    assert pattern.matches("session.db.delete")


def test_qualified_pattern_rejects_other_qualifiers() -> None:
    pattern = CallPattern("db", "delete")
    assert not pattern.matches("notdb.delete")
    assert not pattern.matches("self.delete")
    assert not pattern.matches("delete")


def test_qualified_pattern_requires_the_exact_verb() -> None:
    """Without `verb_family`, `db.delete_file` is a different method."""
    assert not CallPattern("db", "delete").matches("db.delete_file")
    assert CallPattern("db", "delete_file").matches("self.db.delete_file")


def test_multi_segment_qualifier() -> None:
    """`urllib.request.urlopen` — the whole qualifier must match."""
    pattern = CallPattern("urllib.request", "urlopen")
    assert pattern.matches("urllib.request.urlopen")
    assert pattern.matches("x.urllib.request.urlopen")
    assert not pattern.matches("urllib.urlopen")
    assert not pattern.matches("urllib.request.urlopen_async")


# ------------------------------------------------------------
# CallPattern: verb families (1.2a.3b)
# ------------------------------------------------------------

def test_verb_family_matches_prefixed_verbs() -> None:
    """A family covers the bare verb and any `<verb>_<noun>`."""
    pattern = CallPattern("repo", "get", verb_family=True)
    assert pattern.matches("repo.get")
    assert pattern.matches("repo.get_file")
    assert pattern.matches("repo.get_file_by_id")
    assert pattern.matches("self.repo.get_all_files")


def test_verb_family_still_requires_the_qualifier() -> None:
    """The qualifier is what makes it a repository call."""
    pattern = CallPattern("repo", "get", verb_family=True)
    assert not pattern.matches("get_file")
    assert not pattern.matches("notrepo.get_file")
    assert not pattern.matches("mydb.get_user")


def test_verb_family_is_one_verb_not_a_set() -> None:
    pattern = CallPattern("repo", "delete", verb_family=True)
    assert pattern.matches("repo.delete_transactions")
    assert not pattern.matches("repo.create_transaction")


def test_verb_family_without_a_qualifier_is_rejected() -> None:
    """`get*` alone would match every `get_*` method in a project."""
    with pytest.raises(ValueError):
        CallPattern(None, "get", verb_family=True)


def test_parse_rejects_a_bare_family() -> None:
    with pytest.raises(ValueError):
        CallPattern.parse("get*")


# ------------------------------------------------------------
# CallPattern: qualifier families (B.10a)
# ------------------------------------------------------------

LLM_FAMILY = frozenset({"llm", "model"})


def _llm_family(verb: str = "invoke") -> CallPattern:
    """The shape `CALLS_LLM_RULE` uses for a self-held handle."""
    return CallPattern(None, verb, qualifier_prefixes=LLM_FAMILY)


def test_qualifier_family_matches_the_handle_spellings() -> None:
    """One pattern for `llm`, `llm_client`, `model`, `model_client`."""
    pattern = _llm_family()
    assert pattern.matches("llm.invoke")
    assert pattern.matches("self.llm.invoke")
    assert pattern.matches("self.llm_client.invoke")
    assert pattern.matches("self.llm_handle.invoke")
    assert pattern.matches("self.model.invoke")
    assert pattern.matches("self.model_client.invoke")
    assert pattern.matches("vendor.llm_client.invoke")


def test_qualifier_family_needs_the_underscore_boundary() -> None:
    """`llmviewer` is not a handle: the underscore is the boundary."""
    pattern = _llm_family()
    assert not pattern.matches("self.llmviewer.invoke")
    assert not pattern.matches("self.modelviewer.invoke")
    assert not pattern.matches("self.llmclient.invoke")


def test_qualifier_family_rejects_other_qualifiers() -> None:
    pattern = _llm_family()
    assert not pattern.matches("self.client.invoke")
    assert not pattern.matches("self.notllm.invoke")
    assert not pattern.matches("self.retriever.invoke")
    assert not pattern.matches("invoke")


def test_qualifier_family_strips_leading_underscores() -> None:
    """The 1.2a.3e rule runs before the prefix check."""
    pattern = _llm_family()
    assert pattern.matches("self._llm.invoke")
    assert pattern.matches("self.__llm_client.invoke")
    assert not pattern.matches("self.not_llm.invoke")


def test_qualifier_family_still_requires_the_verb() -> None:
    """The family widens the qualifier, not the verb."""
    assert not _llm_family().matches("self.llm_client.chat")
    assert _llm_family("chat").matches("self.llm_client.chat")
    assert not _llm_family().matches("self.llm_client.invoked")


def test_qualifier_family_is_a_closed_set() -> None:
    """The set is written down, and it is not a provider-name table."""
    assert patterns.LLM_HANDLE_PREFIXES == frozenset({"llm", "model"})
    for absent in ("ai", "gpt", "claude", "client", "chat"):
        assert absent not in patterns.LLM_HANDLE_PREFIXES


def test_a_family_and_a_literal_qualifier_cannot_be_combined() -> None:
    """A family replaces the literal qualifier; it does not widen it."""
    with pytest.raises(ValueError, match="replaces the literal"):
        CallPattern("llm", "invoke", qualifier_prefixes=LLM_FAMILY)


def test_an_empty_family_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one prefix"):
        CallPattern(None, "invoke", qualifier_prefixes=frozenset())
    with pytest.raises(ValueError, match="not an empty string"):
        CallPattern(None, "invoke", qualifier_prefixes=frozenset({""}))


def test_a_family_with_a_verb_family_is_allowed() -> None:
    """They answer different questions: which receiver, which verb."""
    pattern = CallPattern(
        None,
        "get",
        verb_family=True,
        qualifier_prefixes=frozenset({"repo"}),
    )
    assert pattern.matches("repo.get_file")
    assert pattern.matches("self.repo.get_all")
    assert not pattern.matches("repo.create_file")
    assert not pattern.matches("get_file")


def test_the_literal_qualifier_is_still_literal() -> None:
    """The family form is the one that widens, never the literal."""
    literal = CallPattern("llm", "invoke")
    assert literal.matches("self.llm.invoke")
    assert not literal.matches("self.llm_client.invoke")


def test_family_text_and_parse_round_trip() -> None:
    """`{llm,model}.invoke` renders the family, and parses back."""
    pattern = _llm_family()
    assert pattern.text == "{llm,model}.invoke"
    assert CallPattern.parse(pattern.text) == pattern
    assert CallPattern.parse("{llm,model}.get*") == CallPattern(
        None, "get", verb_family=True, qualifier_prefixes=LLM_FAMILY
    )


def test_the_family_rendering_is_sorted_and_has_no_wildcard() -> None:
    """`*` still means *verb* family; a qualifier family is not one."""
    first = CallPattern(
        None, "invoke", qualifier_prefixes=frozenset({"model", "llm"})
    )
    second = CallPattern(
        None, "invoke", qualifier_prefixes=frozenset({"llm", "model"})
    )
    assert first.text == second.text == "{llm,model}.invoke"
    assert "*" not in first.text


def test_parse_rejects_a_family_without_a_verb() -> None:
    with pytest.raises(ValueError, match="needs a verb"):
        CallPattern.parse("{llm,model}")


def test_parse_rejects_an_unterminated_family() -> None:
    with pytest.raises(ValueError, match="unterminated"):
        CallPattern.parse("{llm,model.invoke")


def test_llm_rule_credits_the_wrapped_client_shape() -> None:
    """B.10a: the FinBot shape is a `calls_llm` call shape now.

    `self.llm_client.chat(...)` is what a wrapped client looks like, and
    it used to match nothing — the qualifier had to be exactly `llm` or
    `model` — so the class carrying it was not an agent (ADR-0016,
    decision 7 removed the name rules that used to cover for it).
    """
    rule = patterns.CALLS_LLM_RULE
    assert rule.matches("self.llm_client.chat")
    assert rule.matches("self.llm_handle.invoke")
    assert rule.matches("self.model_client.complete")
    assert rule.matches("self.model_client.generate")
    assert not rule.matches("self.client.chat")
    assert not rule.matches("self.llm_client.get")
    assert not rule.matches("self.modelviewer.invoke")


def test_llm_rule_uses_the_family_and_keeps_the_provider_patterns() -> None:
    """The widening is the qualifier; the provider patterns are data."""
    handles = [
        pattern
        for pattern in patterns.CALLS_LLM_RULE.patterns
        if pattern.qualifier_prefixes is not None
    ]
    assert {pattern.verb for pattern in handles} == {
        "invoke", "chat", "complete", "generate",
    }
    assert {pattern.qualifier for pattern in handles} == {None}
    assert all(
        pattern.qualifier_prefixes == patterns.LLM_HANDLE_PREFIXES
        for pattern in handles
    )

    for kept in (
        "openai.chat.completions.create",
        "anthropic.messages.create",
        "ollama.chat",
        "ollama.generate",
        "litellm.completion",
        "langchain.chat_models.ChatOpenAI",
        "llama_cpp.Llama",
        "vllm.LLM",
        "{llm,model}.invoke",
    ):
        assert kept in patterns.CAPABILITY_CALLS_LLM, kept

    for replaced in ("llm.invoke", "llm.chat", "model.invoke"):
        assert replaced not in patterns.CAPABILITY_CALLS_LLM, replaced


# ------------------------------------------------------------
# CallPattern: underscore normalization (1.2a.3e)
# ------------------------------------------------------------

def test_leading_underscores_on_the_receiver_are_ignored() -> None:
    """`_db` is the same object as `db`."""
    pattern = CallPattern("db", "delete")
    assert pattern.matches("_db.delete")
    assert pattern.matches("__db.delete")
    assert pattern.matches("self._db.delete")
    assert pattern.matches("session._db.delete")


def test_underscore_normalization_is_not_prefix_stripping() -> None:
    """`not_db` is a different name, not a private `db`."""
    assert not CallPattern("db", "delete").matches("not_db.delete")
    assert not CallPattern("db", "get", verb_family=True).matches(
        "_notdb.get_file"
    )


def test_the_verb_is_never_normalized() -> None:
    """`db._delete` is a private method, not the public ORM `delete`."""
    assert not CallPattern("db", "delete").matches("db._delete")
    assert not CallPattern("db", "delete", verb_family=True).matches(
        "db._delete_file"
    )


def test_normalization_can_be_turned_off() -> None:
    """The flag is per pattern: exact spelling when it is False."""
    exact = CallPattern("db", "delete", normalize_underscores=False)
    assert exact.matches("db.delete")
    assert not exact.matches("_db.delete")

    family = CallPattern(
        "repo", "get", verb_family=True, normalize_underscores=False
    )
    assert family.matches("repo.get_file")
    assert not family.matches("_repo.get_file")


def test_only_the_first_qualifier_segment_is_normalized() -> None:
    """`os.environ.get` — the receiver, not the attribute path."""
    pattern = CallPattern("os.environ", "get")
    assert pattern.matches("os.environ.get")
    assert pattern.matches("_os.environ.get")
    assert not pattern.matches("os._environ.get")


# ------------------------------------------------------------
# CallPattern: text form
# ------------------------------------------------------------

def test_text_renders_every_shape() -> None:
    assert CallPattern(None, "open").text == "open"
    assert CallPattern("db", "delete").text == "db.delete"
    assert CallPattern("repo", "get", verb_family=True).text == "repo.get*"


def test_text_and_parse_round_trip_for_the_whole_registry() -> None:
    """The rendered form is a faithful rendering of every pattern."""
    for rule in patterns.CALL_RULES:
        for pattern in rule.patterns:
            assert CallPattern.parse(pattern.text) == pattern
            assert pattern.text in rule.text_patterns



# ------------------------------------------------------------
# CapabilityRule
# ------------------------------------------------------------

def test_capability_rule_requires_a_reason() -> None:
    """A rule without a reason is a rule nobody can audit."""
    with pytest.raises(ValueError):
        CapabilityRule("writes_db", (), "")
    with pytest.raises(ValueError):
        CapabilityRule("writes_db", (), "   ")


def test_capability_rule_requires_a_capability() -> None:
    with pytest.raises(ValueError):
        CapabilityRule("", (), "reason")


def test_capability_rule_matches_any_of_its_patterns() -> None:
    rule = patterns.ORM_WRITES_RULE
    assert rule.matches("self.db.delete")
    assert rule.matches("self._session.add")
    assert not rule.matches("self.db.query")


def test_routing_capabilities_are_declared() -> None:
    """`db_statement` and `commit` never land on a node."""
    assert patterns.DB_STATEMENT_RULE.is_routing
    assert patterns.COMMIT_RULE.is_routing
    assert not patterns.ORM_WRITES_RULE.is_routing
    assert patterns.DB_STATEMENT_RULE.capability in ROUTING_CAPABILITIES
    assert patterns.COMMIT_RULE.capability in ROUTING_CAPABILITIES


def test_matches_any_accepts_objects_and_rendered_text() -> None:
    """Objects are the classification path; text is the rendered view."""
    assert matches_any("self.db.delete", (CallPattern("db", "delete"),))
    assert matches_any("self.db.delete", ("db.delete",))
    assert matches_any("repo.get_file", ("repo.get*",))
    assert not matches_any("repo.get_file", ("repo.create*",))


# ------------------------------------------------------------
# Registry invariants
# ------------------------------------------------------------

def test_every_rule_has_a_reason_and_a_capability() -> None:
    for rule in patterns.CALL_RULES:
        assert rule.capability
        assert rule.reason.strip()
        assert rule.reason == rule.reason.strip()


def test_every_family_has_a_qualifier() -> None:
    """1.2a.3b: a verb family without a qualifier is not allowed.

    A qualifier family counts (B.10a): it *is* the qualifier, so a
    `<family>.get*` pattern has a receiver to check like any other, and
    building one without a receiver is still the mistake this pins.
    """
    for rule in patterns.CALL_RULES:
        for pattern in rule.patterns:
            if pattern.verb_family:
                assert (
                    pattern.qualifier is not None
                    or pattern.qualifier_prefixes is not None
                )
                assert pattern.qualifier != ""


def test_no_rule_is_missing_from_the_registry() -> None:
    """Every `*_RULE` in patterns.py is registered."""
    declared = {
        name
        for name, value in vars(patterns).items()
        if name.endswith("_RULE") and isinstance(value, CapabilityRule)
    }
    registered = {
        name
        for name, value in vars(patterns).items()
        if name.endswith("_RULE")
        and isinstance(value, CapabilityRule)
        and value in patterns.CALL_RULES
    }
    assert declared == registered
    assert len(patterns.CALL_RULES) == len(registered)


def _example_calls() -> dict[str, list[CapabilityRule]]:
    """Every rule's own patterns, rendered as example call names.

    A qualifier family stands for every spelling it covers (B.10a), and
    the checks below are worth as much as the names they run on, so a
    family contributes one example per prefix — its shortest spelling
    (`llm`, `model`).
    """
    out: dict[str, list[CapabilityRule]] = {}
    for rule in patterns.CALL_RULES:
        for pattern in rule.patterns:
            verb = f"{pattern.verb}_x" if pattern.verb_family else pattern.verb
            if pattern.qualifier_prefixes is not None:
                qualifiers: list[str | None] = sorted(
                    pattern.qualifier_prefixes
                )
            else:
                qualifiers = [pattern.qualifier]
            for qualifier in qualifiers:
                if qualifier is None:
                    name = verb
                else:
                    name = f"self.{qualifier}.{verb}"
                out.setdefault(name, []).append(rule)
    return out


def test_no_unlisted_capability_overlap() -> None:
    """Two rules may agree on a name only where that is deliberate.

    The example calls come from the patterns themselves, so a typo (a
    pattern that accidentally matches another rule's shape) or a
    shadowed pattern shows up as an overlap with a capability pair that
    nobody declared. `os.system` is both code execution and a shell,
    `db.commit` is both an ORM write and a commit — those two pairs are
    listed in `patterns.INTENTIONAL_OVERLAPS`.
    """
    offenders = []
    for name, rules in _example_calls().items():
        capabilities = {rule.capability for rule in rules}
        if len(capabilities) < 2:
            continue
        if frozenset(capabilities) in patterns.INTENTIONAL_OVERLAPS:
            continue
        offenders.append((name, sorted(capabilities)))
    assert offenders == []


# ------------------------------------------------------------
# Rendered views
# ------------------------------------------------------------

def test_views_are_derived_from_the_rules() -> None:
    """The `CAPABILITY_*` names are renderings, never hand-maintained."""
    assert patterns.CAPABILITY_ORM_READS_DB == (
        patterns.ORM_READS_RULE.text_patterns
    )
    assert patterns.CAPABILITY_ORM_WRITES_DB == (
        patterns.ORM_WRITES_RULE.text_patterns
    )
    assert patterns.CAPABILITY_EXECUTES_CODE == (
        patterns.EXECUTES_CODE_RULE.text_patterns
    )
    assert patterns.ORM_COMMIT_CALLS == patterns.COMMIT_RULE.text_patterns
    assert patterns.CAPABILITY_DB_STATEMENT_CALLS == (
        patterns.DB_STATEMENT_RULE.text_patterns
    )


def test_database_views_are_the_union_of_their_rules() -> None:
    assert patterns.CAPABILITY_READS_DB == (
        patterns.CAPABILITY_DB_STATEMENT_CALLS
        + patterns.CAPABILITY_ORM_READS_DB
    )
    assert patterns.CAPABILITY_WRITES_DB == (
        patterns.CAPABILITY_DB_STATEMENT_CALLS
        + patterns.CAPABILITY_ORM_WRITES_DB
    )


def test_cursor_fetch_is_a_read_and_not_a_statement_call() -> None:
    """The 1.2a.3h split survives: fetch verbs live in the read rule."""
    for name in ("self.cur.fetchall", "self.cursor.fetchone"):
        assert patterns.ORM_READS_RULE.matches(name)
        assert not patterns.DB_STATEMENT_RULE.matches(name)
    # A fetch on an object that is not a cursor is not evidence.
    assert not patterns.ORM_READS_RULE.matches("row.fetchone")


def test_client_get_is_still_not_registered() -> None:
    """The 1.2a.3i GET decision, now asserted on the rule."""
    for name in ("self.client.get", "self.session.get", "self.adapter.get"):
        assert not patterns.HTTP_EGRESS_RULE.matches(name)


def test_intentional_overlaps_are_real() -> None:
    """Each declared overlap is exercised by at least one rule pair."""
    seen = set()
    for rules in _example_calls().values():
        capabilities = {rule.capability for rule in rules}
        if len(capabilities) > 1:
            seen.add(frozenset(capabilities))
    assert patterns.INTENTIONAL_OVERLAPS <= seen


# ------------------------------------------------------------
# Untrusted-source writer rules (B.2)
# ------------------------------------------------------------
# The read side's table-and-lookup agreement is
# `test_untrusted_sources.test_every_declared_pattern_matches_its_own_family`;
# these are the write side's, and they pin the same property for
# `WRITER_RULES` / `untrusted_writer_match`.

def test_the_writer_table_pairs_each_family_with_its_own_rule() -> None:
    """A writer family and its rule are one entry, not two lists.

    The table is what says "this rule evidences a write into this
    family", and the capability on the rule is the family's — the two
    halves `untrusted_source_writers` reads to pair a write with a
    same-family read.
    """
    assert dict(patterns.WRITER_RULES) == {
        "rag_corpus": patterns.WRITES_RAG_CORPUS_RULE,
        "email_inbox": patterns.WRITES_EMAIL_INBOX_RULE,
    }


def test_every_writer_pattern_resolves_to_its_own_family() -> None:
    """Every pattern of every writer rule matches its own family first."""
    for source_type, rule in patterns.WRITER_RULES:
        for pattern in rule.patterns:
            match = patterns.untrusted_writer_match(pattern.text)
            assert match is not None, pattern.text
            assert match[0] == source_type, pattern.text
            assert match[1] is rule, pattern.text


def test_the_writer_families_are_source_families() -> None:
    """A writer writes into a family the read side knows.

    A rule for a source type no read rule defines would produce edges
    into nodes nothing ever creates — the capability would be a name
    with no source behind it.
    """
    read_families = {name for name, _ in patterns.UNTRUSTED_SOURCE_RULES}

    assert {name for name, _ in patterns.WRITER_RULES} <= read_families


def test_the_writer_capabilities_are_declared_and_distinct() -> None:
    """Two families, two capabilities, both in the vocabulary.

    One name would let a write of one family stand in as the writer of a
    read of the other, which is the pairing the two names exist to
    prevent.
    """
    capabilities = [rule.capability for _, rule in patterns.WRITER_RULES]

    assert len(set(capabilities)) == len(capabilities)
    assert set(capabilities) <= patterns.VALID_CAPABILITIES


def test_the_writer_views_are_derived_from_the_rules() -> None:
    """The `CAPABILITY_WRITES_*` names are renderings, not lists."""
    assert patterns.CAPABILITY_WRITES_RAG_CORPUS == (
        patterns.WRITES_RAG_CORPUS_RULE.text_patterns
    )
    assert patterns.CAPABILITY_WRITES_EMAIL_INBOX == (
        patterns.WRITES_EMAIL_INBOX_RULE.text_patterns
    )


def test_the_writer_rules_add_no_overlap() -> None:
    """B.2 declares no new capability overlap (the set is unchanged).

    No other rule credits `writes_rag_corpus` or `writes_email_inbox`,
    so `test_no_unlisted_capability_overlap` above stays green without a
    new entry — and this pins that the set was not widened to hide one.
    """
    assert patterns.INTENTIONAL_OVERLAPS == frozenset({
        frozenset({"executes_code", "executes_shell"}),
        frozenset({"writes_db", "commit"}),
    })


def test_no_rule_duplicates_another_rules_capability() -> None:
    """A name may not reach the same capability through two rules.

    A duplicate means one of the two rules is redundant — a pattern
    filed under the wrong capability, or a rule that should have been
    merged.
    """
    offenders = []
    for name, rules in _example_calls().items():
        capabilities = [rule.capability for rule in rules]
        duplicates = {
            capability
            for capability in capabilities
            if capabilities.count(capability) > 1
        }
        if duplicates:
            offenders.append((name, sorted(duplicates)))
    assert offenders == []

