"""The upload writer — handler → store (B.3b-user_upload-core, W1–W4).

`writer.find_upload_stores` is the one place the writer rule lives: BFS
from an entry point's handler over the call graph, then the `fs` write
sites whose enclosing function is reachable, then the store each write
names, then the signals (§8, revision 2):

    base 0.5 (context) + equal literal 0.6 + same module 0.2
    + in the handler's own body 0.2

    in the body, own file   1 − 0.5 × 0.4 × 0.8 × 0.8 = 0.872
    via a helper, own file  1 − 0.5 × 0.4 × 0.8       = 0.84
    via a helper, other file 1 − 0.5 × 0.4            = 0.8

(The brief states 0.936 and 0.872 for the first two; those are the values
of a *four*-signal and a *three*-signal combination with one extra 0.2
signal, which this rule does not have. The rule the brief describes —
base 0.5, literal, code context, context-in-body — gives 0.872 and 0.84,
and `test_the_numbers_are_the_stated_formula` pins both from the
constants.)

The files are real (written to `tmp_path`): the writer reads which
function a write call lives in, and that is the AST's answer, not a
fixture's. The store nodes are hand-built the way the resolver builds
them, because the resolver is not this file's subject.
"""

from __future__ import annotations

from aicorellator.graph.models import Node
from aicorellator.store.confidence import (
    CODE_CONTEXT_SAME_MODULE,
    LITERAL_EQUAL,
    combine_signals,
)
from aicorellator.store.context.upload.base import UploadSite
from aicorellator.store.context.upload.writer import (
    CONTEXT_IN_BODY,
    UPLOAD_WRITER_BASE,
    find_upload_stores,
)
from aicorellator.store.families.fs import FsFamily

HANDLER = "app/routes.py"
HELPER = "app/storage.py"
LITERAL = "/uploads/report.txt"
TEMP = "/tmp/report.txt"

# A write directly in the handler's body: line 2, handler starts line 1.
IN_BODY = (
    "def handle(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
)

# The same write, in a helper the handler calls (same file).
VIA_HELPER = (
    "def save(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
    "\n"
    "def handle(payload):\n"
    "    save(payload)\n"
)

# The handler calls a helper that lives in another module.
CALLER = (
    "def handle(payload):\n"
    "    save(payload)\n"
)
SAVER = (
    "def save(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
)

# A nested function inside the handler: inside its line range, not its body.
NESTED = (
    "def handle(payload):\n"
    "    def save():\n"
    f'        with open("{LITERAL}", "wb") as fh:\n'
    "            fh.write(payload)\n"
    "    save()\n"
)

# A write in no function at all.
MODULE_LEVEL = (
    f'with open("{LITERAL}", "wb") as fh:\n'
    "    fh.write('x')\n"
    "\n"
    "def handle(payload):\n"
    "    return payload\n"
)


def _entry_node(
    handler: str = "handle",
    node_id: str = "entry_point:1",
    file_path: str = HANDLER,
) -> Node:
    return Node(
        id=node_id, kind="entry_point", name="POST /upload",
        host_id="localhost",
        attributes={"source_file": file_path, "handler": handler},
    )


def _store_node(
    node_id: str, hint: str, family: str = "fs"
) -> Node:
    return Node(
        id=node_id, kind="store", name=f"{family}:{hint}",
        host_id="localhost",
        attributes={"family": family, "identity_hint": hint},
    )


def _fs_writes(built, files: dict[str, str]) -> list:
    """The real `fs` family's write sites, over the scan's files."""
    writes = []
    for entry in built.ctx.manifest.files:
        module = built.ctx.module_for(entry.path)
        writes.extend(FsFamily().detect_writes(module, entry.path))
    return writes


def _run(
    project,
    files: dict[str, str],
    *,
    handler: str = "handle",
    handler_file: str | None = None,
    stores: tuple[str, ...] = (LITERAL,),
    store_family: str = "fs",
    framework: str = "fake",
    call_graph: dict | None = None,
    modules: bool = True,
    entry_node: Node | None = None,
    extra_sites: list[UploadSite] | None = None,
    threshold: float | None = None,
):
    """One writer run: `(project, edges)`.

    The delta holds the entry point the site names and one store node per
    hint — the two things the resolver would have put there — and the
    write sites come from the real `fs` family reading the real files.
    """
    built = project(files=files)
    handler_file = handler_file or next(iter(files))
    entry = entry_node or _entry_node(handler, file_path=handler_file)
    built.delta.nodes.append(entry)
    built.delta.nodes.extend(
        _store_node(f"store:{index}", hint, store_family)
        for index, hint in enumerate(stores)
    )
    sites = [
        UploadSite(
            entry_point=entry, file_path=handler_file, line=1,
            framework=framework,
        )
    ]
    sites.extend(extra_sites or [])
    modules_by_path = None
    if modules:
        modules_by_path = {
            path: built.ctx.module_for(path) for path in files
        }
    kwargs = {}
    if threshold is not None:
        kwargs["confidence_threshold"] = threshold
    edges = find_upload_stores(
        delta=built.delta,
        upload_sites=sites,
        call_graph=call_graph if call_graph is not None else {"handle": set()},
        fs_writes=_fs_writes(built, files),
        store_nodes=[n for n in built.delta.nodes if n.kind == "store"],
        modules=modules_by_path,
        **kwargs,
    )
    return built, edges


