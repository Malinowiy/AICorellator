"""`StoreResolver` — the pairing, the edges, the confidence (R1–R6).

The families do not ship in B.3b-core, so the family here is the test's
instrument: a `FakeFamily` whose sites the test hands in, over files
written to `tmp_path` (the resolver parses them for the enclosing
definitions and the code-context signal). The node side is hand-built
the way the pipeline would have it: a component node for the symbol a
write call lives in, and an `untrusted_source` node for a read site.

What is pinned: the empty registry emits nothing and touches nothing
(R1); one write and one read naming the same literal make one store and
two edges (R2); an unresolved side points at the literal's store with
less confidence (R3) and both sides unresolved make no store at all
(R4); different literals are two stores and no cross pair (R5); and the
same delta twice is the same edges (R6).
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field

import pytest

from aicorellator.graph.models import Edge, Node
from aicorellator.parsing.normalizer_helpers import edge_id
from aicorellator.store import registry
from aicorellator.store.families.base import Family, ReadSite, WriteSite
from aicorellator.store.resolver import StoreResolver

WRITER_FILE = "app/writer.py"
READER_FILE = "app/reader.py"

WRITER_SOURCE = '''\
from app import storage


def upload(data):
    storage.write("/uploads", data)
'''

READER_SOURCE = '''\
from app import storage


def serve():
    return storage.read("/uploads")
'''

# The call sites' lines in the fixtures above: `upload` writes on 5,
# `serve` reads on 5. The enclosing definition starts on line 4.
WRITE_LINE = 5
READ_LINE = 5
DEF_LINE = 4


def _call() -> ast.Call:
    node = ast.parse("storage.write('/uploads')").body[0]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    return node.value


@dataclass
class FakeFamily(Family):
    """A family whose sites the test decides."""

    name: str = "fake"
    writes: list[WriteSite] = field(default_factory=list)
    reads: list[ReadSite] = field(default_factory=list)
    contexts: list = field(default_factory=list)

    def detect_writes(self, tree, file_path, *, context=None):
        self.contexts.append(context)
        return [s for s in self.writes if s.file_path == file_path]

    def detect_reads(self, tree, file_path, *, context=None):
        self.contexts.append(context)
        return [s for s in self.reads if s.file_path == file_path]


def write_site(
    file_path: str = WRITER_FILE,
    line: int = WRITE_LINE,
    hint: str | None = "docs",
    **kwargs,
) -> WriteSite:
    return WriteSite(
        call_site=_call(), file_path=file_path, line=line,
        identity_hint=hint, **kwargs,
    )


def read_site(
    file_path: str = READER_FILE,
    line: int = READ_LINE,
    hint: str | None = "docs",
    source_type: str | None = "user_upload",
    **kwargs,
) -> ReadSite:
    return ReadSite(
        call_site=_call(), file_path=file_path, line=line,
        identity_hint=hint, source_type=source_type, **kwargs,
    )


def writer_node(
    node_id: str = "tool:upload",
    name: str = "upload",
    file_path: str = WRITER_FILE,
    line: int = DEF_LINE,
) -> Node:
    return Node(
        id=node_id, kind="tool", name=name, host_id="localhost",
        attributes={"source_file": file_path, "line_start": line},
    )


def source_node(
    node_id: str = "untrusted_source:1",
    file_path: str = READER_FILE,
    source_type: str = "user_upload",
    line: int = READ_LINE,
) -> Node:
    return Node(
        id=node_id, kind="untrusted_source",
        name=f"{source_type}:storage.read",
        host_id="localhost",
        attributes={
            "source_file": file_path,
            "source_type": source_type,
            "line_start": line,
        },
    )


@pytest.fixture
def scene(scan):
    """A scan over the two fixture files, with the pipeline's nodes."""

    def build(writes=(), reads=(), nodes=None, files=None) -> object:
        default_files = {
            WRITER_FILE: WRITER_SOURCE,
            READER_FILE: READER_SOURCE,
        }
        built = scan(files=files or default_files)
        built.delta.nodes.extend(
            nodes if nodes is not None else [writer_node(), source_node()]
        )
        family = FakeFamily(writes=list(writes), reads=list(reads))
        return built, family

    return build


