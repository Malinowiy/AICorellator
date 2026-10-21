"""`find_untrusted_source_reads` — the `untrusted_source` detector (1.3a).

The detector is the read-site walk: it turns a parsed module into the
`UntrustedSourceRead` records the stage turns into nodes. The rules
themselves are `patterns.UNTRUSTED_SOURCE_RULES`; these tests pin the
walk (what is a read, who the reader is, in what order) and the
agreement between the table and the lookup.
"""

from __future__ import annotations

import ast

import pytest

from aicorellator import patterns
from aicorellator.parsing.untrusted_sources import (
    UntrustedSourceRead,
    find_untrusted_source_reads,
)


def _reads(source: str) -> list[UntrustedSourceRead]:
    return find_untrusted_source_reads(ast.parse(source))


def test_a_module_level_read_has_no_reader() -> None:
    """`reader` is None at module level: the reader is the module."""
    reads = _reads('conn = imaplib.IMAP4_SSL("imap.example.com")\n')

    assert len(reads) == 1
    assert reads[0].source_type == "email_inbox"
    assert reads[0].call_target == "imaplib.IMAP4_SSL"
    assert reads[0].line == 1
    assert reads[0].reader is None
    assert reads[0].container is None


def test_a_function_read_names_the_function() -> None:
    reads = _reads(
        "def read_docs(q):\n"
        "    return retriever.invoke(q)\n"
    )

    assert [(r.source_type, r.reader, r.container) for r in reads] == [
        ("rag_corpus", "read_docs", None),
    ]
    assert reads[0].line == 2


def test_a_method_read_names_the_method_and_the_class() -> None:
    reads = _reads(
        "class Tools:\n"
        "    def list(self):\n"
        "        return client.list_tools()\n"
    )

    assert [(r.reader, r.container) for r in reads] == [
        ("list", "Tools"),
    ]
    assert reads[0].source_type == "mcp_tool_description"


def test_reads_are_in_source_order() -> None:
    """Module level first, then each definition as it appears."""
    reads = _reads(
        "conn = imaplib.IMAP4_SSL('x')\n"
        "\n"
        "def a(q):\n"
        "    return retriever.invoke(q)\n"
        "\n"
        "def b(c):\n"
        "    return vectorstore.similarity_search(c)\n"
    )

    assert [r.call_target for r in reads] == [
        "imaplib.IMAP4_SSL",
        "retriever.invoke",
        "vectorstore.similarity_search",
    ]


def test_a_nested_function_belongs_to_the_enclosing_definition() -> None:
    """`ast.walk` descends; the enclosing symbol is the reader."""
    reads = _reads(
        "def outer(q):\n"
        "    def inner(x):\n"
        "        return retriever.invoke(x)\n"
        "    return inner(q)\n"
    )

    assert [(r.reader, r.container) for r in reads] == [("outer", None)]


def test_calls_that_are_not_sources_are_ignored() -> None:
    """`requests.get` is `http_egress`; a handle is not a read."""
    reads = _reads(
        "import requests\n"
        "\n"
        "def f(url, q):\n"
        "    client = chromadb.Client()\n"
        "    requests.get(url)\n"
        "    db.query(q)\n"
    )

    assert reads == []


def test_the_read_record_is_frozen() -> None:
    read = _reads("retriever.invoke('q')\n")[0]

    with pytest.raises(Exception):
        read.source_type = "other"  # type: ignore[misc]


def test_every_declared_pattern_matches_its_own_family() -> None:
    """The table is the single source of truth, and it is consistent.

    Every pattern of every rule resolves back to the source type the
    table declares — the property that keeps `reads_untrusted` (credited
    by the extractor) and the node's `source_type` (named here) from
    drifting apart.
    """
    for source_type, rule in patterns.UNTRUSTED_SOURCE_RULES:
        assert rule.capability == "reads_untrusted"
        for pattern in rule.patterns:
            match = patterns.untrusted_source_match(pattern.text)
            assert match is not None, pattern.text
            assert match[0] == source_type, pattern.text


def test_the_flat_view_is_the_union_of_the_families() -> None:
    expected = [
        pattern.text
        for _, rule in patterns.UNTRUSTED_SOURCE_RULES
        for pattern in rule.patterns
    ]

    assert list(patterns.CAPABILITY_READS_UNTRUSTED) == expected
