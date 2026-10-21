"""Reporting — the chain-groups section and the bundle (1.3e).

Everything the report says about a group has to be traceable to a row,
and a row has two owners: 1.3c chose it, 1.3e evaluated it. These tests
pin that the section keeps those apart — the summary counts validated
groups only, an unvalidated verdict is listed rather than counted, and a
selected-but-not-evaluated row is neither — plus the two JSON files the
bundle writes.
"""

from __future__ import annotations

import json

from aicorellator import GraphStore
from aicorellator.graph import ChainGroup, Edge, Node
from aicorellator.reporting import render_report, write_bundle
from aicorellator.reporting.markdown import MAX_REASONING_CHARS
from aicorellator.workspace import Workspace


SOURCE = "untrusted_source:" + "s" * 16
ENTRY = "entry_point:" + "e" * 16
READER = "tool:" + "r" * 16
SINK = "sink:" + "f" * 16
E_WRITE = "edge:" + "1" * 16
E_READ_1 = "edge:" + "2" * 16
E_READ_2 = "edge:" + "3" * 16


def _graph(store: GraphStore) -> None:
    """The two sides a group needs: a writer into the anchor, a reader out.

        POST /upload --writes_to--> rag_corpus --has_access_to-->
        get_documents --has_access_to(data)--> run_shell
    """
    store.nodes.upsert(Node(
        id=SOURCE, kind="untrusted_source", name="rag_corpus:catalogue.list",
        host_id="localhost", attributes={"source_type": "rag_corpus"},
    ))
    store.nodes.upsert(Node(
        id=ENTRY, kind="entry_point", name="POST /upload", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=READER, kind="tool", name="get_documents", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id=SINK, kind="sink", name="run_shell", host_id="localhost",
    ))
    store.edges.upsert(Edge(
        id=E_WRITE, source_id=ENTRY, target_id=SOURCE, kind="writes_to",
        host_id="localhost", flow_kind="control",
    ))
    store.edges.upsert(Edge(
        id=E_READ_1, source_id=SOURCE, target_id=READER,
        kind="has_access_to", host_id="localhost", flow_kind="control",
    ))
    store.edges.upsert(Edge(
        id=E_READ_2, source_id=READER, target_id=SINK, kind="has_access_to",
        host_id="localhost", flow_kind="data",
        data_flow_confirmed="parameter_match",
    ))


def _group(
    store: GraphStore,
    group_id: str = "chain_group:" + "g" * 16,
    score: float = 7.0,
    priority: str | None = "low",
) -> ChainGroup:
    """An enumerated, scored group whose chains exist in the graph."""
    write_nodes = [ENTRY, SOURCE]
    read_nodes = [SOURCE, READER, SINK]
    return ChainGroup(
        group_id=group_id,
        untrusted_source_id=SOURCE,
        untrusted_source_name="rag_corpus:catalogue.list",
        write_chain_ids=[store.query._chain_id(write_nodes, [E_WRITE])],
        read_chain_ids=[
            store.query._chain_id(read_nodes, [E_READ_1, E_READ_2]),
        ],
        write_chains=[write_nodes],
        read_chains=[read_nodes],
        score=score,
        score_components={"writes_db": 3, "user_readable_source": 1, "total": 4},
        priority=priority,
    )


def _store(
    store: GraphStore,
    group: ChainGroup,
    verdict: str | None = None,
    validated: bool = False,
    confidence: float = 0.0,
    reasoning: str | None = None,
) -> None:
    """Persist a group the way the two owners do: 1.3c, then 1.3e."""
    record = group.to_record()
    if verdict is not None:
        record.verdict = verdict
        record.confidence = confidence
        record.reasoning = reasoning
        record.validated = validated
    store.chain_groups.upsert(record)


def test_report_omits_the_section_without_rows(store: GraphStore) -> None:
    """No rows means 1.3c has not run here — nothing to report."""
    _graph(store)

    report = render_report(store)

    assert "## Chain groups" not in report


def test_report_renders_the_pair_and_the_verdict(store: GraphStore) -> None:
    """A group is drawn as a source, two chains and a verdict line."""
    _graph(store)
    group = _group(store)
    _store(
        store, group, verdict="exploitable", validated=True, confidence=0.85,
        reasoning="the writer plants a payload the reader executes",
    )

    report = render_report(store)

    assert "## Chain groups (Phase 3)" in report
    assert "(rag_corpus)" in report
    assert (
        "`entry_point:POST /upload` → "
        "`untrusted_source:rag_corpus:catalogue.list`" in report
    )
    assert (
        "`untrusted_source:rag_corpus:catalogue.list` → "
        "`tool:get_documents` → `sink:run_shell`" in report
    )
    assert "`exploitable`" in report
    assert "score=7 · priority=low · conf=0.85 · validated=✓" in report
    assert "the writer plants a payload the reader executes" in report