def _shape(edges) -> list:
    """The edge tuple the determinism test compares."""
    return [
        (e.id, e.kind, e.source_id, e.target_id, e.identity_confidence)
        for e in edges
    ]


# ============================================================
# R1 — the empty registry
# ============================================================

def test_no_families_no_edges(scene) -> None:
    """B.3b-core's whole behaviour: the registry is empty."""
    built, _ = scene()
    before = (list(built.delta.nodes), list(built.delta.edges))

    assert StoreResolver().resolve(built.delta, built.ctx) == []
    assert built.delta.nodes == before[0]
    assert built.delta.edges == before[1]


def test_an_empty_family_list_ignores_the_registry(scene) -> None:
    """`families=[]` is the explicit "none", whatever is registered."""
    built, _ = scene(writes=[write_site()], reads=[read_site()])
    registry.register_family("fake", FakeFamily)
    assert StoreResolver(families=[]).resolve(built.delta, built.ctx) == []
    assert built.delta.nodes == [writer_node(), source_node()]


# ============================================================
# R2 — one write, one read, one literal
# ============================================================

def test_one_store_and_two_edges(scene) -> None:
    built, family = scene(writes=[write_site()], reads=[read_site()])
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)

    stores = built.nodes("store")
    assert len(stores) == 1
    store = stores[0]
    assert store.name == "fake:docs"
    assert store.kind == "store"
    assert store.attributes["family"] == "fake"
    assert store.attributes["identity_hint"] == "docs"
    # §1: an entity is in the graph or not — no probability on the node.
    assert store.confidence == 1.0

    assert [e.kind for e in edges] == ["writes_to", "reads_from"]

    writes_to, reads_from = edges
    assert writes_to.source_id == "tool:upload"
    assert writes_to.target_id == store.id
    assert reads_from.source_id == store.id
    assert reads_from.target_id == "untrusted_source:1"

    # §4: a declaration, not a path — structural, AST-observed, with no
    # data-flow verdict (ADR-0015 decision 5, ADR-0016 decision 3).
    assert {e.flow_kind for e in edges} == {"structural"}
    assert {e.observed_by[0] for e in edges} == {"ast"}
    assert {e.data_flow_confirmed for e in edges} == {None}
    assert {e.precision for e in edges} == {"syntactic"}
    assert {e.id for e in edges} == {
        edge_id("tool:upload", store.id, "writes_to"),
        edge_id(store.id, "untrusted_source:1", "reads_from"),
    }


def test_equal_literals_score_the_literal_signal_alone(scene) -> None:
    """Different files, no shared local import: `1 − 0.7 × 0.4 = 0.72`."""
    built, family = scene(writes=[write_site()], reads=[read_site()])
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)
    assert [round(e.identity_confidence, 4) for e in edges] == [0.72, 0.72]


def test_the_same_module_adds_its_signal(scene) -> None:
    """`1 − 0.7 × 0.4 × 0.8 = 0.776` — one file, both call sites."""
    files = {WRITER_FILE: '''\
def upload(data):
    storage.write("/uploads", data)
    storage.read("/uploads")
'''}
    built, family = scene(
        writes=[write_site(line=2)],
        reads=[read_site(file_path=WRITER_FILE, line=3)],
        files=files,
        nodes=[
            writer_node(line=1),
            source_node(file_path=WRITER_FILE, line=3),
        ],
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)
    assert [round(e.identity_confidence, 4) for e in edges] == [0.776, 0.776]


# ============================================================
# R3 — one side unresolved
# ============================================================

def test_an_unresolved_reader_points_at_the_named_store(scene) -> None:
    """§5: the store comes from the literal side, at a lower confidence.

    The write side keeps its literal signal (0.72); the read side is
    worth `1 − 0.7 × 0.8 = 0.44` — §9's weak band, and the number the
    ADR's own example lands on.
    """
    built, family = scene(
        writes=[write_site(hint="docs")], reads=[read_site(hint=None)],
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)

    assert [s.name for s in built.nodes("store")] == ["fake:docs"]
    writes_to, reads_from = edges
    assert round(writes_to.identity_confidence, 4) == 0.72
    assert round(reads_from.identity_confidence, 4) == 0.44


