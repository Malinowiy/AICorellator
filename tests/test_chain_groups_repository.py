"""Chain groups repository — CRUD over `chain_groups` (1.3c).

The repository is a row mover: it does not score and it does not
decide what a score means. What it does own is the *split* of the row
between 1.3c's columns and the evaluation columns 1.3d/1.3e will
write, which is what most of these tests pin down.
"""

from __future__ import annotations

import sqlite3

import pytest

from aicorellator import GraphStore
from aicorellator.graph import ChainGroupRecord, Node


SOURCE = "untrusted_source:" + "s" * 16


@pytest.fixture
def source(store: GraphStore) -> str:
    """The anchor every group in this file hangs off."""
    store.nodes.upsert(Node(
        id=SOURCE,
        kind="untrusted_source",
        name="rag_corpus:vectorstore.search",
        host_id="localhost",
        attributes={"source_type": "rag_corpus"},
    ))
    return SOURCE


def _record(group_id: str = "chain_group:" + "1" * 16) -> ChainGroupRecord:
    return ChainGroupRecord(
        id=group_id,
        name="rag_corpus:vectorstore.search:3x4",
        untrusted_source_id=SOURCE,
        write_chain_ids=["chain:" + "w" * 16],
        read_chain_ids=["chain:" + "r" * 16],
        score=7.0,
        score_components={"lethal_trifecta": 0, "total": 7},
        priority="low",
    )


def test_add_and_get(store: GraphStore, source: str) -> None:
    store.chain_groups.add(_record())

    fetched = store.chain_groups.get("chain_group:" + "1" * 16)

    assert fetched is not None
    assert fetched.name == "rag_corpus:vectorstore.search:3x4"
    assert fetched.untrusted_source_id == source
    assert fetched.write_chain_ids == ["chain:" + "w" * 16]
    assert fetched.read_chain_ids == ["chain:" + "r" * 16]
    assert fetched.score == 7.0
    assert fetched.score_components == {"lethal_trifecta": 0, "total": 7}
    assert fetched.priority == "low"
    assert fetched.status == "active"
    assert fetched.lethal_trifecta is False
    assert fetched.validated is False
    # B.7: the stored "not computed" pair, the same defaults `chains`
    # carries — 1.3c writes neither column and must not have to.
    assert fetched.prompt_version == ""
    assert fetched.voter_outputs == []


def test_the_provenance_columns_round_trip(
    store: GraphStore,
    source: str,
) -> None:
    """B.7: the prompt and the voter responses survive write and read.

    `to_row` encodes `voter_outputs` as JSON, so this is also the check
    that the column holds the shape `chains.voter_outputs` holds — a JSON
    array of per-voter dicts — rather than a repr the reader would have
    to parse back.
    """
    record = _record()
    record.verdict = "exploitable"
    record.prompt_version = "v1.6.0"
    record.voter_outputs = [{
        "model": "v0",
        "confidence": 0.8,
        "evidence": ["edge:" + "2" * 16],
        "evidence_valid": True,
        "evidence_unknown": [],
    }]
    store.chain_groups.add(record)

    fetched = store.chain_groups.get(record.id)

    assert fetched.prompt_version == "v1.6.0"
    assert fetched.voter_outputs == record.voter_outputs


def test_get_missing_is_none(store: GraphStore) -> None:
    assert store.chain_groups.get("chain_group:nope") is None


def test_add_rejects_a_duplicate_id(store: GraphStore, source: str) -> None:
    store.chain_groups.add(_record())

    with pytest.raises(sqlite3.IntegrityError):
        store.chain_groups.add(_record())


def test_upsert_inserts_when_absent(store: GraphStore, source: str) -> None:
    store.chain_groups.upsert(_record())

    assert store.chain_groups.count() == 1


