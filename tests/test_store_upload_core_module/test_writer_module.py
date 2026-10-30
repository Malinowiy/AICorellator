"""The module-level writer — module level → the module's writes (W1–W5).

B.3b-user_upload-core-module gives `writer.find_upload_stores` its second
branch. A module-level entry point (`entry_kind` `"streamlit"` or
`"cli"`, what `entry_points.EntryPointStage` records for a Streamlit and
for a CLI entry, and no `handler` for either) has no handler to BFS from,
and "which writes does this upload reach" is answered from the module
itself: the names the module **body** calls, everything the call graph
reaches from them, and the writes directly in the module body.

B.3b-user_upload-core-module-followup-2 narrows that: the start set is
the module body's own calls, not the module's list of top-level
functions. A function the body never calls does not run when the module
is imported, so it is not the upload's scope — a FastAPI route handler
defined beside a Streamlit upload is the route's store and not the
upload's (`test_the_uncalled_function_is_not_in_the_reach` and the
siblings below). A function the body **does** call is still a start, and
everything under it comes along, so the delegated shape (`run()` →
`load_and_save()` → write) is unchanged.

The marker is the node's `entry_kind` and not the `<module>` sentinel
(B.3b-user_upload-core-module-followup): the sentinel is on the
`EntryPoint` record, the node has no `handler` at all, and the branch was
inert on a real scan until it read the attribute the node actually
carries.

The numbers come from `confidence`'s constants and not from the brief's
prose, the way `test_store_upload_core/test_writer.py` pins §8's. A write
in the module body is `combine_signals(UPLOAD_WRITER_BASE_MODULE, [equal
literal, same module])`; a write in a function the module body **calls**
is the same combination with `UPLOAD_WRITER_BASE`, because the
context-in-body signal needs a handler's body and a module-level site has
none. (The brief's prose adds the signals — 0.4 + 0.6 + 0.2 — and states
0.896; the rule is `combine_signals`, and `test_number_pinning.py`
computes both numbers from the constants.)

The files are real (written to `tmp_path`): the writer reads which
function a write lives in, and that is the AST's answer, not a fixture's.
"""

from __future__ import annotations

from aicorellator.graph.models import Node
from aicorellator.parsing.entry_points import EntryPoint, EntryPointOutput
from aicorellator.parsing.normalizer_stages import EntryPointStage
from aicorellator.store.confidence import (
    CODE_CONTEXT_SAME_MODULE,
    LITERAL_EQUAL,
    combine_signals,
)
from aicorellator.store.context.upload.base import UploadSite
from aicorellator.store.context.upload.writer import (
    MODULE_LEVEL_ENTRY_KINDS,
    UPLOAD_WRITER_BASE,
    UPLOAD_WRITER_BASE_MODULE,
    find_upload_stores,
)

from .conftest import (
    HELPER,
    LITERAL,
    MODULE,
    TEMP,
    UNRELATED,
    fs_writes,
    module_entry,
    module_site,
    run_module,
    store_node,
)

# W1 — the module body itself writes the upload.
MODULE_BODY = (
    f'with open("{LITERAL}", "wb") as fh:\n'
    "    fh.write('x')\n"
)

# W2 — a top-level function writes it, and the module body calls it.
TOP_LEVEL = (
    "def save(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
    "\n"
    'save(b"x")\n'
)

# W3 — a nested function inside a top-level function writes it, and the
# module body calls the enclosing function.
NESTED = (
    "def handle(payload):\n"
    "    def save():\n"
    f'        with open("{LITERAL}", "wb") as fh:\n'
    "            fh.write(payload)\n"
    "    save()\n"
    "\n"
    'handle(b"x")\n'
)

# W5 — two top-level functions, two stores, and the body calls both.
TWO_FUNCTIONS = (
    "def save_report(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
    "\n"
    "def save_temp(payload):\n"
    f'    with open("{TEMP}", "wb") as fh:\n'
    "        fh.write(payload)\n"
    "\n"
    'save_report(b"x")\n'
    'save_temp(b"x")\n'
)

# The same two functions with **no** call in the module body: neither is
# a start, so neither write is in the reach
# (B.3b-user_upload-core-module-followup-2's false positive, inverted).
TWO_FUNCTIONS_UNCALLED = (
    "def save_report(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
    "\n"
    "def save_temp(payload):\n"
    f'    with open("{TEMP}", "wb") as fh:\n'
    "        fh.write(payload)\n"
)

