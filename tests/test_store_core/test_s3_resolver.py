"""The `s3` family through the resolver (B.3b-s3, E1–E3).

The family reports call sites; `StoreResolver` is what turns two of them
into a `store` node and the two edges that name it. This file is that
join for the second family, over files written to `tmp_path`, with the
node side hand-built the way the pipeline would have it: a component node
for the symbol the write call lives in, and an `untrusted_source` node
for the read site. It is `test_fs_resolver.py`'s file, family for family.

What is pinned:

  - **E1** — one write and one read of the same `Bucket` / `Key` in the
    same module make one store and two edges, at the confidence §8's
    signals give them (`1 − 0.7 × 0.4 × 0.8 = 0.776`: equal literals and
    the same module), and the query layer joins them into one group;
  - **E2** — a write with no read is a store with one edge, and a read
    with no write likewise: a store is not a group;
  - **E3** — a read whose call site the graph did not register as a
    source produces no `reads_from`;
  - **the identity** — two buckets are two stores, and a bucket against a
    bucket plus a key is the same two stores with both edges each: a site
    attaches to the store its literal *equals* and to every store
    `literal_match`'s containment branch relates it to, which the resolver
    reaches since B.3b-fs-followup;
  - **the wildcard** — a site with an unresolved bucket (`None`)
    attaches to every store its family has (§5), at a lower confidence.

The registry is not involved: the resolver is handed the family, the way
`test_fs_resolver.py` hands it `fs`. That the real registry holds `s3` is
pinned in `tests/test_store_families/test_s3.py`.
"""

from __future__ import annotations

from aicorellator.graph.models import Edge, Node
from aicorellator.parsing import write_graph
from aicorellator.store.confidence import (
    BASE,
    CODE_CONTEXT_SAME_MODULE,
    LITERAL_EQUAL,
    LITERAL_ONE_SIDE,
    LITERAL_PREFIX,
    combine_signals,
    literal_match,
)
from aicorellator.store.families.s3 import S3Family
from aicorellator.store.resolver import StoreResolver

HANDLER = "app/handler.py"
BUCKET = "docs"
KEY = "uploads/report.txt"
LITERAL = "docs/uploads/report.txt"

# `handle` writes on line 2 and reads on line 3, both inside the one
# definition that starts on line 1, and both naming one store.
HANDLER_SOURCE = f'''\
def handle(payload):
    s3.put_object(Bucket="{BUCKET}", Key="{KEY}", Body=payload)
    return s3.get_object(Bucket="{BUCKET}", Key="{KEY}")
'''

WRITE_LINE = 2
READ_LINE = 3
DEF_LINE = 1


def _scene(scan, source: str = HANDLER_SOURCE, nodes=None):
    """A scan over one handler, with the pipeline's nodes."""
    built = scan(files={HANDLER: source})
    built.delta.nodes.extend(
        nodes if nodes is not None else [_writer_node(), _source_node()]
    )
    return built


def _writer_node() -> Node:
    return Node(
        id="tool:handle", kind="tool", name="handle", host_id="localhost",
        attributes={"source_file": HANDLER, "line_start": DEF_LINE},
    )


def _source_node(line: int = READ_LINE) -> Node:
    return Node(
        id="untrusted_source:1", kind="untrusted_source",
        name="user_upload:s3", host_id="localhost",
        attributes={
            "source_file": HANDLER,
            "source_type": "user_upload",
            "line_start": line,
        },
    )


def _resolve(built):
    """Resolve, and keep the edges the way `StoreResolverStage` does.

    The resolver returns the edges and puts its store *nodes* in the
    delta; the stage is what appends the edges. A test that only read
    the return value would leave the graph without them, so this does
    both — the same two lines the stage runs.
    """
    edges = StoreResolver([S3Family()]).resolve(built.delta, built.ctx)
    built.delta.edges.extend(edges)
    return edges


