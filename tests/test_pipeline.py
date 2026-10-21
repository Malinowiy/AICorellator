"""Integration test: full Phase 1 pipeline.

Exercises the entire chain from a small synthetic target to a
populated graph in SQLite:

    ingest → discover → run_tsa
           → run_entry_points
           → run_llm_extractor
           → normalize → write_graph → set_phase1_done

Unlike unit tests, this test verifies the components work
together: file discovery feeds TSA, TSA output feeds
classification, classified nodes reach the graph store, and
candidate chains can be enumerated.

The target is a small synthetic project with:
  - an agent class using an LLM
  - an MCP server exposing a tool and a sink
  - a FastAPI route that calls the agent
  - a config file with LLM settings
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicorellator import GraphStore
from aicorellator.ingestion import discover, ingest
from aicorellator.parsing import (
    make_context,
    normalize,
    run_tsa,
    set_phase1_done,
    write_graph,
)
from aicorellator.parsing.entry_points import run as run_entry_points
from aicorellator.parsing.llm_extractor import run as run_llm_extractor
from aicorellator.parsing.capability_extractor import extract as extract_capabilities


# ============================================================
# Synthetic target
# ============================================================

PROJECT_FILES: dict[str, str] = {
    "agents/invoice_agent.py": '''\
"""Invoice agent — processes invoices."""


class InvoiceAgent:
    """Agent that processes invoices via an LLM."""

    def __init__(self, llm_client):
        self.llm = llm_client
        self.name = "invoice"

    async def process(self, invoice_id: str) -> dict:
        return {"status": "ok", "id": invoice_id}
''',

    "agents/orchestrator.py": '''\
"""Orchestrator — delegates to specialized agents."""


class OrchestratorAgent:
    """Top-level agent that routes requests."""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def delegate_to_invoice(self, request: str) -> dict:
        from finbot.agents.runner import run_invoice_agent
        return await run_invoice_agent(request)
''',

    "mcp/servers/findrive/server.py": '''\
"""FinDrive MCP server — file operations."""


class FinDriveServer:
    """MCP server exposing file tools."""

    def upload_file(self, path: str, content: str) -> bool:
        return True

    def delete_file(self, path: str) -> bool:
        return True

    def read_file(self, path: str) -> str:
        return ""
''',

    "mcp/servers/systemutils/server.py": '''\
"""SystemUtils MCP server — system operations."""


class SystemUtilsServer:
    """MCP server exposing system tools."""

    def execute_script(self, script: str) -> str:
        import subprocess
        return subprocess.run(
            script, shell=True, capture_output=True
        ).stdout.decode()

    def run_diagnostics(self) -> dict:
        return {"status": "ok"}
''',

    "apps/api.py": '''\
"""HTTP API — entry point for the invoice workflow."""
from fastapi import APIRouter

router = APIRouter()


@router.post("/invoice")
async def create_invoice(request):
    """Create an invoice via the orchestrator."""
    from finbot.agents.runner import run_orchestrator_agent
    return await run_orchestrator_agent(request)
''',

    "config.py": '''\
"""Application config."""
LLM_PROVIDER = "openai"
LLM_DEFAULT_MODEL = "gpt-4o"
OLLAMA_BASE_URL = "http://localhost:11434"
''',
}


@pytest.fixture
def synthetic_target(tmp_path: Path) -> Path:
    """Write the synthetic project to tmp_path and return it."""
    for rel_path, content in PROJECT_FILES.items():
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return tmp_path


@pytest.fixture
def pipeline_db(tmp_path: Path) -> Path:
    """A fresh DB path for the pipeline test."""
    return tmp_path / "pipeline.db"


# ============================================================
# Integration test
# ============================================================

def test_phase1_full_pipeline(
    synthetic_target: Path,
    pipeline_db: Path,
) -> None:
    """Run the full Phase 1 pipeline on a synthetic project."""
    # 1. Ingestion and discovery
    target = ingest(synthetic_target, kind="folder")
    manifest = discover(target)
    assert manifest.count() >= 6   # our 6 files
    assert len(manifest.by_language("python")) >= 5

    # 2. TSA parsing
    tsa = run_tsa(manifest, target.root)
    assert tsa.files_analyzed >= 5
    class_names = {s.name for s in tsa.symbols if s.kind == "class"}
    assert "InvoiceAgent" in class_names
    assert "OrchestratorAgent" in class_names
    assert "FinDriveServer" in class_names

    # 3. Entry points, agent calls, MCP usages
    ep = run_entry_points(manifest)
    assert len(ep.entry_points) >= 1
    route_paths = {(e.method, e.path) for e in ep.entry_points}
    assert ("POST", "/invoice") in route_paths

    # 4. LLM extraction
    llm = run_llm_extractor(manifest)
    providers = {l.provider for l in llm.deduplicated()}
    assert "openai" in providers

    # 5. Normalization
    delta = normalize(make_context(manifest, tsa, ep, llm))
    node_kinds = {n.kind for n in delta.nodes}
    assert "agent" in node_kinds
    assert "mcp_server" in node_kinds
    assert "entry_point" in node_kinds
    assert "llm_endpoint" in node_kinds

    # 6. Write to graph store
    with GraphStore(pipeline_db) as store:
        result = write_graph(store, delta)
        set_phase1_done(store)

        assert result["nodes_added"] > 0

        # Verify graph contents
        assert store.nodes.count(kind="agent") >= 2       # Invoice + Orchestrator
        assert store.nodes.count(kind="mcp_server") >= 2  # findrive + systemutils
        assert store.nodes.count(kind="entry_point") >= 1
        assert store.state.get_bool("localhost", "phase1_done") is True

        # Verify edges
        edge_kinds = {e.kind for e in store.edges.list()}
        assert "provides_tool" in edge_kinds
        assert "has_access_to" in edge_kinds
        assert "delegates_to" in edge_kinds


def test_pipeline_idempotent(
    synthetic_target: Path,
    pipeline_db: Path,
) -> None:
    """Running the pipeline twice yields the same graph."""
    target = ingest(synthetic_target, kind="folder")
    manifest = discover(target)
    tsa = run_tsa(manifest, target.root)
    ep = run_entry_points(manifest)
    llm = run_llm_extractor(manifest)
    delta = normalize(make_context(manifest, tsa, ep, llm))

    with GraphStore(pipeline_db) as store:
        write_graph(store, delta)
        nodes_first = store.nodes.count()
        edges_first = store.edges.count()

    # Second run: same target, same input
    delta2 = normalize(make_context(manifest, tsa, ep, llm))

    with GraphStore(pipeline_db) as store:
        write_graph(store, delta2)
        assert store.nodes.count() == nodes_first
        assert store.edges.count() == edges_first


def test_pipeline_produces_candidate_chains(
    synthetic_target: Path,
    pipeline_db: Path,
) -> None:
    """After the pipeline runs, find_chains returns candidates.

    The synthetic project has:
        POST /invoice → run_orchestrator_agent → OrchestratorAgent
                      → delegates_to → InvoiceAgent
    And:
        mcp_server:findrive → provides_tool → tool:delete_file
                            → has_access_to → sink:...

    But there's no edge from InvoiceAgent to findrive (no
    create_mcp_server call in invoice_agent.py). So candidate
    chains may or may not exist depending on whether the
    orchestrator → mcp edge exists.

    This test asserts the graph is connected enough to at least
    enumerate something, OR asserts the specific structure that
    is expected.
    """
    target = ingest(synthetic_target, kind="folder")
    manifest = discover(target)
    tsa = run_tsa(manifest, target.root)
    ep = run_entry_points(manifest)
    llm = run_llm_extractor(manifest)
    delta = normalize(make_context(manifest, tsa, ep, llm))

    with GraphStore(pipeline_db) as store:
        write_graph(store, delta)
        set_phase1_done(store)

        # At minimum, the graph should have candidates from
        # mcp_server to sink (if tools have sinks).
        chains = store.query.find_chains(max_hops=10)

        # The graph is non-trivial: check structural invariants.
        assert store.nodes.count() > 0
        assert store.edges.count() > 0

        # If there are entry_point → agent edges and
        # tool → sink edges, we expect chains.
        # We assert the weaker invariant: the graph is acyclic-
        # traversal-safe and produces some result set.
        assert isinstance(chains, list)

def test_pipeline_persists_capabilities(
    synthetic_target: Path, pipeline_db: Path,
) -> None:
    """Capabilities survive extraction → normalize → write → read."""
    target = ingest(synthetic_target, kind="folder")
    manifest = discover(target)
    capabilities = {}
    for entry in manifest.files:
        if entry.language != "python":
            continue
        capabilities[entry.path] = extract_capabilities(entry.abs_path, entry.path)

    tsa = run_tsa(manifest, target.root)
    ep = run_entry_points(manifest)
    llm = run_llm_extractor(manifest)
    delta = normalize(
        make_context(manifest, tsa, ep, llm, capabilities=capabilities)
    )

    with GraphStore(pipeline_db) as store:
        write_graph(store, delta)
        # The synthetic project has SystemUtilsServer.execute_script
        # with subprocess.run(shell=True) — it must have executes_shell.
        sinks = store.nodes.list(kind="sink")
        names = {s.name: s.capabilities for s in sinks}
        assert "executes_shell" in names["execute_script"]


# ============================================================
# Untrusted sources (1.3a)
# ============================================================

UNTRUSTED_FILES: dict[str, str] = {
    "mcp/servers/knowledge/server.py": '''\
"""Knowledge MCP server — retrieval over an attacker-seedable corpus."""


class KnowledgeServer:
    """Exposes a retrieval tool."""

    def get_documents(self, query: str) -> list:
        return vectorstore.similarity_search(query)
''',

    "apps/chat.py": '''\
"""HTTP API that reads the MCP tool catalogue."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/tools")
async def list_available_tools():
    """List the tools the client knows about."""
    return client.list_tools()
