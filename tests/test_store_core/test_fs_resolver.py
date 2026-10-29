"""The `fs` family through the resolver (B.3b-fs, R1–R4).

The family reports call sites; `StoreResolver` is what turns two of them
into a `store` node and the two edges that name it. This file is that
join, over files written to `tmp_path`, with the node side hand-built the
way the pipeline would have it: a component node for the symbol the write
call lives in, and an `untrusted_source` node for the read site.

What is pinned:

  - **R1** — one write and one read of the same literal in the same
    module make one store and two edges, at the confidence §8's signals
    give them (`1 − 0.7 × 0.4 × 0.8 = 0.776`: equal literals and the
    same module), and the query layer joins them into one group;
  - **R2** — a write with no read is a store with one edge, and a read
    with no write likewise: a store is not a group;
  - **R3** — a read whose call site the graph did not register as a
    source produces no `reads_from`, because a read points *from* a
    store and there is nothing here to point at;
  - **R4** — two different literals are two stores, and neither side is
    claimed for the other's.

The registry is not involved: the resolver is handed the family, the way
`tests/test_store_core/test_resolver.py` hands it a fake. That the real
registry holds `fs` is pinned in `tests/test_store_families/test_fs.py`,
and the pipeline's own run is in `tests/test_normalizer_stages/
test_store_resolver.py`.
"""

from __future__ import annotations

from aicorellator.graph.models import Edge, Node
from aicorellator.parsing import write_graph
from aicorellator.store.families.fs import FsFamily
from aicorellator.store.resolver import StoreResolver

HANDLER = "app/handler.py"
LITERAL = "/uploads/report.txt"

# `handle` writes on line 2 and reads on line 4, both inside the one
# definition that starts on line 1.
HANDLER_SOURCE = f'''\
def handle(payload):
    with open("{LITERAL}", "wb") as fh:
        fh.write(payload)
    with open("{LITERAL}", "rb") as fh:
        return fh.read()
'''

WRITE_LINE = 2
READ_LINE = 4
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
        name="user_upload:open", host_id="localhost",
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
    edges = StoreResolver([FsFamily()]).resolve(built.delta, built.ctx)
    built.delta.edges.extend(edges)
    return edges


# ============================================================
# R1 — one write, one read, one literal
# ============================================================

def test_one_store_and_two_edges(scan) -> None:
    """The family's two sites become one store (§1) and two edges."""
    built = _scene(scan)
    edges = _resolve(built)

    stores = built.nodes("store")
    assert [store.name for store in stores] == [f"fs:{LITERAL}"]
    assert stores[0].attributes == {
        "family": "fs", "identity_hint": LITERAL,
    }
    assert stores[0].confidence == 1.0

    assert [edge.kind for edge in edges] == ["writes_to", "reads_from"]
    writes_to, reads_from = edges
    assert writes_to.source_id == "tool:handle"
    assert writes_to.target_id == stores[0].id
    assert reads_from.source_id == stores[0].id
    assert reads_from.target_id == "untrusted_source:1"


def test_the_confidence_is_the_signals_the_pair_earned(scan) -> None:
    """Equal literals (0.6) and the same module (0.2) over a base of 0.3.

    `1 − 0.7 × 0.4 × 0.8 = 0.776`, above §9's default threshold — which
    is the shape the whole mechanism exists for: two call sites that
    name the same file in the same module.
    """
    built = _scene(scan)
    edges = _resolve(built)

    assert [round(edge.identity_confidence, 4) for edge in edges] == [
        0.776, 0.776,
    ]


def test_the_edges_are_structural(scan) -> None:
    """ADR-0021 §4, ADR-0015/0016: a declaration, with no data-flow claim."""
    built = _scene(scan)
    edges = _resolve(built)

    assert {edge.flow_kind for edge in edges} == {"structural"}
    assert {edge.observed_by[0] for edge in edges} == {"ast"}
    assert {edge.data_flow_confirmed for edge in edges} == {None}