# ============================================================
# E1 — one write, one read, one literal
# ============================================================

def test_one_store_and_two_edges(scan) -> None:
    """The family's two sites become one store (§1) and two edges."""
    built = _scene(scan)
    edges = _resolve(built)

    stores = built.nodes("store")
    assert [store.name for store in stores] == [f"s3:{LITERAL}"]
    assert stores[0].attributes == {
        "family": "s3", "identity_hint": LITERAL,
    }
    assert stores[0].confidence == 1.0

    assert [edge.kind for edge in edges] == ["writes_to", "reads_from"]
    writes_to, reads_from = edges
    assert writes_to.source_id == "tool:handle"
    assert writes_to.target_id == stores[0].id
    assert reads_from.source_id == stores[0].id
    assert reads_from.target_id == "untrusted_source:1"


def test_the_confidence_is_the_signals_the_pair_earned(scan) -> None:
    """Equal literals (`0.6`) and the same module (`0.2`) over a base.

    The number is computed from `store.confidence`'s constants and not
    from prose: `combine_signals(BASE, [LITERAL_EQUAL,
    CODE_CONTEXT_SAME_MODULE])` is the `0.776` §8's signals give the
    pair, and it is above the default threshold — the shape the whole
    mechanism exists for, one bucket and one key named twice.
    """
    expected = combine_signals(
        BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
    )
    assert round(expected, 4) == 0.776

    built = _scene(scan)
    edges = _resolve(built)
    assert [round(edge.identity_confidence, 4) for edge in edges] == [
        round(expected, 4), round(expected, 4),
    ]


def test_the_edges_are_structural(scan) -> None:
    """ADR-0021 §4, ADR-0015/0016: a declaration, with no data-flow claim."""
    built = _scene(scan)
    edges = _resolve(built)

    assert {edge.flow_kind for edge in edges} == {"structural"}
    assert {edge.observed_by[0] for edge in edges} == {"ast"}
    assert {edge.data_flow_confirmed for edge in edges} == {None}


def test_the_familys_edges_are_a_chain_group(scan, store) -> None:
    """E1 — the two links meet at the store, and that is a group (§6).

    The chains around the store are hand-built, because the family
    produces the two *links* and not the paths they sit on:

        entry_point --has_access_to--> tool:handle --writes_to--> store
        store --reads_from--> source --has_access_to--> reader --> sink
    """
    built = _scene(
        scan,
        nodes=[
            _writer_node(),
            _source_node(),
            Node(
                id="entry_point:1", kind="entry_point", name="POST /upload",
                host_id="localhost",
                attributes={"source_file": HANDLER, "line_start": DEF_LINE},
            ),
            Node(
                id="tool:reader", kind="tool", name="read_report",
                host_id="localhost",
                attributes={"source_file": HANDLER, "line_start": DEF_LINE},
            ),
            Node(
                id="sink:1", kind="sink", name="run_shell",
                host_id="localhost",
                attributes={"source_file": HANDLER, "line_start": DEF_LINE},
            ),
        ],
    )
    built.delta.edges.extend([
        _link("edge:1", "entry_point:1", "tool:handle"),
        _link("edge:2", "untrusted_source:1", "tool:reader"),
        _link("edge:3", "tool:reader", "sink:1"),
    ])
    _resolve(built)
    write_graph(store, built.delta)

    groups = store.query.find_chain_groups()
    assert len(groups) == 1
    group = groups[0]
    assert group.store_id == built.nodes("store")[0].id
    assert group.store_name == f"s3:{LITERAL}"
    assert group.untrusted_source_id == "untrusted_source:1"
    assert group.weak is False
    assert round(group.store_confidence, 4) == 0.776


# ============================================================
# E2 — one side only, and E3 — a read with no source
# ============================================================