# The module body and a called top-level function, both writing one store.
BODY_AND_FUNCTION = (
    f'with open("{LITERAL}", "wb") as fh:\n'
    "    fh.write('x')\n"
    "\n"
    "def save(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
    "\n"
    'save(b"x")\n'
)

# A module whose only write is inside a class method: no top-level
# function writes, and the module body does not.
CLASS_METHOD = (
    "class Uploader:\n"
    "    def save(self, payload):\n"
    f'        with open("{LITERAL}", "wb") as fh:\n'
    "            fh.write(payload)\n"
)

# A top-level function that writes nothing.
NO_WRITE = (
    "def handle(payload):\n"
    "    return payload\n"
)

# A CLI module: the `if __name__ == "__main__":` block is a statement in
# the module body, so the call inside it is a module-body call. `unused`
# is defined beside it and never called.
CLI_MODULE = (
    "def save_report(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
    "\n"
    "def unused(payload):\n"
    f'    with open("{TEMP}", "wb") as fh:\n'
    "        fh.write(payload)\n"
    "\n"
    'if __name__ == "__main__":\n'
    '    save_report(b"x")\n'
)

# A Streamlit app whose module body calls a helper that lives in another
# file (`SAVER`).
CALLER = (
    "def handle(payload):\n"
    "    save(payload)\n"
    "\n"
    'handle(b"x")\n'
)
SAVER = (
    "def save(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
)

# A module body whose only call is an attribute call: `run` is not an
# `ast.Name` target, so it is not a start name at all.
ATTRIBUTE_CALL = (
    "import helper\n"
    "\n"
    "helper.run()\n"
    "\n"
    "def save(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
)

# A function-level site's own shape, for the regression: the branch is
# selected by the handler, so this must be untouched.
HANDLER_SITE = (
    "def handle(payload):\n"
    f'    with open("{LITERAL}", "wb") as fh:\n'
    "        fh.write(payload)\n"
)


def _function_entry(
    handler: str = "handle", file_path: str = MODULE
) -> Node:
    """A function-level entry-point node — the shape the core step owns."""
    return Node(
        id="entry_point:2", kind="entry_point", name="POST /upload",
        host_id="localhost",
        attributes={"source_file": file_path, "handler": handler},
    )


def _streamlit_record() -> EntryPoint:
    """A Streamlit entry as `entry_points` records it — handler `<module>`.

    The record's `handler` is the sentinel; the **node** the stage builds
    from it has no `handler` and carries `entry_kind="streamlit"` instead.
    """
    return EntryPoint(
        method="STREAMLIT", path="file_uploader", handler="<module>",
        file_path=MODULE, line=1,
    )


def _cli_record() -> EntryPoint:
    """A CLI entry as `entry_points` records it — handler `<module>`."""
    return EntryPoint(
        method="CLI", path=MODULE, handler="<module>",
        file_path=MODULE, line=1,
    )


def _stage_node(project, files: dict[str, str], record: EntryPoint) -> Node:
    """The entry-point node `EntryPointStage` builds for `record`.

    Not a fixture's node: the stage is the real producer, so this is the
    shape a scan hands the writer — which is what decides whether the
    branch can fire at all.
    """
    output = EntryPointOutput(entry_points=[record], imports={})
    built = project(files=files, entry_points=output)
    EntryPointStage().apply(built.delta, built.ctx)
    return built.nodes("entry_point")[0]


def _module_base() -> float:
    """W1's number, from the constants: base 0.4, literal, same module."""
    return combine_signals(
        UPLOAD_WRITER_BASE_MODULE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
    )


def _function_base() -> float:
    """W2's number, from the constants: base 0.5, literal, same module."""
    return combine_signals(
        UPLOAD_WRITER_BASE, [LITERAL_EQUAL, CODE_CONTEXT_SAME_MODULE]
    )


# ============================================================
# W1 — the module body writes the upload
# ============================================================

def test_a_write_in_the_module_body_is_an_edge(project) -> None:
    """W1 — the module is the entry point's body, so the write is its."""
    built, edges = run_module(project, {MODULE: MODULE_BODY})

    assert [edge.kind for edge in edges] == ["writes_to"]
    edge = edges[0]
    store = built.nodes("store")[0]
    assert (edge.source_id, edge.target_id) == ("entry_point:1", store.id)