def test_summary_counts_validated_groups_only(store: GraphStore) -> None:
    """An unvalidated verdict is listed, not counted (Layer 3 posture)."""
    _graph(store)
    good = _group(store, "chain_group:" + "1" * 16)
    bad = _group(store, "chain_group:" + "2" * 16, score=4.0)
    _store(
        store, good, verdict="exploitable", validated=True, confidence=0.9,
        reasoning="r",
    )
    _store(
        store, bad, verdict="exploitable", validated=False, confidence=0.9,
        reasoning="cited an edge outside the pair",
    )

    report = render_report(store)

    assert "| Groups selected | 2 |" in report
    assert "| Evaluated | 2 |" in report
    assert "| Validated (Layer 3) | 1 |" in report
    assert "| `exploitable` | 1 |" in report
    assert "**Rejected by Layer 3: 1.**" in report
    assert bad.group_id in report


def test_an_unevaluated_group_is_not_a_rejected_one(store: GraphStore) -> None:
    """Selected, no verdict yet: counted as pending, not as rejected."""
    _graph(store)
    group = _group(store)
    _store(store, group)

    report = render_report(store)

    assert "| Evaluated | 0 |" in report
    assert "| Not evaluated yet | 1 |" in report
    assert "Rejected by Layer 3" not in report
    assert "`unevaluated`" in report


def test_reasoning_is_truncated(store: GraphStore) -> None:
    """A merged voter transcript is cut to ~300 chars, like run_eval's lines."""
    _graph(store)
    group = _group(store)
    long_reasoning = "x" * (MAX_REASONING_CHARS + 120) + "TAIL"
    _store(
        store, group, verdict="uncertain", validated=True, confidence=0.5,
        reasoning=long_reasoning,
    )

    report = render_report(store)

    assert "x" * MAX_REASONING_CHARS in report
    assert "TAIL" not in report
    assert "…" in report


def test_an_unresolvable_chain_is_printed_as_an_id(store: GraphStore) -> None:
    """A row whose chains the graph no longer enumerates still reports."""
    _graph(store)
    group = _group(store)
    group.write_chain_ids = ["chain:" + "d" * 16]
    group.write_chains = []
    _store(store, group, verdict="uncertain", validated=False, confidence=0.2)

    report = render_report(store)

    assert "chain:" + "d" * 16 in report
    assert "Read chain:" in report


# ------------------------------------------------------------
# The bundle (chains.json + chain_groups.json)
# ------------------------------------------------------------

def test_bundle_writes_both_json_files(store: GraphStore, tmp_path) -> None:
    """`chain_groups.json` sits next to `chains.json` (1.3e)."""
    _graph(store)
    group = _group(store)
    _store(
        store, group, verdict="exploitable", validated=True, confidence=0.7,
        reasoning="r",
    )

    path = write_bundle(store, Workspace(root=tmp_path))

    assert (path / "report.md").exists()
    assert (path / "chains.json").exists()
    groups = json.loads((path / "chain_groups.json").read_text())
    assert len(groups) == 1

    entry = groups[0]
    assert entry["id"] == group.group_id
    assert entry["verdict"] == "exploitable"
    assert entry["confidence"] == 0.7
    assert entry["validated"] is True
    assert entry["score"] == 7.0
    assert entry["priority"] == "low"
    assert entry["status"] == "active"
    assert entry["score_components"]["writes_db"] == 3
    assert entry["reasoning"] == "r"

    # The anchor resolved, the chains resolved to node references, and the
    # ids kept beside them.
    assert entry["untrusted_source"]["name"] == "rag_corpus:catalogue.list"
    assert entry["untrusted_source"]["source_type"] == "rag_corpus"
    assert entry["write_chain_ids"] == group.write_chain_ids
    assert entry["read_chain_ids"] == group.read_chain_ids
    assert [n["name"] for n in entry["write_chains"][0]["nodes"]] == [
        "POST /upload", "rag_corpus:catalogue.list",
    ]
    assert [n["name"] for n in entry["read_chains"][0]["nodes"]] == [
        "rag_corpus:catalogue.list", "get_documents", "run_shell",
    ]
    assert entry["write_chains"][0]["id"] == group.write_chain_ids[0]

    # report.md in the bundle is the same document, section included.
    assert "## Chain groups (Phase 3)" in (path / "report.md").read_text()


def test_bundle_writes_an_empty_array_without_groups(
    store: GraphStore,
    tmp_path,
) -> None:
    """No rows is an empty list, not a missing file."""
    _graph(store)

    path = write_bundle(store, Workspace(root=tmp_path))

    assert json.loads((path / "chain_groups.json").read_text()) == []
    assert json.loads((path / "chains.json").read_text()) == []


def test_bundle_keeps_an_unresolvable_chain_as_an_id(
    store: GraphStore,
    tmp_path,
) -> None:
    """A chain the enumerator no longer produces is still named."""
    _graph(store)
    group = _group(store)
    group.write_chain_ids = ["chain:" + "d" * 16]
    group.write_chains = []
    _store(store, group, verdict="uncertain", validated=False, confidence=0.2)

    path = write_bundle(store, Workspace(root=tmp_path))

    entry = json.loads((path / "chain_groups.json").read_text())[0]
    assert entry["write_chains"] == [{"id": "chain:" + "d" * 16, "nodes": []}]