# ============================================================
# W1 — the write is the handler's own
# ============================================================

def test_the_numbers_are_the_stated_formula() -> None:
    """§8's revision, from the constants: 0.872 and 0.84.

    The brief states 0.936 and 0.872 for these two; those are one signal
    more than the rule it describes. The rule — base 0.5, equal literal,
    same module, context-in-body — gives these two, and the writer pins
    them from `confidence`'s constants rather than restating them.
    """
    in_body = combine_signals(
        UPLOAD_WRITER_BASE,
        [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE, CONTEXT_IN_BODY],
    )
    via_helper = combine_signals(
        UPLOAD_WRITER_BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
    )
    assert round(in_body, 4) == 0.872
    assert round(via_helper, 4) == 0.84


def test_a_write_in_the_handlers_body_is_the_strongest_link(project) -> None:
    """W1 — the handler's own write, at 0.872."""
    built, edges = _run(project, {HANDLER: IN_BODY})

    assert [edge.kind for edge in edges] == ["writes_to"]
    edge = edges[0]
    store = built.nodes("store")[0]
    assert (edge.source_id, edge.target_id) == ("entry_point:1", store.id)
    assert round(edge.identity_confidence, 4) == 0.872


def test_the_writer_edge_is_structural_and_makes_no_flow_claim(
    project,
) -> None:
    """ADR-0021 §3–§4: a declaration, `observed_by=["ast"]`, no data flow."""
    _, edges = _run(project, {HANDLER: IN_BODY})

    assert {edge.flow_kind for edge in edges} == {"structural"}
    assert {edge.observed_by[0] for edge in edges} == {"ast"}
    assert {edge.data_flow_confirmed for edge in edges} == {None}
    assert {edge.precision for edge in edges} == {"syntactic"}


# ============================================================
# W2 — the handler calls a helper
# ============================================================

def test_a_write_reached_through_a_helper_is_weaker(project) -> None:
    """W2 — the same literal and module, no context-in-body: 0.84."""
    _, edges = _run(
        project,
        {HANDLER: VIA_HELPER},
        call_graph={"handle": {"save"}, "save": set()},
    )

    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.84]


def test_a_helper_in_another_file_is_weaker_still(project) -> None:
    """No same-module signal either: `1 − 0.5 × 0.4 = 0.8`."""
    _, edges = _run(
        project,
        {HANDLER: CALLER, HELPER: SAVER},
        call_graph={"handle": {"save"}, "save": set()},
    )

    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.8]


# ============================================================
# W3 — a write the handler does not reach
# ============================================================

def test_a_write_the_handler_does_not_reach_is_no_edge(project) -> None:
    """W3 — a helper nobody calls is not the handler's write."""
    _, edges = _run(
        project,
        {HANDLER: VIA_HELPER},
        call_graph={"handle": set(), "save": set()},
    )

    assert edges == []


def test_a_write_in_a_nested_function_is_not_the_handlers_body(
    project,
) -> None:
    """Inside the handler's lines, but the write is the nested one's.

    The nested function is reachable (the call graph records the call
    inside the handler's body), so the link exists — at the helper's
    0.84, not the body's 0.872.
    """
    _, edges = _run(
        project,
        {HANDLER: NESTED},
        call_graph={"handle": {"save"}, "save": set()},
    )

    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.84]


def test_a_write_in_the_module_body_is_not_the_handlers(project) -> None:
    """The handler does not run the module body."""
    _, edges = _run(project, {HANDLER: MODULE_LEVEL})
    assert edges == []


def test_a_module_level_entry_point_gets_no_edge(project) -> None:
    """No handler, no BFS — the Streamlit step's question, deferred."""
    module_entry = Node(
        id="entry_point:9", kind="entry_point",
        name=f"Streamlit:{HANDLER}:file_uploader", host_id="localhost",
        attributes={
            "source_file": HANDLER, "line_start": 1,
            "method": "STREAMLIT", "path": "file_uploader",
        },
    )
    _, edges = _run(project, {HANDLER: IN_BODY}, entry_node=module_entry)

    assert edges == []