def test_the_module_body_edge_carries_the_module_base(project) -> None:
    """W1 — base 0.4, equal literal, same module, no context-in-body.

    The number is computed from `confidence`'s constants and not restated
    from the brief's prose: `1 − (1 − 0.4) × (1 − 0.6) × (1 − 0.2)`. The
    context-in-body signal does **not** fire — it is the *handler's*
    body, and a module-level site has no handler.
    """
    _, edges = run_module(project, {MODULE: MODULE_BODY})

    assert round(edges[0].identity_confidence, 4) == round(
        _module_base(), 4
    )
    assert round(edges[0].identity_confidence, 4) == 0.808


def test_the_module_level_edge_is_structural_and_makes_no_flow_claim(
    project,
) -> None:
    """ADR-0021 §3–§4: a declaration, `observed_by=["ast"]`, no data flow."""
    _, edges = run_module(project, {MODULE: MODULE_BODY})

    assert {edge.flow_kind for edge in edges} == {"structural"}
    assert {edge.observed_by[0] for edge in edges} == {"ast"}
    assert {edge.data_flow_confirmed for edge in edges} == {None}
    assert {edge.precision for edge in edges} == {"syntactic"}


# ============================================================
# W2 — a function the module body calls writes the upload
# ============================================================

def test_a_write_in_a_called_function_is_an_edge(project) -> None:
    """W2 — a function the module body calls is a handler-shaped scope."""
    built, edges = run_module(
        project, {MODULE: TOP_LEVEL}, call_graph={"save": set()},
    )

    assert [edge.source_id for edge in edges] == ["entry_point:1"]
    assert [edge.target_id for edge in edges] == [
        built.nodes("store")[0].id
    ]


def test_the_called_function_edge_carries_the_function_base(
    project,
) -> None:
    """W2 — base 0.5, and still no context-in-body.

    The write is in a function, but not in the entry point's own body:
    the entry point's body is the module, and the write is the called
    function's. So the base is the function's and the 0.2 does not fire.
    """
    _, edges = run_module(
        project, {MODULE: TOP_LEVEL}, call_graph={"save": set()},
    )

    assert round(edges[0].identity_confidence, 4) == round(
        _function_base(), 4
    )
    assert round(edges[0].identity_confidence, 4) == 0.84


def test_the_uncalled_function_is_not_in_the_reach(project) -> None:
    """M2 — a top-level function the module body does not call writes nothing.

    This is B.3b-user_upload-core-module-followup-2's whole point, and the
    false positive it removes: the start set is the module body's own
    calls and **not** the module's list of top-level functions, so a
    function that is defined beside the upload and never called from the
    module body contributes no store. A Streamlit upload and a FastAPI
    route handler in one file is the shape this fixes — the route's store
    is the route's (`tests/test_store_upload_streamlit/`).

    A call graph is passed and it holds both functions, which is not what
    makes this empty: nothing starts the BFS, because the module body
    never names either of them. It is not the "no helper" case either —
    a helper under a function the body does not call is just as
    unreachable (`test_a_write_in_a_helper_the_module_does_not_reach`).
    """
    _, edges = run_module(
        project, {MODULE: TWO_FUNCTIONS_UNCALLED}, stores=(LITERAL, TEMP),
        call_graph={"save_report": set(), "save_temp": set()},
    )

    assert edges == []


def test_a_write_in_a_helper_the_module_does_not_reach(project) -> None:
    """The start set is the whole of it, not one hop of it.

    `handle` calls `save` and `save` writes, so the call graph has the
    edge — but the module body never calls `handle`, so no BFS starts and
    the helper's store is not reached either. The rule is "what the
    module body reaches", and a function it does not call is not a step
    on that path. The same fixture with a module-body `handle(...)` call
    is an edge (`test_a_write_in_a_function_the_module_calls_is_an_edge`).
    """
    _, edges = run_module(
        project, {MODULE: NO_WRITE, HELPER: SAVER},
        call_graph={"handle": {"save"}, "save": set()},
    )

    assert edges == []


# ============================================================
# W3 — a nested function inside a called function
# ============================================================