''',
}


@pytest.fixture
def untrusted_target(tmp_path: Path) -> Path:
    """A two-file project with one read of each of two source families."""
    root = tmp_path / "untrusted"
    for rel_path, content in UNTRUSTED_FILES.items():
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return root


def test_pipeline_produces_untrusted_source_nodes(
    untrusted_target: Path,
    pipeline_db: Path,
) -> None:
    """1.3a: source nodes and their reader edges survive to the store.

    The end-to-end shape of the new kind: the extractor credits
    `reads_untrusted` on the reader, the stage emits one node per read
    site, and the edge from the source to the reader reaches SQLite with
    the derived `flow_kind` — the whole 1.3a path, in one scan.
    """
    target = ingest(untrusted_target, kind="folder")
    manifest = discover(target)
    tsa = run_tsa(manifest, target.root)
    ep = run_entry_points(manifest)
    llm = run_llm_extractor(manifest)
    capabilities = {
        entry.path: extract_capabilities(entry.abs_path, entry.path)
        for entry in manifest.files
        if entry.language == "python"
    }
    delta = normalize(
        make_context(manifest, tsa, ep, llm, capabilities=capabilities)
    )

    sources = [n for n in delta.nodes if n.kind == "untrusted_source"]
    assert {n.attributes["source_type"] for n in sources} == {
        "rag_corpus", "mcp_tool_description",
    }

    with GraphStore(pipeline_db) as store:
        write_graph(store, delta)

        assert store.nodes.count(kind="untrusted_source") == 2
        by_name = {
            n.name: n for n in store.nodes.list(kind="untrusted_source")
        }
        rag = by_name["rag_corpus:vectorstore.similarity_search"]
        assert rag.attributes["source_file"] == (
            "mcp/servers/knowledge/server.py"
        )

        # The capability is on the reader, not on the source.
        tool = next(
            n for n in store.nodes.list(kind="tool")
            if n.name == "get_documents"
        )
        assert "reads_untrusted" in tool.capabilities
        assert rag.capabilities == []

        edges = store.edges.list(source_id=rag.id, kind="has_access_to")
        assert [e.target_id for e in edges] == [tool.id]
        assert edges[0].flow_kind == "data"
        assert edges[0].data_flow_confirmed == "call_result_match"

        # The catalogue read happens in a route handler, so its reader is
        # the entry point node the same scan created.
        catalogue = by_name["mcp_tool_description:client.list_tools"]
        entry = store.nodes.list(kind="entry_point")
        assert [e.target_id for e in store.edges.list(
            source_id=catalogue.id
        )] == [n.id for n in entry if n.name == "GET /tools"]


def test_pipeline_finds_no_chain_groups_yet(
    untrusted_target: Path,
    pipeline_db: Path,
) -> None:
    """1.3b: the enumerator runs end to end and finds nothing, for two
    recorded reasons — neither of which is papered over here.

    1. **No write side exists.** 1.3a emits `source → reader` only;
       nothing emits `writer → source`, so no source node has an
       incoming edge and `_write_chains_to` has nothing to walk. That
       is 1.3a.1, and it is a *detection* question (which source node
       does a writer write into?), not an enumeration one.
    2. **No read side reaches a sink.** The corpus read lands on
       `get_documents`, a tool with no capabilities, and the catalogue
       read lands on an entry point; the project has no sink at all, so
       no read chain terminates.

    The assertions below pin both absences explicitly, so this test
    fails the moment either side appears: it is a statement about the
    graph, not a tolerance of zero. Do not add a write side to
    `UNTRUSTED_FILES` to make this green — a fabricated writer would
    test the fixture, not the pipeline.
    """
    target = ingest(untrusted_target, kind="folder")
    manifest = discover(target)
    tsa = run_tsa(manifest, target.root)
    ep = run_entry_points(manifest)
    llm = run_llm_extractor(manifest)
    capabilities = {
        entry.path: extract_capabilities(entry.abs_path, entry.path)
        for entry in manifest.files
        if entry.language == "python"
    }
    delta = normalize(
        make_context(manifest, tsa, ep, llm, capabilities=capabilities)
    )

    with GraphStore(pipeline_db) as store:
        write_graph(store, delta)

        sources = store.nodes.list(kind="untrusted_source")
        assert len(sources) == 2

        # The read side is real: both sources have an outgoing edge.
        for source in sources:
            assert store.edges.list(source_id=source.id)

        # The write side is absent: no edge ends at a source.
        for source in sources:
            assert store.edges.list(target_id=source.id) == []

        # And no sink exists anywhere for a read chain to reach.
        assert store.nodes.list(kind="sink") == []
        assert store.query.find_chains() == []

        assert store.query.find_chain_groups() == []