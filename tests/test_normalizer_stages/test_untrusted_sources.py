"""UntrustedSourceStage: `untrusted_source` nodes and their readers (1.3a).

The stage is the second half of the 1.3a pair — `patterns
.UNTRUSTED_SOURCE_RULES` decides *what* a source is, the extractor credits
`reads_untrusted` on the reader, and this stage names the node after the
same table. These tests cover the node, the edge, the reader resolution
order, and the dedup.
"""

from __future__ import annotations

from aicorellator.graph.models import Node
from aicorellator.parsing.entry_points import EntryPoint, EntryPointOutput
from aicorellator.parsing.normalizer_helpers import untrusted_source_id
from aicorellator.parsing.normalizer_stages import (
    FlowKindStage,
    UntrustedSourceStage,
)

CORPUS_SOURCE = '''\
def read_docs(question):
    return retriever.invoke(question)
'''

MAILBOX_SOURCE = '''\
import imaplib


class MailboxTool:
    def search_inbox(self, query):
        imap = imaplib.IMAP4_SSL("imap.example.com")
        return imap.search(None, query)
'''

EGRESS_SOURCE = '''\
def fetch_weather(city):
    return requests.get(city)
'''


def _node(kind: str, name: str, file_path: str = "tools.py", **attrs) -> Node:
    return Node(
        id=f"{kind}:{name}",
        kind=kind,
        name=name,
        host_id="localhost",
        attributes={"source_file": file_path, "line_start": 1, **attrs},
    )


def _sources(delta) -> list[Node]:
    return [n for n in delta.nodes if n.kind == "untrusted_source"]


def test_a_read_gets_a_node_and_an_edge_to_the_tool(project) -> None:
    """`retriever.invoke` in a tool body: one source node, one edge."""
    p = project(files={"tools.py": CORPUS_SOURCE})
    p.delta.nodes.append(_node("tool", "read_docs"))

    delta = UntrustedSourceStage().apply(p.delta, p.ctx)

    sources = _sources(delta)
    assert [n.name for n in sources] == ["rag_corpus:retriever.invoke"]
    source = sources[0]
    assert source.id == untrusted_source_id(
        "rag_corpus", "retriever.invoke", "tools.py"
    )
    assert source.attributes["source_type"] == "rag_corpus"
    assert source.attributes["call_target"] == "retriever.invoke"
    assert source.attributes["source_file"] == "tools.py"
    assert source.attributes["reader"] == "read_docs"
    assert source.attributes["reader_kind"] == "function"
    assert source.attributes["line_start"] == 2
    assert source.host_id == "localhost"
    # The capability belongs to the reader, not to the place (see the
    # stage docstring): the source carries none.
    assert source.capabilities == []

    assert [(e.kind, e.source_id, e.target_id) for e in delta.edges] == [
        ("has_access_to", source.id, "tool:read_docs"),
    ]
    assert delta.edges[0].data_flow_confirmed == "call_result_match"


def test_flow_kind_is_data_once_the_deriving_stage_runs(project) -> None:
    """The stage sets the evidence; `FlowKindStage` derives the kind."""
    p = project(files={"tools.py": CORPUS_SOURCE})
    p.delta.nodes.append(_node("tool", "read_docs"))

    UntrustedSourceStage().apply(p.delta, p.ctx)
    FlowKindStage().apply(p.delta, p.ctx)

    assert [e.flow_kind for e in p.delta.edges] == ["data"]


def test_a_method_read_links_to_the_class_when_the_method_has_no_node(
    project,
) -> None:
    """An unclassified method's read is the class's capability."""
    p = project(files={"tools.py": MAILBOX_SOURCE})
    p.delta.nodes.append(_node("agent", "MailboxTool"))

    delta = UntrustedSourceStage().apply(p.delta, p.ctx)

    assert [n.name for n in _sources(delta)] == [
        "email_inbox:imaplib.IMAP4_SSL",
        "email_inbox:imap.search",
    ]
    assert [e.target_id for e in delta.edges] == ["agent:MailboxTool"] * 2
    assert _sources(delta)[0].attributes["reader_kind"] == "method"
    assert _sources(delta)[0].attributes["reader_container"] == "MailboxTool"
    assert _sources(delta)[0].attributes["reader"] == "search_inbox"


