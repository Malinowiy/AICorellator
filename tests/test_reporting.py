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
from aicorellator.graph import Chain, ChainGroup, Edge, Node
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


def _graph(store: GraphStore, trifecta: bool = False) -> None:
    """The two sides a group needs: a writer into the anchor, a reader out.

        POST /upload --writes_to--> rag_corpus --has_access_to-->
        get_documents --has_access_to(data)--> run_shell

    `trifecta` writes 1.3d's flag on the reader — the node that reads
    untrusted content on its way to the sink — which is what B.6's
    projection reads. Default `False` keeps every other test's graph
    exactly as it was.
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
        attributes=({"lethal_trifecta": True} if trifecta else {}),
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
    validated_kind: str | None = None,
    lethal_trifecta: bool = False,
    confidence: float = 0.0,
    reasoning: str | None = None,
    prompt_version: str = "",
    voter_outputs: list[dict] | None = None,
) -> None:
    """Persist a group the way the two owners do: 1.3c, then 1.3e.

    The columns of the evaluated row travel together, the way one
    evaluation writes them: `validated_kind` is A.4's label for the
    Layer 3 check the verdict went through, `lethal_trifecta` is B.6's
    projection of 1.3d's per-node flag, and `prompt_version` /
    `voter_outputs` are B.7's provenance — the row's, written beside the
    verdict rather than derived here (what the evaluator puts in them is
    `test_analysis_evaluator_groups.py`'s to pin).
    """
    record = group.to_record()
    if verdict is not None:
        record.verdict = verdict
        record.confidence = confidence
        record.reasoning = reasoning
        record.validated = validated
        record.validated_kind = validated_kind
        record.lethal_trifecta = lethal_trifecta
        record.prompt_version = prompt_version
        record.voter_outputs = voter_outputs or []
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


def test_bundle_carries_the_projection_the_label_and_the_provenance(
    store: GraphStore,
    tmp_path,
) -> None:
    """B.6/B.7: `chain_groups.json` names the row's evaluation columns.

    B.6: `lethal_trifecta` and `validated_kind` are row columns the
    bundle used to drop, so a consumer of the file could not tell a group
    whose read side has all three halves of the trifecta (ADR-0009) from
    one whose read side does not, nor read the level Layer 3 reached.
    B.7: `prompt_version` / `voter_outputs` are the other two — what the
    verdict is traced by (ADR-0005, ADR-0006). The row carries all four,
    and the file now says all four.

    One note on the label's value: since B.9 a *validated* chain carries
    `validated_kind="evidence"`, and `"semantic"` is the level a chain
    reaches when the evidence check is what rejected it
    (`graph.models.VALID_VALIDATED_KINDS`). The row here is hand-written
    — this test's subject is the bundle's column, not the level — so the
    pair is the pre-B.9 one.
    """
    _graph(store, trifecta=True)
    group = _group(store)
    _store(
        store, group, verdict="exploitable", validated=True,
        validated_kind="semantic", lethal_trifecta=True,
        confidence=0.7, reasoning="r",
        prompt_version="v1.6.0",
        voter_outputs=[{"model": "v0", "confidence": 0.8, "evidence": []}],
    )

    path = write_bundle(store, Workspace(root=tmp_path))

    entry = json.loads((path / "chain_groups.json").read_text())[0]
    assert entry["lethal_trifecta"] is True
    assert entry["validated_kind"] == "semantic"
    assert entry["prompt_version"] == "v1.6.0"
    assert entry["voter_outputs"] == [
        {"model": "v0", "confidence": 0.8, "evidence": []}
    ]


def test_bundle_says_the_not_computed_shape_before_an_evaluation(
    store: GraphStore,
    tmp_path,
) -> None:
    """1.3c's row: no projection has run, no label, no provenance.

    The graph here *has* a trifecta reader, and the file still says
    `False` — the column is the evaluator's projection, and a row 1.3e
    has not reached carries "not computed" rather than a claim about the
    graph. The label is `null` for the same reason: no verdict, so no
    Layer 3 check has run — and the file mirrors the stored column,
    where it is NULL. (Reading the row through the model would turn that
    NULL into "existence", `from_row`'s default for a row written before
    A.4; a row 1.3c wrote is not one of those.) B.7's two are the same
    "not computed": the stored pair `''` / `[]`, emitted rather than
    omitted so a consumer never has to tell "not asked" from "asked and
    empty".
    """
    _graph(store, trifecta=True)
    group = _group(store)
    _store(store, group)  # 1.3c only: no verdict.

    path = write_bundle(store, Workspace(root=tmp_path))

    entry = json.loads((path / "chain_groups.json").read_text())[0]
    assert entry["lethal_trifecta"] is False
    assert entry["validated_kind"] is None
    assert entry["prompt_version"] == ""
    assert entry["voter_outputs"] == []


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


# ------------------------------------------------------------
# Evaluated chains — the Layer 3 notes (A.4)
# ------------------------------------------------------------

def _rejected_chain(
    validated: bool = False,
    validated_kind: str = "existence",
    validation_notes: list[str] | None = None,
) -> Chain:
    """A chain over the same graph, as the evaluator stores one."""
    return Chain(
        id="chain:" + "c" * 16,
        chain_nodes=[ENTRY, SOURCE],
        chain_edges=[E_WRITE],
        verdict="exploitable",
        prompt_version="v1",
        confidence=0.9,
        reasoning="the upload lands in the corpus",
        validated=validated,
        validated_kind=validated_kind,
        validation_notes=validation_notes or [],
    )


def test_report_prints_the_notes_of_a_rejected_chain(
    store: GraphStore,
) -> None:
    """A rejected chain is stored, so its reasons are rendered (A.4).

    The ✗ beside the verdict says Layer 3 refused it; the notes say by
    which check, under the reasoning and in the same block.
    """
    _graph(store)
    note = (
        f"disconnected: edge {E_WRITE} links {ENTRY} → {SOURCE}, "
        f"expected {SOURCE} → {ENTRY}"
    )
    store.chains.add(_rejected_chain(validation_notes=[note]))

    report = render_report(store)

    assert f"> **Layer 3:** {note}" in report
    assert report.index("> **Reasoning:**") < report.index("> **Layer 3:**")


def test_report_prints_notes_one_line_each(store: GraphStore) -> None:
    """One failed check, one line — the shape the array holds."""
    _graph(store)
    store.chains.add(_rejected_chain(validation_notes=[
        "disconnected: edge edge:x is not in the graph",
        f"last node {SOURCE} is not a sink",
    ]))

    report = render_report(store)

    assert (
        "> **Layer 3:** disconnected: edge edge:x is not in the graph"
        in report
    )
    assert f"> **Layer 3:** last node {SOURCE} is not a sink" in report


def test_report_prints_nothing_for_a_chain_that_passed(
    store: GraphStore,
) -> None:
    """A validated chain has no notes, so its block is what it was."""
    _graph(store)
    store.chains.add(_rejected_chain(
        validated=True, validated_kind="semantic",
    ))

    report = render_report(store)

    assert "**Layer 3:**" not in report