def test_an_unresolved_writer_points_at_every_named_store(scene) -> None:
    """The unnamed side may point at any of them; each is stated.

    Two writes name two stores and one read names none: the read
    attaches to both, because the AST cannot say which one it reads,
    and each link carries the one-side-only confidence.
    """
    built, family = scene(
        writes=[write_site(hint="a"), write_site(hint="b")],
        reads=[read_site(hint=None)],
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)

    stores = {s.name: s.id for s in built.nodes("store")}
    assert sorted(stores) == ["fake:a", "fake:b"]

    reads_from = [e for e in edges if e.kind == "reads_from"]
    assert {e.source_id for e in reads_from} == set(stores.values())
    assert {e.target_id for e in reads_from} == {"untrusted_source:1"}
    assert [round(e.identity_confidence, 4) for e in reads_from] == [
        0.44, 0.44,
    ]
    assert [e.kind for e in edges] == [
        "writes_to", "writes_to", "reads_from", "reads_from",
    ]


# ============================================================
# R4 — no literal on either side
# ============================================================

def test_no_literal_no_store_and_no_edges(scene) -> None:
    """§5: two unnamed call sites are not evidence of a shared store."""
    built, family = scene(
        writes=[write_site(hint=None)], reads=[read_site(hint=None)],
    )
    before = list(built.delta.nodes)
    assert StoreResolver([family]).resolve(built.delta, built.ctx) == []
    assert built.delta.nodes == before
    assert built.nodes("store") == []


# ============================================================
# R5 — different literals
# ============================================================

def test_different_literals_are_two_stores_and_no_cross_pair(scene) -> None:
    """Two names, two stores; neither side is claimed for the other's."""
    built, family = scene(
        writes=[write_site(hint="a")], reads=[read_site(hint="b")],
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)

    stores = {s.name: s.id for s in built.nodes("store")}
    assert sorted(stores) == ["fake:a", "fake:b"]
    assert [
        (e.kind, e.target_id if e.kind == "writes_to" else e.source_id)
        for e in edges
    ] == [
        ("writes_to", stores["fake:a"]),
        ("reads_from", stores["fake:b"]),
    ]


# ============================================================
# R6 — determinism
# ============================================================

def test_the_same_delta_twice_is_the_same_edges(scene) -> None:
    """Families in registry order, sites in the family's order, ids from
    content: the result is a property of the code, not of the walk."""
    writes = [write_site(hint="a"), write_site(hint="b")]
    reads = [read_site(hint="a"), read_site(hint=None)]

    first, family_a = scene(writes=writes, reads=reads)
    second, family_b = scene(writes=writes, reads=reads)

    edges_a = StoreResolver([family_a]).resolve(first.delta, first.ctx)
    edges_b = StoreResolver([family_b]).resolve(second.delta, second.ctx)

    assert _shape(edges_a) == _shape(edges_b)
    assert [n.id for n in first.nodes("store")] == [
        n.id for n in second.nodes("store")
    ]
    # Two writes name two stores; the read naming "a" attaches to a, and
    # the read naming nothing attaches to both. The two read sites are
    # one line, so their link to store "a" is one edge — the store link
    # is (store, source), not (site, store).
    assert [e.kind for e in edges_a] == [
        "writes_to", "writes_to", "reads_from", "reads_from",
    ]
    assert sorted(
        round(e.identity_confidence, 4)
        for e in edges_a
        if e.kind == "reads_from"
    ) == [0.44, 0.72]


# ============================================================
# The ends the delta does not have
# ============================================================

def test_no_node_for_the_writer_means_no_write_edge(scene) -> None:
    """A component the graph does not know cannot start a write chain.

    The store node is still created — the literal named it (§1) — and
    the read side is still linked, so a store with no incoming edge is
    visible rather than silent.
    """
    built, family = scene(
        writes=[write_site()], reads=[read_site()], nodes=[source_node()],
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)

    assert [e.kind for e in edges] == ["reads_from"]
    assert [s.name for s in built.nodes("store")] == ["fake:docs"]