def test_a_read_in_a_route_handler_links_to_the_entry_point(project) -> None:
    """The handler has no node; the entry point does, via `handler`."""
    p = project(
        files={"app.py": CORPUS_SOURCE},
        entry_points=EntryPointOutput(entry_points=[EntryPoint(
            method="GET", path="/ask", handler="read_docs",
            file_path="app.py", line=1,
        )]),
    )
    p.delta.nodes.append(_node(
        "entry_point", "GET /ask", file_path="app.py", handler="read_docs",
    ))

    delta = UntrustedSourceStage().apply(p.delta, p.ctx)

    assert [e.target_id for e in delta.edges] == ["entry_point:GET /ask"]


def test_a_method_node_wins_over_an_entry_point_of_the_same_name(
    project,
) -> None:
    """First write wins, and `delta.nodes` is in stage order."""
    p = project(files={"app.py": CORPUS_SOURCE})
    p.delta.nodes.extend([
        _node("tool", "read_docs", file_path="app.py"),
        _node(
            "entry_point", "GET /ask", file_path="app.py",
            handler="read_docs",
        ),
    ])

    delta = UntrustedSourceStage().apply(p.delta, p.ctx)

    assert [e.target_id for e in delta.edges] == ["tool:read_docs"]


def test_a_read_with_no_reader_node_still_produces_the_node(project) -> None:
    """A script has no node; the read is still a fact about the file."""
    p = project(files={"tools.py": MAILBOX_SOURCE})

    delta = UntrustedSourceStage().apply(p.delta, p.ctx)

    assert len(_sources(delta)) == 2
    assert delta.edges == []


def test_a_read_in_another_file_is_a_different_node(project) -> None:
    """The read site is the identity, so the file is part of it."""
    p = project(files={
        "tools.py": CORPUS_SOURCE,
        "other.py": "def ask(q):\n    return retriever.invoke(q)\n",
    })
    p.delta.nodes.extend([
        _node("tool", "read_docs"),
        _node("tool", "ask", file_path="other.py"),
    ])

    delta = UntrustedSourceStage().apply(p.delta, p.ctx)

    rag = [
        n for n in _sources(delta)
        if n.attributes["source_type"] == "rag_corpus"
    ]
    assert {n.attributes["source_file"] for n in rag} == {
        "tools.py", "other.py",
    }
    assert len({n.id for n in rag}) == 2
    assert sorted(e.target_id for e in delta.edges) == [
        "tool:ask", "tool:read_docs",
    ]


def test_a_read_inside_a_nested_function_belongs_to_its_definition(
    project,
) -> None:
    """`ast.walk` descends: the enclosing symbol is the reader."""
    p = project(files={"tools.py": (
        "def read_docs(q):\n"
        "    def inner(x):\n"
        "        return retriever.invoke(x)\n"
        "    return inner(q)\n"
    )})
    p.delta.nodes.append(_node("tool", "read_docs"))

    delta = UntrustedSourceStage().apply(p.delta, p.ctx)

    assert [e.target_id for e in delta.edges] == ["tool:read_docs"]
    assert _sources(delta)[0].attributes["reader"] == "read_docs"


def test_calls_that_are_not_sources_produce_nothing(project) -> None:
    """`requests.get` is `http_egress`, not an untrusted source."""
    p = project(files={"tools.py": EGRESS_SOURCE})
    p.delta.nodes.append(_node("tool", "fetch_weather"))

    delta = UntrustedSourceStage().apply(p.delta, p.ctx)

    assert _sources(delta) == []
    assert delta.edges == []
