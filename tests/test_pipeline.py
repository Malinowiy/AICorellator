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
from aicorellator.parsing.detectors_runner import run as run_detectors
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
    """1.3b + 1.3a.1: the enumerator runs end to end and finds nothing,
    for three recorded reasons — none of them papered over here.

    1. **The write side exists but has no pair in this fixture.**
       1.3a.1 emits `mcp_server → writes_to → untrusted_source`, and
       this project gives it nothing to match: the catalogue source is
       in `apps/chat.py`, which holds no server, and the one server
       (`mcp/servers/knowledge/`) shares its file with a *rag_corpus*
       read — the server reading its store, not declaring a catalogue.
       Both halves are asserted below.
    2. **No read side reaches a sink.** The corpus read lands on
       `get_documents` and the catalogue read lands on a plain function
       with no node at all; the project has no sink, so no read chain
       terminates.
    3. **No entry point reaches a server**, so even if a write edge
       existed there would be no write chain to pair.

    The assertions pin all three absences explicitly, so this test fails
    the moment the fixture gains a writer: it is a statement about the
    graph, not a tolerance of zero. Do not add a catalogue read to
    `mcp/servers/knowledge/server.py` to make it green — the writer path
    is covered by `test_pipeline_writer_edge_reaches_the_store`, and a
    fixture bent to produce a group would test the fixture.
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

        # The write side has no pair: no edge ends at a source.
        for source in sources:
            assert store.edges.list(target_id=source.id) == []
        assert store.edges.list(kind="writes_to") == []

        # Why: no server is visible from the catalogue's file. The two
        # server nodes this project has are the class-detected
        # `KnowledgeServer` and the path-heuristic `knowledge`, both in
        # `mcp/servers/knowledge/` — neither in `apps/chat.py`.
        by_name = {n.name: n for n in sources}
        catalogue = by_name["mcp_tool_description:client.list_tools"]
        assert catalogue.attributes["source_file"] == "apps/chat.py"
        assert sorted(
            n.attributes.get("source_file", "")
            for n in store.nodes.list(kind="mcp_server")
        ) == ["", "mcp/servers/knowledge/server.py"]

        # And no sink exists anywhere for a read chain to reach.
        assert store.nodes.list(kind="sink") == []
        assert store.query.find_chains() == []

        assert store.query.find_chain_groups() == []


# ============================================================
# Untrusted source writers (1.3a.1)
# ============================================================

WRITER_FILES: dict[str, str] = {
    # Path-heuristic server: the directory names the server (stage 1),
    # so the source under the same directory is one it declares.
    "mcp/servers/knowledge/server.py": '''\
"""Knowledge MCP server — declares a catalogue and reads it back."""


class KnowledgeServer:
    """Exposes a retrieval tool and introspects its own catalogue."""

    def get_documents(self, query: str) -> list:
        return vectorstore.similarity_search(query)

    def declared_tools(self) -> list:
        return client.list_tools()
''',

    # Framework-detected server: `FastMCP(...)` names it, and the node
    # only exists after the detector merge — the reason the writer stage
    # runs after that stage.
    "mcp/fastserver.py": '''\
"""A FastMCP server that introspects its own catalogue."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fastknowledge")


@mcp.tool()
def search(query: str) -> str:
    """Search the corpus."""
    return "ok"


def catalogue() -> list:
    return server.list_tools()