def test_upsert_refreshes_the_prioritization_columns(
    store: GraphStore,
    source: str,
) -> None:
    store.chain_groups.upsert(_record())

    updated = _record()
    updated.score = 15.0
    updated.score_components = {"lethal_trifecta": 5, "total": 15}
    updated.priority = "high"
    store.chain_groups.upsert(updated)

    fetched = store.chain_groups.get(updated.id)
    assert store.chain_groups.count() == 1
    assert fetched.score == 15.0
    assert fetched.score_components == {"lethal_trifecta": 5, "total": 15}
    assert fetched.priority == "high"


def test_upsert_leaves_the_evaluation_columns_alone(
    store: GraphStore,
    source: str,
) -> None:
    """A second prioritization run must not erase a paid-for verdict."""
    store.chain_groups.upsert(_record())
    store._conn.execute(
        """
        UPDATE chain_groups SET
            lethal_trifecta = 1, verdict = 'exploitable',
            confidence = 0.9, reasoning = 'the read side runs a shell',
            voter_outputs = '[{"model": "v0", "confidence": 0.9}]',
            prompt_version = 'v1.6.0',
            validated = 1
        WHERE id = :id
        """,
        {"id": _record().id},
    )

    updated = _record()
    updated.score = 12.0
    store.chain_groups.upsert(updated)

    fetched = store.chain_groups.get(updated.id)
    assert fetched.score == 12.0
    assert fetched.lethal_trifecta is True
    assert fetched.verdict == "exploitable"
    assert fetched.confidence == 0.9
    assert fetched.reasoning == "the read side runs a shell"
    # B.7: the provenance travels with the verdict it belongs to, so a
    # prioritization re-run cannot leave a verdict with no prompt and no
    # responses behind it.
    assert fetched.prompt_version == "v1.6.0"
    assert fetched.voter_outputs == [{"model": "v0", "confidence": 0.9}]
    assert fetched.validated is True


def test_upsert_writes_a_verdict(store: GraphStore, source: str) -> None:
    """1.3e's write lands on the row 1.3c created (1.3e).

    Prioritization writes a row with no verdict; the evaluator writes the
    same row back with one. The verdict columns are the only difference
    the second write is allowed to make to them.
    """
    store.chain_groups.upsert(_record())

    evaluated = _record()
    evaluated.verdict = "exploitable"
    evaluated.confidence = 0.9
    evaluated.reasoning = "the read side runs a shell"
    evaluated.validated = True
    store.chain_groups.upsert(evaluated)

    fetched = store.chain_groups.get(evaluated.id)
    assert store.chain_groups.count() == 1
    assert fetched.verdict == "exploitable"
    assert fetched.confidence == 0.9
    assert fetched.reasoning == "the read side runs a shell"
    assert fetched.validated is True
    # 1.3c's columns are refreshed from the incoming record, which is the
    # same group, so they are unchanged.
    assert fetched.score == 7.0
    assert fetched.priority == "low"


def test_upsert_carries_validated_with_the_verdict(
    store: GraphStore,
    source: str,
) -> None:
    """`validated` is not written on its own — it travels with a verdict.

    A row that was validated by an earlier run is marked unvalidated by a
    new verdict that Layer 3 rejected, because the two describe the same
    evaluation.
    """
    store.chain_groups.upsert(_record())
    store._conn.execute(
        "UPDATE chain_groups SET verdict = 'exploitable', validated = 1"
        " WHERE id = :id",
        {"id": _record().id},
    )

    rejected = _record()
    rejected.verdict = "uncertain"
    rejected.validated = False
    store.chain_groups.upsert(rejected)

    fetched = store.chain_groups.get(rejected.id)
    assert fetched.verdict == "uncertain"
    assert fetched.validated is False


def test_upsert_keeps_created_at(store: GraphStore, source: str) -> None:
    store.chain_groups.upsert(_record())
    created = store.chain_groups.get(_record().id).created_at

    store.chain_groups.upsert(_record())

    assert store.chain_groups.get(_record().id).created_at == created