def test_a_write_with_no_read_is_a_store_with_one_edge(scan) -> None:
    """The store exists — the literal named it — and nothing reads it.

    §5 keeps the store the literal side named. The confidence is the
    literal alone, `1 − 0.7 × 0.4 = 0.72`: with no counterpart there is
    no code-context signal to add, and the literal is what named it.
    """
    built = _scene(
        scan,
        source=(
            "def handle(payload):\n"
            f'    s3.put_object(Bucket="{BUCKET}", Key="{KEY}")\n'
        ),
    )
    edges = _resolve(built)

    assert [store.name for store in built.nodes("store")] == [f"s3:{LITERAL}"]
    assert [edge.kind for edge in edges] == ["writes_to"]
    assert round(edges[0].identity_confidence, 4) == round(
        combine_signals(BASE, [LITERAL_EQUAL]), 4
    )


def test_a_read_with_no_write_is_a_store_with_one_edge(scan) -> None:
    """And the same from the other side."""
    built = _scene(
        scan,
        source=(
            "def handle():\n"
            f'    return s3.get_object(Bucket="{BUCKET}", Key="{KEY}")\n'
        ),
        nodes=[_source_node(line=2)],
    )
    edges = _resolve(built)

    assert [store.name for store in built.nodes("store")] == [f"s3:{LITERAL}"]
    assert [edge.kind for edge in edges] == ["reads_from"]
    assert edges[0].target_id == "untrusted_source:1"


def test_a_read_the_graph_did_not_register_is_no_reads_from(scan) -> None:
    """E3 — a read points *from* a store; with no source there is no edge.

    A read's role is the graph's (`untrusted_source` nodes, 1.3a), and
    an S3 read is not one of the source types — `user_upload` is
    B.3b-user_upload. The store is still created: the literal named it,
    and the read is a fact about it even when the link has no target.
    """
    built = _scene(scan, nodes=[_writer_node()])
    edges = _resolve(built)

    assert [store.name for store in built.nodes("store")] == [f"s3:{LITERAL}"]
    assert [edge.kind for edge in edges] == ["writes_to"]


# ============================================================
# The identity — two stores, and the containment branch
# ============================================================

def test_two_different_buckets_are_two_stores_and_no_cross_pair(
    scan,
) -> None:
    """Two buckets, two stores; neither side is claimed for the other's.

    The write names `docs` and the read `other`, in the same module: the
    code context agrees about everything and the literals do not. §8's
    rule is "no link", so the two edges that come out name the store
    their own site named.
    """
    built = _scene(
        scan,
        source=(
            "def handle(payload):\n"
            f'    s3.put_object(Bucket="{BUCKET}", Key="{KEY}")\n'
            f'    return s3.get_object(Bucket="other", Key="{KEY}")\n'
        ),
        nodes=[_writer_node(), _source_node(line=3)],
    )
    edges = _resolve(built)

    stores = {store.name: store.id for store in built.nodes("store")}
    assert sorted(stores) == [f"s3:{LITERAL}", f"s3:other/{KEY}"]

    writes_to = [edge for edge in edges if edge.kind == "writes_to"]
    reads_from = [edge for edge in edges if edge.kind == "reads_from"]
    assert [edge.target_id for edge in writes_to] == [stores[f"s3:{LITERAL}"]]
    assert [edge.source_id for edge in reads_from] == [
        stores[f"s3:other/{KEY}"]
    ]