def test_a_write_in_a_nested_function_is_at_the_function_base(
    project,
) -> None:
    """W3 — reached through the enclosing function's name.

    The nested function is not a module-body call, so it is not a BFS
    start of its own: it is reachable because `handle` calls it, which is
    what the call graph records, and the module body calls `handle`. Its
    write is inside a function and not in the module body, so the base is
    the function's 0.5 — and that it differs from the module body's 0.808
    is the point of the assertion.
    """
    _, edges = run_module(
        project, {MODULE: NESTED},
        call_graph={"handle": {"save"}, "save": set()},
    )

    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.84]
    assert round(edges[0].identity_confidence, 4) != round(_module_base(), 4)


def test_a_nested_write_is_the_enclosing_functions_write(project) -> None:
    """M4 — a nested write is claimed even when no call is recorded.

    The reachability check is `any` over the write's **whole** enclosing
    chain, not over its innermost name: a write inside `save` inside
    `handle` is inside `handle`'s body, and `handle` is what the module
    body calls — so the edge is there without a call edge from `handle`
    to `save`. The same rule the function-level branch has (a nested
    function inside the handler is reached through the handler's name,
    `test_store_upload_core/test_writer.py`), stated here so the module
    branch's version of it is a decision on the record.
    """
    _, edges = run_module(
        project, {MODULE: NESTED},
        call_graph={"handle": set(), "save": set()},
    )

    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.84]


def test_a_nested_write_in_an_uncalled_function_is_not_in_the_reach(
    project,
) -> None:
    """M5 — the enclosing function is what the nested write rides on.

    `handle` nests `save`, and `save` writes — but the module body never
    calls `handle`, so neither name is in the reach and the nested write
    is not either. This is the nested half of M2: a nested function is
    reached **through** its enclosing top-level function, so it inherits
    that function's absence from the reach as much as its presence.
    """
    uncalled = NESTED.replace('handle(b"x")\n', "")
    _, edges = run_module(
        project, {MODULE: uncalled},
        call_graph={"handle": {"save"}, "save": set()},
    )

    assert edges == []


def test_a_module_body_that_only_makes_attribute_calls_has_no_start(
    project,
) -> None:
    """`helper.run()` names nothing the call graph holds.

    `_module_body_calls` records `ast.Name` call targets only — the call
    graph's own rule (ADR-0016, `entry_points._collect_call_names`) — so a
    module body whose calls are all attribute calls starts no BFS and
    reaches nothing but its own writes. `save` is defined and writes, and
    it is not reached: a stated false negative, the same one
    `entry_to_sink` has.
    """
    _, edges = run_module(
        project, {MODULE: ATTRIBUTE_CALL}, call_graph={"save": set()},
    )

    assert edges == []


# ============================================================
# W4 — nothing in the module writes
# ============================================================

def test_no_write_in_the_module_is_no_edge(project) -> None:
    """W4 — a module that writes nothing has nothing to link."""
    _, edges = run_module(project, {MODULE: NO_WRITE})

    assert edges == []


def test_a_write_in_a_class_method_is_not_a_module_level_write(
    project,
) -> None:
    """W4 — a method is not a module-body call, so it is not a start.

    A false negative, stated: `self.`-calls are not in the call graph
    (ADR-0016) and a module-level entry point has no `self` to call a
    method through, so an app that keeps its upload handling in a class
    earns no edge. The module body is the other branch, and this write is
    not in it — it is inside a function, whatever the function is.
    """
    _, edges = run_module(project, {MODULE: CLASS_METHOD})

    assert edges == []


def test_a_write_in_another_module_is_no_edge_when_nothing_calls_it(
    project,
) -> None:
    """W4 — a helper module's write is reached through the call graph."""
    _, edges = run_module(
        project, {MODULE: NO_WRITE, HELPER: SAVER},
        call_graph={"handle": set(), "save": set()},
    )

    assert edges == []


def test_a_write_in_a_function_the_module_calls_is_an_edge(project) -> None:
    """The call graph is scan-wide: the helper's file needs no signal.

    `CALLER` is a module body that calls `handle`, which calls `save` in
    another file (`SAVER`), so the BFS from the body's own call name
    crosses the file boundary — the module-level analogue of the core
    step's "a helper in another file" case, and no same-module signal, so
    `1 − 0.5 × 0.4 = 0.8`.
    """
    _, edges = run_module(
        project, {MODULE: CALLER, HELPER: SAVER},
        call_graph={"handle": {"save"}, "save": set()},
    )

    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.8]