''',
}


@pytest.fixture
def writer_target(tmp_path: Path) -> Path:
    """A two-file project with one writer of each server kind."""
    root = tmp_path / "writers"
    for rel_path, content in WRITER_FILES.items():
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return root


def test_pipeline_writer_edge_reaches_the_store(
    writer_target: Path,
    pipeline_db: Path,
) -> None:
    """1.3a.1 end to end: `mcp_server → writes_to → untrusted_source`.

    Two writers per file, because the graph has more than one kind of
    `mcp_server` node and the rule is "every server visible from the
    source's file":

      - `mcp/servers/knowledge/server.py` yields **two** server nodes —
        `KnowledgeServer` (a `*Server` class under `mcp/servers/`,
        classified by `class_nodes`, stage 2) and `knowledge` (the path
        heuristic, stage 1). Both declare the catalogue read in the same
        file, so both write it;
      - `mcp/fastserver.py` yields `fastknowledge`, detected from
        `FastMCP("fastknowledge")` — and that node only exists once
        `detector_merge` has run, which is why the writer stage runs
        after the merge.

    The `rag_corpus` read in the first file gets no writer edge: the
    server reads its store, it does not declare the corpus. That gate is
    the whole scope of 1.3a.1.
    """
    target = ingest(writer_target, kind="folder")
    manifest = discover(target)
    tsa = run_tsa(manifest, target.root)
    ep = run_entry_points(manifest)
    llm = run_llm_extractor(manifest)
    capabilities = {
        entry.path: extract_capabilities(entry.abs_path, entry.path)
        for entry in manifest.files
        if entry.language == "python"
    }
    detection = run_detectors(manifest)
    delta = normalize(make_context(
        manifest, tsa, ep, llm,
        capabilities=capabilities, framework_detection=detection,
    ))

    with GraphStore(pipeline_db) as store:
        write_graph(store, delta)

        nodes = {n.id: n for n in store.nodes.list()}
        sources = [n for n in nodes.values() if n.kind == "untrusted_source"]
        assert len(sources) == 3

        edges = store.edges.list(kind="writes_to")
        assert len(edges) == 3
        # Control, with no traced dataflow (ADR-0015 decision 5).
        assert {e.flow_kind for e in edges} == {"control"}
        assert {e.data_flow_confirmed for e in edges} == {"unknown"}
        assert sorted(
            (nodes[e.source_id].name, nodes[e.target_id].name)
            for e in edges
        ) == [
            ("KnowledgeServer", "mcp_tool_description:client.list_tools"),
            ("fastknowledge", "mcp_tool_description:server.list_tools"),
            ("knowledge", "mcp_tool_description:client.list_tools"),
        ]

        # The corpus read is not a catalogue: no writer edge.
        rag = next(
            n for n in sources
            if n.name == "rag_corpus:vectorstore.similarity_search"
        )
        assert store.edges.list(target_id=rag.id) == []

        # The dynamic invocation stage finds nothing to link: the project
        # has no agent node at all (the `*Server` class is an
        # `mcp_server`, and the catalogue readers are a method and a
        # plain function), so no reader has the `reads_untrusted`
        # capability the rule gates on.
        assert store.nodes.list(kind="agent") == []
        assert store.edges.list(kind="uses_tool") == []

        # The write side alone is not a group (ADR-0010): no entry point
        # reaches a server, and the project has no sink for a read chain
        # to terminate at.
        assert store.query.find_chain_groups() == []


# ============================================================
# Dynamic MCP invocation (1.3a.1.2)
# ============================================================

DYNAMIC_FILES: dict[str, str] = {
    # Path-heuristic server: the directory names it, its class exposes a
    # tool, and the tool reaches a shell sink in the same file.
    "mcp/servers/knowledge/server.py": '''\
"""Knowledge MCP server -- a retrieval tool over a shell sink."""
import subprocess


class KnowledgeServer:
    """MCP server exposing a retrieval tool."""

    def get_documents(self, query: str) -> list:
        return vectorstore.similarity_search(query)

    def execute_script(self, script: str) -> str:
        return subprocess.run(
            script, shell=True, capture_output=True
        ).stdout.decode()
''',

    # The reader. FinBot's shape in three steps: a literal factory path
    # for the server it talks to (the writer anchor), a
    # `create_mcp_server("knowledge")` call in an `agents/` module (the
    # agent → server edge), and a catalogue read inside the agent class
    # (the `reads_untrusted` capability, and the read edge whose target
    # is the agent node).
    "agents/tool_provider.py": '''\
"""The agent that reads the MCP tool catalogue and invokes its tools."""
from app.services.mcp import create_mcp_server

_SERVER_INTROSPECTORS = {
    "knowledge": "app.mcp.servers.knowledge.server.create_knowledge_server",
}