def test_a_bucket_and_a_bucket_with_a_key_are_two_stores(scan) -> None:
    """The containment branch, through the resolver (B.3b-fs-followup).

    A write naming `'docs/uploads/'` and a read naming
    `'docs/uploads/report.txt'` are two literals and two stores, and each
    site attaches to **both**: `literal_match` relates them on
    containment, and the resolver's attachment is the site's own literal
    plus every key that row relates to it. So each store earns one write
    and one read — the equal literal on one side and the prefix row on
    the other, `1 − 0.7 × 0.4 × 0.8 = 0.776` and `1 − 0.7 × 0.7 × 0.8 =
    0.608`. The branch itself is pinned in
    `tests/test_store_core/test_confidence.py`.
    """
    built = _scene(
        scan,
        source=(
            "def handle(payload):\n"
            f'    s3.put_object(Bucket="{BUCKET}", Key="uploads/")\n'
            f'    return s3.get_object(Bucket="{BUCKET}", Key="{KEY}")\n'
        ),
        nodes=[_writer_node(), _source_node(line=3)],
    )
    edges = _resolve(built)

    assert literal_match("docs/uploads/", LITERAL) == LITERAL_PREFIX
    stores = {store.name: store.id for store in built.nodes("store")}
    assert sorted(stores) == ["s3:docs/uploads/", f"s3:{LITERAL}"]
    assert [edge.target_id for edge in edges if edge.kind == "writes_to"] == [
        stores["s3:docs/uploads/"], stores[f"s3:{LITERAL}"],
    ]
    assert [edge.source_id for edge in edges if edge.kind == "reads_from"] == [
        stores["s3:docs/uploads/"], stores[f"s3:{LITERAL}"],
    ]
    equal = combine_signals(BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE])
    prefix = combine_signals(BASE, [LITERAL_PREFIX, CODE_CONTEXT_SAME_MODULE])
    assert [round(edge.identity_confidence, 4) for edge in edges] == [
        round(equal, 4), round(prefix, 4),
        round(prefix, 4), round(equal, 4),
    ]


def test_an_unresolved_bucket_attaches_to_the_store_it_cannot_name(
    scan,
) -> None:
    """§5's wildcard: an unnamed site points at every store of its family.

    The write's bucket is a parameter, so its hint is `None` and §5
    keeps the store the *read* named, letting the write point at it.
    The write's literal signal is `LITERAL_ONE_SIDE` — it names no
    store of its own — while the read names the store exactly, so the
    two links differ: `1 − 0.7 × 0.8 × 0.8 = 0.552` for the write and
    the equal pair's `0.776` for the read.
    """
    from_unresolved = combine_signals(
        BASE, [LITERAL_ONE_SIDE, CODE_CONTEXT_SAME_MODULE]
    )
    from_equal = combine_signals(
        BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
    )
    built = _scene(
        scan,
        source=(
            "def handle(payload):\n"
            "    s3.put_object(Bucket=payload['bucket'], "
            "Key=payload['key'])\n"
            f'    return s3.get_object(Bucket="{BUCKET}", Key="{KEY}")\n'
        ),
    )
    edges = _resolve(built)

    assert [store.name for store in built.nodes("store")] == [f"s3:{LITERAL}"]
    assert [edge.kind for edge in edges] == ["writes_to", "reads_from"]
    assert [round(edge.identity_confidence, 4) for edge in edges] == [
        round(from_unresolved, 4), round(from_equal, 4),
    ]


# ============================================================
# Determinism
# ============================================================

def test_two_runs_over_one_scene_give_the_same_edges(scan) -> None:
    """The edges are a function of the code, not of the traversal.

    The store's id is content-derived (`store_node_id`) and the edges'
    are too, so the same scene resolved twice yields the same ids in the
    same order — which is what the pipeline depends on when it writes
    the delta.
    """
    first = _scene(scan)
    first_edges = _resolve(first)
    second = _scene(scan)
    again = _resolve(second)

    assert [edge.id for edge in again] == [edge.id for edge in first_edges]
    assert [store.name for store in second.nodes("store")] == [
        store.name for store in first.nodes("store")
    ]
    assert [store.name for store in second.nodes("store")] == [
        f"s3:{LITERAL}"
    ]


def _link(edge_id: str, source_id: str, target_id: str) -> Edge:
    """A control edge — the kind the chains around a store are made of."""
    return Edge(
        id=edge_id, source_id=source_id, target_id=target_id,
        kind="has_access_to", host_id="localhost", flow_kind="control",
    )