def test_a_read_is_attributed_to_the_source_of_its_type(scene) -> None:
    """When the line does not match, a unique source of that type will do.

    A family may report a read site one line off the call the source
    stage saw — a wrapper, a helper. One source of the site's type in
    the file is that read; two are not, and the ambiguity is left
    unresolved (§5).
    """
    one = source_node(node_id="untrusted_source:one", line=1)
    built, family = scene(reads=[read_site(line=99)], nodes=[one])
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)
    assert [e.target_id for e in edges] == ["untrusted_source:one"]

    two_a = source_node(node_id="untrusted_source:a", line=1)
    two_b = source_node(node_id="untrusted_source:b", line=2)
    built, family = scene(reads=[read_site(line=99)], nodes=[two_a, two_b])
    assert StoreResolver([family]).resolve(built.delta, built.ctx) == []


def test_a_read_with_no_source_type_gets_no_second_chance(scene) -> None:
    """A read that claims no source is a read of nothing the graph holds.

    §1 still makes the store the literal named an entity, so the node is
    in the delta — with nothing pointing at it and nothing leaving it.
    """
    built, family = scene(
        reads=[read_site(line=99, source_type=None)], nodes=[source_node()],
    )
    assert StoreResolver([family]).resolve(built.delta, built.ctx) == []
    assert [s.name for s in built.nodes("store")] == ["fake:docs"]
    assert built.edges("reads_from") == []


def test_the_resolver_fills_the_module_facts(scene) -> None:
    """§8's code-context signal needs the imports; the family is not asked.

    The fixture files import `app.storage` from under `app`, so the
    facts of both sites name `app/storage.py` — and the two sites then
    share a local import, which is the wrapper signal.
    """
    files = {
        WRITER_FILE: WRITER_SOURCE,
        READER_FILE: READER_SOURCE,
        "app/storage.py": "def write(path, data):\n    pass\n",
        "app/__init__.py": "",
    }
    built, family = scene(
        writes=[write_site()], reads=[read_site()], files=files,
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)

    assert family.writes[0].facts is not None
    assert family.reads[0].facts is not None
    # `from app import storage` names both the package and the module,
    # and the manifest holds both.
    assert family.writes[0].facts.local_imports == frozenset({
        "app/__init__.py", "app/storage.py",
    })
    # Literal + shared wrapper: `1 − 0.7 × 0.4 × 0.7 = 0.804`.
    assert [round(e.identity_confidence, 4) for e in edges] == [0.804, 0.804]


def test_the_context_reaches_every_detector(scene) -> None:
    """The interface promises a context; the resolver threads it (B.3b)."""
    built, family = scene(writes=[write_site()], reads=[read_site()])
    marker = object()
    StoreResolver([family], context=marker).resolve(built.delta, built.ctx)
    assert family.contexts == [marker, marker, marker, marker]