def test_a_module_sentinel_handler_gets_no_edge(project) -> None:
    """`<module>` is not a function name the call graph holds."""
    sentinel = _entry_node(handler="<module>")
    _, edges = _run(project, {HANDLER: IN_BODY}, entry_node=sentinel)
    assert edges == []


def test_an_entry_point_node_the_delta_does_not_hold_gets_no_edge(
    project,
) -> None:
    """A stale id (the merge re-points ids) cannot be an edge's source."""
    built, _ = _run(project, {HANDLER: IN_BODY})
    ghost = _entry_node(node_id="entry_point:ghost")
    edges = find_upload_stores(
        delta=built.delta,
        upload_sites=[UploadSite(
            entry_point=ghost, file_path=HANDLER, line=1, framework="fake",
        )],
        call_graph={"handle": set()},
        fs_writes=_fs_writes(built, {HANDLER: IN_BODY}),
        store_nodes=built.nodes("store"),
        modules={HANDLER: built.ctx.module_for(HANDLER)},
    )
    assert edges == []


def test_without_modules_the_writer_guesses_nothing(project) -> None:
    """The enclosing function comes from the AST; no AST, no edge."""
    _, edges = _run(project, {HANDLER: IN_BODY}, modules=False)
    assert edges == []


# ============================================================
# W4 — a write that names no store
# ============================================================

def test_a_write_with_an_unresolved_literal_gets_no_store(project) -> None:
    """W4 — §5: a parameter names nothing, and a store needs a literal.

    The `fs` family reports the site with `identity_hint=None`, so no
    store node matches it and the handler earns no edge.
    """
    source = (
        "def handle(payload, path):\n"
        '    with open(path, "wb") as fh:\n'
        "        fh.write(payload)\n"
    )
    built, edges = _run(project, {HANDLER: source})

    assert [write.identity_hint for write in _fs_writes(
        built, {HANDLER: source}
    )] == [None]
    assert edges == []


def test_an_fstring_write_names_its_own_prefix_not_the_store(
    project,
) -> None:
    """`f"/uploads/{name}"` names `/uploads/` — a different store.

    The family reports the part before the first `{`, which is a literal
    in its own right; the lookup is exact equality, so a handler writing
    into `/uploads/report.txt` is not the writer of `/uploads/`.
    """
    source = (
        "def handle(payload, name):\n"
        '    with open(f"/uploads/{name}", "wb") as fh:\n'
        "        fh.write(payload)\n"
    )
    built, edges = _run(project, {HANDLER: source})

    assert [write.identity_hint for write in _fs_writes(
        built, {HANDLER: source}
    )] == ["/uploads/"]
    assert edges == []

    _, matched = _run(project, {HANDLER: source}, stores=("/uploads/",))
    assert len(matched) == 1


def test_a_write_naming_a_store_the_delta_does_not_hold_gets_no_edge(
    project,
) -> None:
    """The store node is what the edge points at; without it, no edge."""
    _, edges = _run(project, {HANDLER: IN_BODY}, stores=())
    assert edges == []


def test_a_store_of_another_family_is_not_this_writes(project) -> None:
    """The lookup is `(family, literal)`: an `s3` store is a different one."""
    _, edges = _run(project, {HANDLER: IN_BODY}, store_family="s3")
    assert edges == []


# ============================================================
# One store, several writers; one writer, several stores
# ============================================================

def test_two_handlers_writing_one_store_are_two_edges(project) -> None:
    """Two uploads into `/uploads/`: two writers, one store.

    The store is one node (§1) and the edges are two, because the
    writers are — which is the shape a group over it joins on.
    """
    source = (
        "def upload_a(payload):\n"
        f'    with open("{LITERAL}", "wb") as fh:\n'
        "        fh.write(payload)\n"
        "\n"
        "def upload_b(payload):\n"
        f'    with open("{LITERAL}", "wb") as fh:\n'
        "        fh.write(payload)\n"
    )
    built = project(files={HANDLER: source})
    first = _entry_node("upload_a", "entry_point:1")
    second = _entry_node("upload_b", "entry_point:2")
    store = _store_node("store:0", LITERAL)
    built.delta.nodes.extend([first, second, store])

    edges = find_upload_stores(
        delta=built.delta,
        upload_sites=[
            UploadSite(
                entry_point=first, file_path=HANDLER, line=1,
                framework="fake",
            ),
            UploadSite(
                entry_point=second, file_path=HANDLER, line=5,
                framework="fake",
            ),
        ],
        call_graph={"upload_a": set(), "upload_b": set()},
        fs_writes=_fs_writes(built, {HANDLER: source}),
        store_nodes=[store],
        modules={HANDLER: built.ctx.module_for(HANDLER)},
    )

    assert [edge.source_id for edge in edges] == [
        "entry_point:1", "entry_point:2",
    ]
    assert {edge.target_id for edge in edges} == {store.id}
    assert [round(edge.identity_confidence, 4) for edge in edges] == [
        0.872, 0.872,
    ]