def test_mark_stale(store: GraphStore, source: str) -> None:
    store.chain_groups.upsert(_record())

    store.chain_groups.mark_stale(_record().id)

    assert store.chain_groups.get(_record().id).status == "stale"
    assert store.chain_groups.count(status="stale") == 1
    assert store.chain_groups.count(status="active") == 0


# ------------------------------------------------------------
# Listing
# ------------------------------------------------------------

def _three(store: GraphStore) -> list[ChainGroupRecord]:
    records = []
    for i, (score, priority) in enumerate(
        [(2.0, None), (15.0, "high"), (10.0, "medium")]
    ):
        record = _record(f"chain_group:{str(i) * 16}")
        record.score = score
        record.priority = priority
        store.chain_groups.upsert(record)
        records.append(record)
    return records


def test_list_is_ordered_by_score_then_id(
    store: GraphStore, source: str
) -> None:
    _three(store)

    listed = store.chain_groups.list()

    assert [r.score for r in listed] == [15.0, 10.0, 2.0]


def test_list_filters(store: GraphStore, source: str) -> None:
    _three(store)

    assert len(store.chain_groups.list(priority="high")) == 1
    assert len(store.chain_groups.list(min_score=10.0)) == 2
    assert len(store.chain_groups.list(status="active")) == 3
    assert len(store.chain_groups.list(limit=1)) == 1
    assert store.chain_groups.list(status="stale") == []


def test_list_by_source(store: GraphStore, source: str) -> None:
    _three(store)
    other = "untrusted_source:" + "o" * 16
    store.nodes.upsert(Node(
        id=other, kind="untrusted_source", name="email_inbox:imap.search",
        host_id="localhost",
    ))
    elsewhere = _record("chain_group:" + "9" * 16)
    elsewhere.untrusted_source_id = other
    elsewhere.score = 99.0
    store.chain_groups.upsert(elsewhere)

    listed = store.chain_groups.list_by_source(source)

    assert [r.id for r in listed] == [
        r.id for r in store.chain_groups.list() if r.score != 99.0
    ]
    assert store.chain_groups.count(source_id=other) == 1


def test_count_by_status(store: GraphStore, source: str) -> None:
    _three(store)
    store.chain_groups.mark_stale("chain_group:" + "0" * 16)

    assert store.chain_groups.count() == 3
    assert store.chain_groups.count(status="active") == 2
    assert store.chain_groups.count(status="stale") == 1


# ------------------------------------------------------------
# Schema constraints and cascade
# ------------------------------------------------------------

def test_priority_check_round_trips_and_rejects_a_bad_bucket(
    store: GraphStore, source: str
) -> None:
    record = _record()
    record.priority = None
    store.chain_groups.upsert(record)
    assert store.chain_groups.get(record.id).priority is None

    record.priority = "urgent"
    with pytest.raises(sqlite3.IntegrityError):
        store.chain_groups.upsert(record)


def test_a_negative_score_is_storable(
    store: GraphStore, source: str
) -> None:
    """ADR-0012 scores are not clamped at 0 — the column has no CHECK."""
    record = _record()
    record.score = -2.0
    store.chain_groups.upsert(record)

    assert store.chain_groups.get(record.id).score == -2.0


def test_deleting_the_source_cascades(store: GraphStore, source: str) -> None:
    store.chain_groups.upsert(_record())

    store._conn.execute("DELETE FROM nodes WHERE id = :id", {"id": source})

    assert store.chain_groups.count() == 0


def test_score_components_round_trips_as_json(
    store: GraphStore, source: str
) -> None:
    record = _record()
    record.score_components = {
        "lethal_trifecta": 5,
        "total": 5,
        "reasons": {"lethal_trifecta": "reads_untrusted at tool:x"},
    }
    store.chain_groups.upsert(record)

    fetched = store.chain_groups.get(record.id)
    assert fetched.score_components == record.score_components