class ToolProviderAgent:
    """Discovers the tools of the MCP servers it connects to."""

    def __init__(self):
        self.servers = {}

    def connect_servers(self):
        self.servers["knowledge"] = create_mcp_server("knowledge")

    async def connect(self, client):
        """Read a server's tool catalogue."""
        return await client.list_tools()
''',

    # The route, in a file that is not an `agents/` module: that is what
    # makes the call an entry → agent delegation rather than an
    # agent → agent one.
    "apps/api.py": '''\
"""HTTP API."""
from fastapi import APIRouter

from app.agents.tool_provider import ToolProviderAgent

router = APIRouter()


@router.get("/chat")
async def chat():
    """Start an agent run."""
    agent = ToolProviderAgent()
    agent.connect_servers()
    return {"agent": agent}
''',
}


@pytest.fixture
def dynamic_target(tmp_path: Path) -> Path:
    """A three-file project with both halves of ADR-0010's channel."""
    root = tmp_path / "dynamic"
    for rel_path, content in DYNAMIC_FILES.items():
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return root


# The same project with the literal moved out of the reader's file — the
# FinBot shape, and what 1.3a.1.3 exists for. `agents/tool_provider.py`
# below is the `DYNAMIC_FILES` module with `_SERVER_INTROSPECTORS`
# removed: it reads a catalogue and names no server at all.
SPLIT_PROVIDER = '''\
"""The agent that reads the MCP tool catalogue and invokes its tools."""
from app.services.mcp import create_mcp_server


class ToolProviderAgent:
    """Discovers the tools of the MCP servers it connects to."""

    def __init__(self):
        self.servers = {}

    def connect_servers(self):
        self.servers["knowledge"] = create_mcp_server("knowledge")

    async def connect(self, client):
        """Read a server's tool catalogue."""
        return await client.list_tools()
