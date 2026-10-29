"""CrossFileResolutionStage: `has_access_to` (tool → sink) across imports (B.1).

`has_access_to` between a tool and a sink is otherwise built only *within*
a single file (`intra_file_edges`). A tool in `server.py` that calls a
sink in an imported `repositories.py` gets no edge, and `find_chains`
loses the path. Four scenarios, one resolver:

  1. the tool imports the module that holds the sink it reaches → one edge
  2. the tool does not import the sink's module → no edge
  3. the sink is in the tool's own file → not duplicated
  4. an import cycle → the resolver terminates

The edges are control edges: `flow_kind="control"`, `observed_by=["ast"]`,
`data_flow_confirmed is None` (ADR-0015, ADR-0016 decision 3).
"""

from __future__ import annotations

import ast

from aicorellator.graph.models import Edge
from aicorellator.parsing.capability_extractor import (
    FileCapabilities,
    SymbolCapabilities,
)
from aicorellator.parsing.entry_points import EntryPointOutput
from aicorellator.parsing.models import TSASymbol
from aicorellator.parsing.normalizer_helpers import edge_id
from aicorellator.parsing.normalizer_stages import (
    CrossFileResolutionStage,
    IntraFileEdgeStage,
    McpServerNodeStage,
    MethodNodeStage,
)

SERVER = "mcp/servers/findrive/server.py"
REPOSITORIES = "mcp/servers/findrive/repositories.py"

SERVER_SOURCE = '''\
from repositories import delete_file


class FinDriveServer:
    def get_file(self, file_id):
        return delete_file(file_id)
'''

REPOSITORIES_SOURCE = '''\
import os


def delete_file(file_id):
    os.remove(file_id)
'''

# Scenario 3: the tool and the sink live in one file.
SAME_FILE_SOURCE = '''\
import os


class FinDriveServer:
    def get_file(self, file_id):
        return self.delete_file(file_id)

    def delete_file(self, file_id):
        os.remove(file_id)
'''

# Scenario 4: two files that import each other.
CYCLE_A = "mcp/servers/cyc/a.py"
CYCLE_B = "mcp/servers/cyc/b.py"

CYCLE_A_SOURCE = '''\
import os

from b import delete_b


def delete_a(x):
    os.remove(x)


class A:
    def get_a(self, x):
        return delete_b(x)
'''

CYCLE_B_SOURCE = '''\
import os

from a import delete_a


def delete_b(x):
    os.remove(x)


class B:
    def get_b(self, x):
        return delete_a(x)
'''