def test_a_raising_family_is_logged_and_costs_only_itself(
    scene, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR-0011's rule: detection failures are not scan failures."""

    class Broken(FakeFamily):
        name = "broken"

        def detect_writes(self, tree, file_path, *, context=None):
            raise RuntimeError("boom")

    built, _ = scene(writes=[write_site()], reads=[read_site()])
    broken = Broken(reads=[read_site()])
    with caplog.at_level(logging.ERROR):
        edges = StoreResolver([broken]).resolve(built.delta, built.ctx)

    assert "failed on" in caplog.text
    assert [e.kind for e in edges] == ["reads_from"]


# ============================================================
# R7 — prefix containment (B.3b-fs-followup)
# ============================================================
#
# `_attached_keys` attaches a site to its own literal **and** to every
# literal that contains it or is contained by it, which is what makes
# `literal_match`'s prefix row (+0.3) reachable through the resolver. The
# motivating shape is the `fs` family's: a writer names a directory and a
# reader names a file inside it. Two keys, two stores, two pairs — the
# store the directory names and the store the file names are both real.

DIRECTORY = "/uploads/"
FILE = "/uploads/x"


def test_a_directory_and_the_file_inside_it_are_two_stores(scene) -> None:
    """A1 — both literals are attached to, so both become stores.

    The write names the directory and the read names the file, and each
    site attaches to both keys: the directory's store earns the write's
    equal literal and the read's prefix row (`1 − 0.7 × 0.4 = 0.72` and
    `1 − 0.7 × 0.7 = 0.51`), and the file's store is the mirror image.
    Neither pair is merged into the other, and neither site loses its own
    store: the write puts data in the directory and the read takes it out
    of the file, and both are what the literals say.
    """
    built, family = scene(
        writes=[write_site(hint=DIRECTORY)], reads=[read_site(hint=FILE)],
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)

    stores = {s.name: s.id for s in built.nodes("store")}
    assert sorted(stores) == ["fake:/uploads/", "fake:/uploads/x"]

    assert [e.kind for e in edges] == [
        "writes_to", "writes_to", "reads_from", "reads_from",
    ]
    # `_store_keys` sorts, so the directory's key comes first in both
    # directions and the two edges of a pair sit in the same order.
    assert [e.target_id for e in edges if e.kind == "writes_to"] == [
        stores["fake:" + DIRECTORY], stores["fake:" + FILE],
    ]
    assert [e.source_id for e in edges if e.kind == "reads_from"] == [
        stores["fake:" + DIRECTORY], stores["fake:" + FILE],
    ]
    assert [round(e.identity_confidence, 4) for e in edges] == [
        0.72, 0.51, 0.51, 0.72,
    ]


def test_the_file_naming_the_directory_is_the_same_two_stores(scene) -> None:
    """A1, the other way round — containment is symmetric.

    The writer names the file and the reader the directory. The
    attachment is the same two keys (the containment rule is `startswith`
    in both directions), and the two pairs swap which side carries the
    prefix row.
    """
    built, family = scene(
        writes=[write_site(hint=FILE)], reads=[read_site(hint=DIRECTORY)],
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)

    assert sorted(s.name for s in built.nodes("store")) == [
        "fake:/uploads/", "fake:/uploads/x",
    ]
    assert [round(e.identity_confidence, 4) for e in edges] == [
        0.51, 0.72, 0.72, 0.51,
    ]


def test_equal_hints_are_one_store_and_one_pair(scene) -> None:
    """A2 — the exact case is untouched: one key, attached once.

    The same literal on both sides is `LITERAL_EQUAL`, and the two
    branches cannot both claim a key: the exact branch is tested first,
    and `literal_match` answers `LITERAL_EQUAL` before it tests
    containment, so a hint equal to a key never reaches the `elif`. One
    store, two edges, and R2's number.
    """
    built, family = scene(
        writes=[write_site(hint=DIRECTORY)],
        reads=[read_site(hint=DIRECTORY)],
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)

    assert [s.name for s in built.nodes("store")] == ["fake:" + DIRECTORY]
    assert [e.kind for e in edges] == ["writes_to", "reads_from"]
    assert [round(e.identity_confidence, 4) for e in edges] == [0.72, 0.72]


def test_hints_that_contain_neither_way_are_two_stores_and_no_pair(
    scene,
) -> None:
    """A3 — `'/uploads/a'` and `'/tmp/b'` contain neither way.

    Each site attaches to its own literal only, so each store earns one
    edge and the pairing §8 calls "no link" is not made.
    """
    built, family = scene(
        writes=[write_site(hint="/uploads/a")],
        reads=[read_site(hint="/tmp/b")],
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)

    stores = {s.name: s.id for s in built.nodes("store")}
    assert sorted(stores) == ["fake:/tmp/b", "fake:/uploads/a"]
    assert [
        (e.kind, e.target_id if e.kind == "writes_to" else e.source_id)
        for e in edges
    ] == [
        ("writes_to", stores["fake:/uploads/a"]),
        ("reads_from", stores["fake:/tmp/b"]),
    ]
    assert [round(e.identity_confidence, 4) for e in edges] == [0.72, 0.72]


def test_an_unnamed_writer_names_no_store_of_its_own(scene) -> None:
    """A4 — §5 stays: a `None` hint contributes no key.

    The read's literal is the only store, and the unnamed write side is
    the wildcard (§5) — it points at that store with the one-side signal,
    `1 − 0.7 × 0.8 = 0.44`, and with no store of its own. The one-side
    row attaches nothing: it is the answer for the `None` hint, and an
    unnamed site names nothing.
    """
    built, family = scene(
        writes=[write_site(hint=None)], reads=[read_site(hint=FILE)],
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)

    assert [s.name for s in built.nodes("store")] == ["fake:" + FILE]
    assert [e.kind for e in edges] == ["writes_to", "reads_from"]
    assert [round(e.identity_confidence, 4) for e in edges] == [0.44, 0.72]


def test_the_containment_shape_twice_is_the_same_edges(scene) -> None:
    """A5 — the attachment walks the sorted keys, not a set.

    `_attached_keys` returns the exact key and the containment keys in
    `_store_keys`'s order, so the edges — and their order — are a
    property of the literals and not of the walk.
    """
    first, family_a = scene(
        writes=[write_site(hint=FILE)], reads=[read_site(hint=DIRECTORY)],
    )
    second, family_b = scene(
        writes=[write_site(hint=FILE)], reads=[read_site(hint=DIRECTORY)],
    )

    edges_a = StoreResolver([family_a]).resolve(first.delta, first.ctx)
    edges_b = StoreResolver([family_b]).resolve(second.delta, second.ctx)

    assert _shape(edges_a) == _shape(edges_b)
    assert [n.id for n in first.nodes("store")] == [
        n.id for n in second.nodes("store")
    ]
    assert [e.kind for e in edges_a] == [
        "writes_to", "writes_to", "reads_from", "reads_from",
    ]


def test_the_two_stores_are_two_groups(scene, store) -> None:
    """A1's shape, as the join sees it (ADR-0021 §6).

    The join is by store id, so two stores is two groups — and each store
    has one side carrying the equal literal and the other the prefix row,
    so the pair's confidence, `min` of the two links (§7), is the prefix
    pair's 0.51 in both cases. Both groups are therefore **weak**: below
    §9's default threshold, so the default enumeration reports neither and
    `include_weak` is what reports them. Both are above
    `WEAK_JOIN_FLOOR`, so neither is dropped as "no link at all" — this
    is the band a containment pair lives in, and it is why the two-group
    shape is a statement about the graph and not about the default report.
    """
    built, family = scene(
        writes=[write_site(hint=DIRECTORY)], reads=[read_site(hint=FILE)],
    )
    edges = StoreResolver([family]).resolve(built.delta, built.ctx)
    _persist(built, edges, store)

    groups = store.query.find_chain_groups(include_weak=True)
    assert len(groups) == 2
    assert {group.store_name for group in groups} == {
        "fake:" + DIRECTORY, "fake:" + FILE,
    }
    assert [round(group.store_confidence, 4) for group in groups] == [
        0.51, 0.51,
    ]
    assert [group.weak for group in groups] == [True, True]
    assert store.query.find_chain_groups() == []


def _persist(built, edges, store) -> None:
    """Put a resolver result in a graph whose chains the join can walk.

    The resolver emits the store links; the chains around them are what
    `find_chain_groups` needs before it can see a group — an entry point
    that reaches the writer, and a reader that reaches a sink — so they
    are hand-built here the way `tests/test_query_layer.py` builds them,
    over the nodes the resolver has just written.
    """
    for node in built.delta.nodes:
        store.nodes.upsert(node)
    for edge in edges:
        store.edges.upsert(edge)

    store.nodes.upsert(Node(
        id="entry_point:1", kind="entry_point", name="POST /upload",
        host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="tool:reader", kind="tool", name="read_it", host_id="localhost",
    ))
    store.nodes.upsert(Node(
        id="sink:1", kind="sink", name="run_shell", host_id="localhost",
    ))
    for source_id, target_id in [
        ("entry_point:1", "tool:upload"),
        ("untrusted_source:1", "tool:reader"),
        ("tool:reader", "sink:1"),
    ]:
        store.edges.upsert(Edge(
            id=edge_id(source_id, target_id, "has_access_to"),
            source_id=source_id, target_id=target_id,
            kind="has_access_to", host_id="localhost",
        ))