def test_a_module_body_write_in_another_file_is_not_claimed(project) -> None:
    """The branch claims the entry point's **own** file, and no other.

    A module body elsewhere runs at that file's import, and this writer
    has no import graph to say the module runs it. Deliberately narrower
    than `entry_to_sink`'s CLI fallback, which links a CLI entry to every
    sink in the target; a stated false negative.
    """
    _, edges = run_module(
        project, {MODULE: NO_WRITE, UNRELATED: MODULE_BODY},
    )

    assert edges == []


# ============================================================
# W5 — several writes, one module
# ============================================================

def test_two_called_functions_writing_two_stores_are_two_edges(
    project,
) -> None:
    """W5 — one edge per store, in the writes' source order.

    Two functions of one module writing two literals are two `writes_to`
    edges from one entry point: the store is the edge's target and there
    are two stores (§1), so nothing merges them. Both functions are
    module-body calls, which is what puts them in the reach
    (`test_the_uncalled_function_is_not_in_the_reach` is the same fixture
    without them).
    """
    built, edges = run_module(
        project, {MODULE: TWO_FUNCTIONS}, stores=(LITERAL, TEMP),
        call_graph={"save_report": set(), "save_temp": set()},
    )

    stores = {store.id: store.name for store in built.nodes("store")}
    assert [stores[edge.target_id] for edge in edges] == [
        f"fs:{LITERAL}", f"fs:{TEMP}",
    ]
    assert [round(edge.identity_confidence, 4) for edge in edges] == [
        0.84, 0.84,
    ]


def test_a_module_body_write_and_a_function_write_of_one_store_are_one_edge(
    project,
) -> None:
    """One `writes_to` per (entry point, store), the stronger kept.

    The module body's 0.808 and the called function's 0.84 are two
    claims about one link; the edge is one row, and the stronger claim is
    what it carries — the resolver's `_keep_edge` rule, reached from a
    second branch.
    """
    _, edges = run_module(
        project, {MODULE: BODY_AND_FUNCTION}, call_graph={"save": set()},
    )

    assert len(edges) == 1
    assert round(edges[0].identity_confidence, 4) == 0.84


def test_a_threshold_above_the_module_body_link_drops_it(project) -> None:
    """The writer's floor applies to the module branch like any other."""
    _, dropped = run_module(
        project, {MODULE: MODULE_BODY}, threshold=0.81,
    )
    assert dropped == []

    _, kept = run_module(project, {MODULE: MODULE_BODY}, threshold=0.808)
    assert len(kept) == 1


# ============================================================
# The branch's selector, and what it must not change
# ============================================================

def test_the_closed_set_is_the_named_constant(project) -> None:
    """The vocabulary is one name, and it is closed.

    `MODULE_LEVEL_ENTRY_KINDS` is what `_module_level` selects on, so the
    set is a fact about the code that has one place to be read from — and
    it is a `frozenset`, so a caller cannot add a member at runtime. A
    third module-level entry kind is a separate step with its own test,
    and this is the test that a new member would have to change.
    """
    assert MODULE_LEVEL_ENTRY_KINDS == frozenset({"cli", "streamlit"})
    assert isinstance(MODULE_LEVEL_ENTRY_KINDS, frozenset)

    for entry_kind in sorted(MODULE_LEVEL_ENTRY_KINDS):
        node = module_entry(entry_kind=entry_kind)
        assert node.attributes["entry_kind"] in MODULE_LEVEL_ENTRY_KINDS
        _, edges = run_module(project, {MODULE: MODULE_BODY}, entry=node)
        assert [round(edge.identity_confidence, 4) for edge in edges] == [
            0.808
        ]


def test_the_module_level_branch_is_the_entry_kind(project) -> None:
    """M1/M2 — the closed set, on the node the stage builds.

    `entry_kind` is what `EntryPointStage` writes for every node it
    builds, and it is the branch's whole selector: both members of the
    set are exercised, because a set with one member that works and one
    that does not is not a set. The fixture is the shape a real Streamlit
    node has — `entry_kind="streamlit"`, `method="STREAMLIT"`, a `path`,
    and **no** `handler` — and the node with neither marker is not
    module-level: it is an entry point whose kind the stage could not
    name, and it earns nothing.
    """
    entry = module_entry()
    assert entry.attributes["entry_kind"] == "streamlit"
    assert "handler" not in entry.attributes

    _, edges = run_module(project, {MODULE: MODULE_BODY})
    assert len(edges) == 1

    _, cli_edges = run_module(
        project, {MODULE: MODULE_BODY},
        entry=module_entry(entry_kind="cli", path=MODULE),
    )
    assert [round(edge.identity_confidence, 4) for edge in cli_edges] == [
        0.808
    ]

    nameless = Node(
        id="entry_point:3", kind="entry_point", name="?", host_id="localhost",
        attributes={"source_file": MODULE, "line_start": 1},
    )
    _, no_edges = run_module(
        project, {MODULE: MODULE_BODY}, entry=nameless,
    )
    assert no_edges == []