def _line_of(source: str, name: str) -> int:
    """The real line of `name` in `source`, as TSA would report it."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node.lineno
    raise AssertionError(name)


def _symbol(
    name: str,
    kind: str,
    file_path: str,
    source: str,
    class_name: str | None = None,
) -> TSASymbol:
    line = _line_of(source, name)
    return TSASymbol(
        name=name, kind=kind, file_path=file_path,
        line_start=line, line_end=line + 4, class_name=class_name,
    )


def _caps(*entries: tuple[str, list[str]]) -> dict[str, FileCapabilities]:
    """`(file, [names])` → capabilities, `writes_filesystem` on each name.

    `writes_filesystem` is a sink capability (ADR-0016, decision 7): the
    capability is the behaviour the body shows, not the name.
    """
    out: dict[str, FileCapabilities] = {}
    for file_path, names in entries:
        out[file_path] = FileCapabilities(
            file_path=file_path,
            symbols=[
                SymbolCapabilities(
                    symbol_name=name,
                    file_path=file_path,
                    capabilities={"writes_filesystem"},
                )
                for name in names
            ],
        )
    return out


def _scenario_one(project):
    """Scenario 1, with the node stages run (the real order)."""
    p = project(
        files={SERVER: SERVER_SOURCE, REPOSITORIES: REPOSITORIES_SOURCE},
        symbols=[
            _symbol("get_file", "method", SERVER, SERVER_SOURCE,
                    "FinDriveServer"),
            _symbol("delete_file", "method", REPOSITORIES,
                    REPOSITORIES_SOURCE),
        ],
        capabilities=_caps((REPOSITORIES, ["delete_file"])),
        entry_points=EntryPointOutput(
            imports={SERVER: ["repositories"]},
            call_graph={"get_file": {"delete_file"}},
        ),
    )
    McpServerNodeStage().apply(p.delta, p.ctx)
    MethodNodeStage().apply(p.delta, p.ctx)
    return p


# ============================================================
# 1. Import + sink in another file → edge exists
# ============================================================

def test_a_tool_reaches_a_sink_in_an_imported_module(project) -> None:
    """server.py imports repositories.py and calls its sink."""
    p = _scenario_one(project)

    CrossFileResolutionStage().apply(p.delta, p.ctx)

    tool = p.named("get_file")
    sink = p.named("delete_file")
    access = p.edges("has_access_to")
    assert [(e.source_id, e.target_id) for e in access] == [(tool.id, sink.id)]
    # Control-flow reachability, not dataflow (ADR-0015, ADR-0016).
    assert access[0].flow_kind == "control"
    assert access[0].observed_by == ["ast"]
    assert access[0].data_flow_confirmed is None


# ============================================================
# 2. No import → no edge
# ============================================================

def test_a_tool_that_does_not_import_the_sink_module_gets_no_edge(
    project,
) -> None:
    """Same files, same call graph, no import: nothing to resolve."""
    p = project(
        files={SERVER: SERVER_SOURCE, REPOSITORIES: REPOSITORIES_SOURCE},
        symbols=[
            _symbol("get_file", "method", SERVER, SERVER_SOURCE,
                    "FinDriveServer"),
            _symbol("delete_file", "method", REPOSITORIES,
                    REPOSITORIES_SOURCE),
        ],
        capabilities=_caps((REPOSITORIES, ["delete_file"])),
        entry_points=EntryPointOutput(
            imports={},                     # server.py imports nothing
            call_graph={"get_file": {"delete_file"}},
        ),
    )
    McpServerNodeStage().apply(p.delta, p.ctx)
    MethodNodeStage().apply(p.delta, p.ctx)

    CrossFileResolutionStage().apply(p.delta, p.ctx)

    assert p.edges("has_access_to") == []


# ============================================================
# 3. Sink in the same file → not duplicated
# ============================================================

def test_a_sink_in_the_same_file_is_not_duplicated(project) -> None:
    """`intra_file_edges` builds the same-file edge; this stage must not
    add a second one."""
    p = project(
        files={SERVER: SAME_FILE_SOURCE},
        symbols=[
            _symbol("get_file", "method", SERVER, SAME_FILE_SOURCE,
                    "FinDriveServer"),
            _symbol("delete_file", "method", SERVER, SAME_FILE_SOURCE,
                    "FinDriveServer"),
        ],
        capabilities=_caps((SERVER, ["delete_file"])),
        entry_points=EntryPointOutput(
            call_graph={"get_file": {"delete_file"}},
        ),
    )
    McpServerNodeStage().apply(p.delta, p.ctx)
    MethodNodeStage().apply(p.delta, p.ctx)
    IntraFileEdgeStage().apply(p.delta, p.ctx)      # same-file has_access_to

    CrossFileResolutionStage().apply(p.delta, p.ctx)

    access = p.edges("has_access_to")
    ids = [e.id for e in access]
    assert len(ids) == len(set(ids)) == 1
    assert access[0].flow_kind == "control"


# ============================================================
# 4. Import cycle → the resolver terminates
# ============================================================

def test_an_import_cycle_terminates(project) -> None:
    """a.py imports b.py and b.py imports a.py; both hold a tool and a
    sink. The resolver must terminate and build one edge per pair."""
    p = project(
        files={CYCLE_A: CYCLE_A_SOURCE, CYCLE_B: CYCLE_B_SOURCE},
        symbols=[
            _symbol("get_a", "method", CYCLE_A, CYCLE_A_SOURCE, "A"),
            _symbol("delete_a", "method", CYCLE_A, CYCLE_A_SOURCE),
            _symbol("get_b", "method", CYCLE_B, CYCLE_B_SOURCE, "B"),
            _symbol("delete_b", "method", CYCLE_B, CYCLE_B_SOURCE),
        ],
        capabilities=_caps(
            (CYCLE_A, ["delete_a"]),
            (CYCLE_B, ["delete_b"]),
        ),
        entry_points=EntryPointOutput(
            imports={CYCLE_A: ["b"], CYCLE_B: ["a"]},
            call_graph={"get_a": {"delete_b"}, "get_b": {"delete_a"}},
        ),
    )
    McpServerNodeStage().apply(p.delta, p.ctx)
    MethodNodeStage().apply(p.delta, p.ctx)

    # No RecursionError / TimeoutError: the loops are over finite maps.
    CrossFileResolutionStage().apply(p.delta, p.ctx)

    pairs = sorted(
        (e.source_id, e.target_id) for e in p.edges("has_access_to")
    )
    assert len(pairs) == 2
    assert len(set(pairs)) == 2


# ============================================================
# Dedupe against edge ids the delta already has
# ============================================================

def test_an_edge_the_delta_already_has_is_not_emitted_twice(project) -> None:
    """The edge id is content-derived, so an existing edge wins."""
    p = _scenario_one(project)
    tool = p.named("get_file")
    sink = p.named("delete_file")
    p.delta.edges.append(Edge(
        id=edge_id(tool.id, sink.id, "has_access_to"),
        source_id=tool.id,
        target_id=sink.id,
        kind="has_access_to",
        host_id="localhost",
    ))

    CrossFileResolutionStage().apply(p.delta, p.ctx)

    assert len(p.edges("has_access_to")) == 1


# ============================================================
# Determinism
# ============================================================

def test_the_same_fixture_produces_the_same_edge_ids(project) -> None:
    """Two runs over the same fixture → the same set of edge ids."""

    def run() -> list[str]:
        p = _scenario_one(project)
        CrossFileResolutionStage().apply(p.delta, p.ctx)
        return sorted(e.id for e in p.edges("has_access_to"))

    assert run() == run()