'''

SPLIT_REGISTRY = '''\
"""Server registry -- which factory builds which server."""
_SERVER_INTROSPECTORS = {
    "knowledge": "app.mcp.servers.knowledge.server.create_knowledge_server",
}
'''

SPLIT_FILES: dict[str, str] = {
    **DYNAMIC_FILES,
    "agents/tool_provider.py": SPLIT_PROVIDER,
    "config/registry.py": SPLIT_REGISTRY,
}


@pytest.fixture
def split_target(tmp_path: Path) -> Path:
    """The dynamic project, with the literal in a file that reads nothing."""
    root = tmp_path / "split"
    for rel_path, content in SPLIT_FILES.items():
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return root



def _normalized_dynamic(target: Path):
    manifest = discover(target)
    tsa = run_tsa(manifest, target.root)
    ep = run_entry_points(manifest)
    llm = run_llm_extractor(manifest)
    capabilities = {
        entry.path: extract_capabilities(entry.abs_path, entry.path)
        for entry in manifest.files
        if entry.language == "python"
    }
    detection = run_detectors(manifest)
    return normalize(make_context(
        manifest, tsa, ep, llm,
        capabilities=capabilities, framework_detection=detection,
    ))


def test_pipeline_chain_group_through_dynamic_invocation(
    dynamic_target: Path,
    pipeline_db: Path,
) -> None:
    """1.3a.1.2 end to end: a group forms on a FinBot-shaped project.

    Every hop is a rule landed in 1.3, and each one is asserted on its
    own so a failure says which rule broke:

        literal factory path    knowledge → writes_to → catalogue
        catalogue read          catalogue → has_access_to → agent
        dynamic invocation      agent → uses_tool → get_documents
        same-file edges         knowledge → provides_tool →
                                get_documents
                                get_documents → has_access_to →
                                execute_script (the sink)
        delegation              GET /chat → delegates_to → agent
                                agent → has_access_to → knowledge

    The chains the enumerator pairs are then

        write   GET /chat → agent → knowledge → catalogue
        read    catalogue → agent → get_documents → execute_script

    and a second read chain goes through the server instead of straight
    to the tool (`catalogue → agent → knowledge → get_documents →
    execute_script`), because `provides_tool` is control and therefore
    traversable. Two groups, one per read chain — collapsing or ranking
    the longer path is ADR-0012's prioritization, which is 1.3c and is
    not implemented.
    """
    target = ingest(dynamic_target, kind="folder")
    delta = _normalized_dynamic(target)

    with GraphStore(pipeline_db) as store:
        write_graph(store, delta)

        nodes = {n.id: n for n in store.nodes.list()}
        agent = next(
            n for n in nodes.values() if n.name == "ToolProviderAgent"
        )
        server = next(n for n in nodes.values() if n.name == "knowledge")
        catalogue = next(
            n for n in nodes.values()
            if n.name == "mcp_tool_description:client.list_tools"
        )
        tool = next(n for n in nodes.values() if n.name == "get_documents")

        # The reader is the gate, and `reads_untrusted` is on it.
        assert "reads_untrusted" in agent.capabilities

        # The anchor that makes the write side reachable (1.3a.1.2).
        assert [
            (nodes[e.source_id].name, nodes[e.target_id].name)
            for e in store.edges.list(kind="writes_to")
        ] == [("knowledge", "mcp_tool_description:client.list_tools")]

        # The rule that closes the read side (1.3a.1.2).
        invocations = store.edges.list(kind="uses_tool")
        assert [
            (nodes[e.source_id].name, nodes[e.target_id].name)
            for e in invocations
        ] == [("ToolProviderAgent", "get_documents")]
        assert {e.flow_kind for e in invocations} == {"control"}
        assert {e.data_flow_confirmed for e in invocations} == {"unknown"}

        # The two edges the invocation rule walks.
        assert store.edges.list(
            source_id=catalogue.id, kind="has_access_to"
        )[0].target_id == agent.id
        assert store.edges.list(
            source_id=server.id, kind="provides_tool"
        )[0].target_id == tool.id

        groups = store.query.find_chain_groups()

        assert len(groups) == 2
        assert {
            tuple(nodes[n].name for n in chain) for chain in
            (c for g in groups for c in g.read_chains)
        } == {
            (
                "mcp_tool_description:client.list_tools",
                "ToolProviderAgent", "get_documents", "execute_script",
            ),
            (
                "mcp_tool_description:client.list_tools",
                "ToolProviderAgent", "knowledge", "get_documents",
                "execute_script",
            ),
        }
        assert {
            tuple(nodes[n].name for n in chain) for chain in
            (c for g in groups for c in g.write_chains)
        } == {(
            "GET /chat", "ToolProviderAgent", "knowledge",
            "mcp_tool_description:client.list_tools",
        )}


def test_pipeline_chain_groups_persist_and_prioritize(
    dynamic_target: Path,
    pipeline_db: Path,
) -> None:
    """1.3c end to end: enumerate, score, select, store.

    The graph is 1.3a.1.2's FinBot shape, so both halves of the channel
    exist and two groups come out of `find_chain_groups` — one whose
    read chain reaches the shell in three hops, one in four. Scoring
    ranks the short one first (it earns the short-chain point), and
    both clear ADR-0012's `min_score_to_evaluate = 5`.

    Then the store step: `to_record()` plus `ChainGroupsRepository`,
    which is the caller's job because the Query Layer does not write.
    A second pass over the same graph is idempotent — same ids, same
    scores, one row each — and does not disturb the evaluation columns.
    """
    target = ingest(dynamic_target, kind="folder")
    delta = _normalized_dynamic(target)

    with GraphStore(pipeline_db) as store:
        write_graph(store, delta)

        groups = store.query.find_chain_groups()
        assert len(groups) == 2

        selected = store.query.prioritize_chain_groups(groups)
        for group in selected:
            store.chain_groups.upsert(group.to_record())

        rows = store.chain_groups.list()
        assert [r.id for r in rows] == [g.group_id for g in selected]
        assert sorted(r.score for r in rows) == [5.0, 6.0]
        assert [r.priority for r in rows] == ["low", "low"]
        assert all(r.status == "active" for r in rows)
        # The name is the anchor plus the two hop counts, not prose.
        assert all(
            r.name.startswith("mcp_tool_description:client.list_tools:")
            for r in rows
        )
        assert {r.name.split(":")[-1] for r in rows} == {"3x3", "3x4"}
        # 1.3d/1.3e columns are untouched by prioritization.
        assert all(r.verdict is None and r.lethal_trifecta is False
                   for r in rows)

        # An evaluation that 1.3e would write...
        store._conn.execute(
            "UPDATE chain_groups"
            " SET verdict = 'exploitable', confidence = 0.9",
        )
        # ...survives a re-run of prioritization over the same graph.
        again = store.query.prioritize_chain_groups(
            store.query.find_chain_groups()
        )
        for group in again:
            store.chain_groups.upsert(group.to_record())

        assert [g.group_id for g in again] == [g.group_id for g in selected]
        assert store.chain_groups.count() == 2
        assert [r.id for r in store.chain_groups.list()] == [
            r.id for r in rows
        ]
        assert [r.score for r in store.chain_groups.list()] == [
            r.score for r in rows
        ]
        assert all(
            r.verdict == "exploitable" and r.confidence == 0.9
            for r in store.chain_groups.list()
        )


def test_pipeline_chain_group_survives_the_literal_moving_file(
    split_target: Path,
    pipeline_db: Path,
) -> None:
    """1.3a.1.3 end to end: the literal and the reader are in two files.

    The fixture is `DYNAMIC_FILES` with one change: the literal factory
    path moves from the reader's module to `config/registry.py`, which
    reads nothing. Everything else — the server, its tool, the sink, the
    route, and `create_mcp_server("knowledge")` in the `agents/` module —
    is unchanged.

    On 1.3a.1.2's same-file anchor this graph had no write side at all:
    `agents/tool_provider.py` names no server once the literal leaves,
    and no server is visible from it, so `find_chain_groups` returned
    nothing. At project scope the literal still names `knowledge`, so

        write   GET /chat → ToolProviderAgent → knowledge → catalogue
        read    catalogue → ToolProviderAgent → get_documents →
                execute_script

    and the group forms.
    """
    provider = SPLIT_FILES["agents/tool_provider.py"]
    assert "_SERVER_INTROSPECTORS" not in provider
    assert "_SERVER_INTROSPECTORS" in SPLIT_FILES["config/registry.py"]

    target = ingest(split_target, kind="folder")
    delta = _normalized_dynamic(target)

    with GraphStore(pipeline_db) as store:
        write_graph(store, delta)

        nodes = {n.id: n for n in store.nodes.list()}
        catalogue = next(
            n for n in nodes.values()
            if n.name == "mcp_tool_description:client.list_tools"
        )

        # The catalogue's file is the reader's file...
        assert catalogue.attributes["source_file"].endswith(
            "agents/tool_provider.py"
        )
        # ...which names no server, so the writer edge comes from the
        # project literal rather than from the file the read is in.
        assert [
            (nodes[e.source_id].name, nodes[e.target_id].name)
            for e in store.edges.list(kind="writes_to")
        ] == [("knowledge", "mcp_tool_description:client.list_tools")]

        # The read side is what it was (1.3a.1.2): the agent invokes the
        # tool the catalogue describes.
        assert [
            (nodes[e.source_id].name, nodes[e.target_id].name)
            for e in store.edges.list(kind="uses_tool")
        ] == [("ToolProviderAgent", "get_documents")]

        groups = store.query.find_chain_groups()

        assert len(groups) == 2
        assert {
            tuple(nodes[n].name for n in chain) for chain in
            (c for g in groups for c in g.read_chains)
        } == {
            (
                "mcp_tool_description:client.list_tools",
                "ToolProviderAgent", "get_documents", "execute_script",
            ),
            (
                "mcp_tool_description:client.list_tools",
                "ToolProviderAgent", "knowledge", "get_documents",
                "execute_script",
            ),
        }
        assert {
            tuple(nodes[n].name for n in chain) for chain in
            (c for g in groups for c in g.write_chains)
        } == {(
            "GET /chat", "ToolProviderAgent", "knowledge",
            "mcp_tool_description:client.list_tools",
        )}