def test_a_cli_entry_gets_the_same_reach_as_a_streamlit_one(project) -> None:
    """U3 — `entry_kind` `"cli"` is the same rule, not a second one.

    The branch selects on the closed set, so the reach is the module
    body's for both members: a CLI script is a file that runs, and the
    functions it calls are the functions the `if __name__ == "__main__":`
    block names. `CLI_MODULE` holds one it calls (`save_report`) and one
    it does not (`unused`), and only the first store is claimed — the
    same narrowing, on the other member of the set.
    """
    built, edges = run_module(
        project, {MODULE: CLI_MODULE}, stores=(LITERAL, TEMP),
        entry=module_entry(entry_kind="cli", path=MODULE),
        call_graph={"save_report": set(), "unused": set()},
    )

    stores = {
        store.id: store.attributes["identity_hint"]
        for store in built.nodes("store")
    }
    assert [stores[edge.target_id] for edge in edges] == [LITERAL]
    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.84]


def test_a_real_streamlit_node_gets_the_module_body_edge(project) -> None:
    """E1 — the node `EntryPointStage` builds, the write, the edge.

    The node is not a fixture's: the stage builds it from a Streamlit
    entry record, which is the shape a real scan produces. Before
    B.3b-user_upload-core-module-followup this node earned no edge,
    because the branch was reading a `handler` the stage never writes for
    a module-level entry.
    """
    node = _stage_node(project, {MODULE: MODULE_BODY}, _streamlit_record())

    assert "handler" not in node.attributes
    assert node.attributes["entry_kind"] == "streamlit"

    _, edges = run_module(project, {MODULE: MODULE_BODY}, entry=node)
    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.808]
    assert edges[0].source_id == node.id


def test_a_real_cli_node_gets_the_module_body_edge(project) -> None:
    """E2 — the other member of the closed set, the same way."""
    node = _stage_node(project, {MODULE: MODULE_BODY}, _cli_record())

    assert node.attributes["entry_kind"] == "cli"
    assert "handler" not in node.attributes

    _, edges = run_module(project, {MODULE: MODULE_BODY}, entry=node)
    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.808]
    assert edges[0].source_id == node.id


def test_an_entry_kind_outside_the_closed_set_is_not_module_level(
    project,
) -> None:
    """M3 — the set is closed; `"mcp"` is a different kind of entry.

    An MCP entry is a framework-detected server, and its upload — if it
    has one — is not the module's. A third module-level entry kind is a
    separate step with its own test.
    """
    assert "mcp" not in MODULE_LEVEL_ENTRY_KINDS

    node = Node(
        id="entry_point:4", kind="entry_point", name="MCP:server.py",
        host_id="localhost",
        attributes={
            "source_file": MODULE, "line_start": 1,
            "method": "MCP", "entry_kind": "mcp", "path": MODULE,
        },
    )
    _, edges = run_module(project, {MODULE: MODULE_BODY}, entry=node)

    assert edges == []


def test_the_module_sentinel_alone_is_not_module_level(project) -> None:
    """The marker this step removed, pinned as gone.

    `handler="<module>"` is what the *`EntryPoint` record* carries, and
    B.3b-user_upload-core-module read it off the node — where it never
    appeared on a real scan. With `entry_kind` as the marker, a hand-built
    node carrying only the sentinel is a function-level site whose
    "handler" names nothing, so it earns no edge; the sentinel is not an
    accepted second marker.
    """
    node = Node(
        id="entry_point:5", kind="entry_point",
        name=f"Streamlit:{MODULE}:file_uploader", host_id="localhost",
        attributes={
            "source_file": MODULE, "line_start": 1,
            "method": "STREAMLIT", "path": "file_uploader",
            "handler": "<module>",
        },
    )
    _, edges = run_module(project, {MODULE: MODULE_BODY}, entry=node)

    assert edges == []