def test_the_familys_edges_are_a_chain_group(scan, store) -> None:
    """R1 — the two links meet at the store, and that is a group (§6).

    The chains around the store are hand-built, because the family
    produces the two *links* and not the paths they sit on:

        entry_point --has_access_to--> tool:handle --writes_to--> store
        store --reads_from--> source --has_access_to--> reader --> sink

    The join is by the store's id, so `store_id` naming the node the
    family created is the whole assertion: this project had no store
    before the family ran.
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
    assert group.store_name == f"fs:{LITERAL}"
    assert group.untrusted_source_id == "untrusted_source:1"
    assert group.weak is False
    assert round(group.store_confidence, 4) == 0.776


def _link(edge_id: str, source_id: str, target_id: str) -> Edge:
    """A control edge — the kind the chains around a store are made of."""
    return Edge(
        id=edge_id, source_id=source_id, target_id=target_id,
        kind="has_access_to", host_id="localhost", flow_kind="control",
    )


# ============================================================
# R2 — one side only
# ============================================================

def test_a_write_with_no_read_is_a_store_with_one_edge(scan) -> None:
    """The store exists — the literal named it — and nothing reads it.

    §5 keeps the store the literal side named. The confidence is the
    literal alone, `1 − 0.7 × 0.4 = 0.72`: with no counterpart there is
    no code-context signal to add, and the literal is what named it.
    """
    built = _scene(
        scan,
        source=f'def handle(payload):\n    open("{LITERAL}", "wb")\n',
    )
    edges = _resolve(built)

    assert [store.name for store in built.nodes("store")] == [f"fs:{LITERAL}"]
    assert [edge.kind for edge in edges] == ["writes_to"]
    assert round(edges[0].identity_confidence, 4) == 0.72


def test_a_read_with_no_write_is_a_store_with_one_edge(scan) -> None:
    """And the same from the other side."""
    built = _scene(
        scan,
        source=f'def handle():\n    return open("{LITERAL}", "rb").read()\n',
        nodes=[_source_node(line=2)],
    )
    edges = _resolve(built)

    assert [store.name for store in built.nodes("store")] == [f"fs:{LITERAL}"]
    assert [edge.kind for edge in edges] == ["reads_from"]
    assert edges[0].target_id == "untrusted_source:1"


def test_a_read_the_graph_did_not_register_is_no_reads_from(scan) -> None:
    """R3 — a read points *from* a store; with no source there is no edge.

    A read's role is the graph's (`untrusted_source` nodes, 1.3a), and a
    filesystem read is not one of the source types — `user_upload` is
    B.3b-user_upload. The store is still created: the literal named it,
    and the read is a fact about it even when the link has no target.
    """
    built = _scene(scan, nodes=[_writer_node()])
    edges = _resolve(built)

    assert [store.name for store in built.nodes("store")] == [f"fs:{LITERAL}"]
    assert [edge.kind for edge in edges] == ["writes_to"]


# ============================================================
# R4 — two different literals
# ============================================================

def test_two_different_literals_are_two_stores_and_no_cross_pair(
    scan,
) -> None:
    """Two names, two stores; neither side is claimed for the other's.

    The write names `a.txt` and the read `b.txt`, in the same module: the
    code context agrees about everything and the literals do not. §8's
    rule is "no link", so the two edges that come out name the store
    their own site named.
    """
    built = _scene(
        scan,
        source=(
            'def handle(payload):\n'
            '    open("/uploads/a.txt", "wb")\n'
            '    return open("/uploads/b.txt", "rb").read()\n'
        ),
        nodes=[_writer_node(), _source_node(line=3)],
    )
    edges = _resolve(built)

    stores = {store.name: store.id for store in built.nodes("store")}
    assert sorted(stores) == ["fs:/uploads/a.txt", "fs:/uploads/b.txt"]

    writes_to = [edge for edge in edges if edge.kind == "writes_to"]
    reads_from = [edge for edge in edges if edge.kind == "reads_from"]
    assert [edge.target_id for edge in writes_to] == [
        stores["fs:/uploads/a.txt"]
    ]
    assert [edge.source_id for edge in reads_from] == [
        stores["fs:/uploads/b.txt"]
    ]