def test_one_handler_writing_two_stores_is_two_edges(project) -> None:
    """A temp write beside the upload is a second edge — a false positive.

    The writer cannot tell `/uploads/` from `/tmp/`: both are `fs` writes
    the handler makes, and the rule is the write's literal. Whether the
    temp store becomes anything is the reader-in-scope rule's business
    (`attribution`), not the writer's — stated, and pinned here so the
    false positive is a decision on the record.
    """
    source = (
        "def handle(payload):\n"
        f'    with open("{LITERAL}", "wb") as fh:\n'
        "        fh.write(payload)\n"
        f'    with open("{TEMP}", "wb") as fh:\n'
        "        fh.write(payload)\n"
    )
    built, edges = _run(project, {HANDLER: source}, stores=(LITERAL, TEMP))

    stores = {store.id: store.name for store in built.nodes("store")}
    assert [stores[edge.target_id] for edge in edges] == [
        f"fs:{LITERAL}", f"fs:{TEMP}",
    ]
    assert [round(edge.identity_confidence, 4) for edge in edges] == [
        0.872, 0.872,
    ]


def test_two_writes_of_one_store_by_one_handler_are_one_edge(
    project,
) -> None:
    """One `writes_to` per (entry point, store), the strongest kept."""
    source = (
        "def handle(payload):\n"
        f'    with open("{LITERAL}", "wb") as fh:\n'
        "        fh.write(payload)\n"
        f'    with open("{LITERAL}", "ab") as fh:\n'
        "        fh.write(payload)\n"
    )
    _, edges = _run(project, {HANDLER: source})

    assert len(edges) == 1
    assert round(edges[0].identity_confidence, 4) == 0.872


# ============================================================
# The floor, and what is not a store
# ============================================================

def test_the_default_threshold_keeps_every_writer_edge(project) -> None:
    """0.8 is the floor of this rule and §9's threshold is 0.7."""
    _, edges = _run(
        project,
        {HANDLER: CALLER, HELPER: SAVER},
        call_graph={"handle": {"save"}, "save": set()},
    )
    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.8]


def test_a_threshold_above_the_link_drops_it(project) -> None:
    """The floor is the writer's own, and the brief names one.

    It never fires at §9's default; a caller that raises it is asking the
    writer not to state a link it would otherwise state.
    """
    _, edges = _run(project, {HANDLER: IN_BODY}, threshold=0.9)
    assert edges == []

    _, kept = _run(project, {HANDLER: IN_BODY}, threshold=0.872)
    assert len(kept) == 1


def test_a_node_that_is_not_a_store_is_not_a_store(project) -> None:
    """`store_nodes` is filtered by kind and by both attributes."""
    built, _ = _run(project, {HANDLER: IN_BODY})
    wrong_kind = Node(
        id="tool:1", kind="tool", name=f"fs:{LITERAL}", host_id="localhost",
        attributes={"family": "fs", "identity_hint": LITERAL},
    )
    no_attributes = Node(
        id="store:bare", kind="store", name="?", host_id="localhost",
        attributes={},
    )

    edges = find_upload_stores(
        delta=built.delta,
        upload_sites=[UploadSite(
            entry_point=built.delta.nodes[0], file_path=HANDLER, line=1,
            framework="fake",
        )],
        call_graph={"handle": set()},
        fs_writes=_fs_writes(built, {HANDLER: IN_BODY}),
        store_nodes=[wrong_kind, no_attributes],
        modules={HANDLER: built.ctx.module_for(HANDLER)},
    )
    assert edges == []


def test_no_upload_site_is_no_edge(project) -> None:
    """The empty-registry path, at the writer's own level."""
    built, _ = _run(project, {HANDLER: IN_BODY})
    assert find_upload_stores(
        delta=built.delta,
        upload_sites=[],
        call_graph={"handle": set()},
        fs_writes=_fs_writes(built, {HANDLER: IN_BODY}),
        store_nodes=built.nodes("store"),
        modules={HANDLER: built.ctx.module_for(HANDLER)},
    ) == []