def test_without_modules_the_module_branch_guesses_nothing(project) -> None:
    """No AST, no top-level functions, no module body: no edge.

    The same false negative the enclosing-function check has, stated:
    the branch reads the module, and a caller that hands over none gets
    nothing back rather than a guess.
    """
    _, edges = run_module(project, {MODULE: MODULE_BODY}, modules=False)

    assert edges == []


def test_a_function_level_site_still_uses_its_handler(project) -> None:
    """The other branch, unchanged: 0.872 for the handler's own body.

    A function-level site carries a `handler` and no module-level
    `entry_kind`, and everything about it — the BFS start, the base, the
    context-in-body signal — is B.3b-user_upload-core's.
    """
    built, edges = run_module(
        project, {MODULE: HANDLER_SITE}, entry=_function_entry(),
        call_graph={"handle": set()},
    )

    assert [round(edge.identity_confidence, 4) for edge in edges] == [0.872]
    assert edges[0].source_id == "entry_point:2"
    assert built.nodes("store")[0].attributes["identity_hint"] == LITERAL


def test_a_module_level_site_and_a_function_level_site_are_two_writers(
    project,
) -> None:
    """One run, both branches: the module's writes and the handler's.

    The module-level site reaches the module body's store at the module
    base **and** the write inside the function the module body calls at
    the function base; the function-level site reaches that same write at
    its own 0.872, because the write is in the handler's own body. Two
    entry points, so the temp store carries two claims — one edge per
    site — and the module body's store carries one. Overlap is expected
    and not merged: the edges differ in their source, and a reader of the
    temp file would see both writers.

    The `handle(...)` line at the bottom is what puts the handler's write
    in the module-level site's reach: without it the module body names no
    function and the module-level site claims only its own write
    (`test_the_uncalled_function_is_not_in_the_reach`).
    """
    source = (
        f'with open("{LITERAL}", "wb") as fh:\n'
        "    fh.write('x')\n"
        "\n"
        "def handle(payload):\n"
        f'    with open("{TEMP}", "wb") as fh:\n'
        "        fh.write(payload)\n"
        "\n"
        'handle(b"x")\n'
    )
    built = project(files={MODULE: source})
    module_node = module_entry()
    handler_node = _function_entry()
    built.delta.nodes.extend([
        module_node,
        handler_node,
        store_node("store:0", LITERAL),
        store_node("store:1", TEMP),
    ])

    edges = find_upload_stores(
        delta=built.delta,
        upload_sites=[
            module_site(entry_point=module_node, file_path=MODULE),
            UploadSite(
                entry_point=handler_node, file_path=MODULE, line=5,
                framework="fake",
            ),
        ],
        call_graph={"handle": set()},
        fs_writes=fs_writes(built, {MODULE: source}),
        store_nodes=built.nodes("store"),
        modules={MODULE: built.ctx.module_for(MODULE)},
    )

    assert [
        (edge.source_id, edge.target_id, round(edge.identity_confidence, 4))
        for edge in edges
    ] == [
        ("entry_point:1", "store:0", 0.808),
        ("entry_point:1", "store:1", 0.84),
        ("entry_point:2", "store:1", 0.872),
    ]


def test_the_edges_are_a_function_of_the_sites_and_not_of_a_walk(
    project,
) -> None:
    """Determinism: the reachable set is a set, and only membership is read.

    Two runs over the same fixture produce the same ids in the same
    order, and the order is the write sites' source order — so a
    re-scan of one target produces the same rows.
    """
    files = {MODULE: TWO_FUNCTIONS}
    call_graph = {"save_report": set(), "save_temp": set()}
    stores = (LITERAL, TEMP)

    first, first_edges = run_module(
        project, files, stores=stores, call_graph=call_graph,
    )
    second, second_edges = run_module(
        project, files, stores=stores, call_graph=call_graph,
    )

    def shape(edges):
        return [
            (edge.id, edge.source_id, edge.target_id,
             edge.identity_confidence)
            for edge in edges
        ]

    assert shape(first_edges) == shape(second_edges)
    assert [edge.target_id for edge in first_edges] == [
        store.id for store in first.nodes("store")
    ]

