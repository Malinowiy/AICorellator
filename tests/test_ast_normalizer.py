"""Unit tests for AST Normalizer.

Tests use hand-built FileManifest and TSAOutput objects — no file
system access, no TSA subprocess. Fast and deterministic.
"""

from pathlib import Path

import pytest

from aicorellator.ingestion.discovery import FileEntry, FileManifest
from aicorellator.ingestion.target import Target
from aicorellator.parsing.ast_normalizer import (
    GraphDelta,
    _edge_id,
    _mcp_server_id,
    _node_from_class,
    _node_from_method,
    _server_name_for_file,
    _sha256,
    normalize,
)
from aicorellator.parsing.classification import Classification
from aicorellator.parsing.entry_points import (
    AgentCall,
    EntryPoint,
    EntryPointOutput,
)
from aicorellator.parsing.models import TSASymbol, TSAOutput

# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def target(tmp_path: Path) -> Target:
    """A target pointing at an empty temp directory."""
    return Target(root=tmp_path, kind="folder")


@pytest.fixture
def manifest_with_findrive(target: Target) -> FileManifest:
    """A manifest containing two files under mcp/servers/findrive/."""
    m = FileManifest(target=target)
    m.files = [
        FileEntry(
            path="mcp/servers/findrive/server.py",
            abs_path=target.root / "mcp/servers/findrive/server.py",
            language="python",
            content_hash="aaa",
            size=100,
        ),
        FileEntry(
            path="mcp/servers/findrive/repositories.py",
            abs_path=target.root / "mcp/servers/findrive/repositories.py",
            language="python",
            content_hash="bbb",
            size=200,
        ),
    ]
    return m


@pytest.fixture
def tsa_with_agent() -> TSAOutput:
    """TSA output with a single agent class."""
    return TSAOutput(
        symbols=[
            TSASymbol(
                name="InvoiceAgent",
                kind="class",
                file_path="agents/invoice.py",
                line_start=10,
                line_end=50,
            ),
        ],
    )


# ============================================================
# Helpers
# ============================================================

def test_sha256_deterministic() -> None:
    assert _sha256("foo") == _sha256("foo")
    assert _sha256("foo") != _sha256("bar")
    assert len(_sha256("foo")) == 64


def test_mcp_server_id_stable() -> None:
    id1 = _mcp_server_id("findrive")
    id2 = _mcp_server_id("findrive")
    assert id1 == id2
    assert id1.startswith("mcp_server:")


def test_mcp_server_id_differs_by_name() -> None:
    assert _mcp_server_id("findrive") != _mcp_server_id("finmail")


def test_edge_id_stable() -> None:
    e1 = _edge_id("a", "b", "provides_tool")
    e2 = _edge_id("a", "b", "provides_tool")
    assert e1 == e2
    assert e1.startswith("edge:")


def test_edge_id_differs_by_kind() -> None:
    assert _edge_id("a", "b", "provides_tool") != _edge_id("a", "b", "has_access_to")


def test_server_name_for_file_findrive() -> None:
    assert _server_name_for_file("mcp/servers/findrive/server.py") == "findrive"


def test_server_name_for_file_non_mcp() -> None:
    assert _server_name_for_file("agents/invoice.py") is None
    assert _server_name_for_file("mcp/servers/__init__.py") is None


# ============================================================
# Node construction
# ============================================================

def test_node_from_class_agent() -> None:
    symbol = TSASymbol(
        name="InvoiceAgent",
        kind="class",
        file_path="agents/invoice.py",
        line_start=10,
        line_end=50,
    )
    c = Classification(kind="agent")
    node = _node_from_class(symbol, c)
    assert node.kind == "agent"
    assert node.name == "InvoiceAgent"
    assert node.id.startswith("agent:")
    assert node.attributes["source_file"] == "agents/invoice.py"
    assert node.attributes["line_start"] == 10


def test_node_from_class_llm_endpoint_has_provider() -> None:
    symbol = TSASymbol(
        name="OllamaClient",
        kind="class",
        file_path="core/llm/ollama_client.py",
        line_start=1,
        line_end=100,
    )
    c = Classification(kind="llm_endpoint", attributes={"provider": "ollama"})
    node = _node_from_class(symbol, c)
    assert node.kind == "llm_endpoint"
    assert node.attributes["provider"] == "ollama"


def test_node_from_class_deterministic_id() -> None:
    """Same symbol + classification → same ID."""
    symbol = TSASymbol(
        name="A", kind="class", file_path="f.py", line_start=1, line_end=2,
    )
    c = Classification(kind="agent")
    assert _node_from_class(symbol, c).id == _node_from_class(symbol, c).id


def test_node_from_class_different_files_different_ids() -> None:
    """Same class name in different files → different IDs."""
    s1 = TSASymbol(name="A", kind="class", file_path="a.py", line_start=1, line_end=2)
    s2 = TSASymbol(name="A", kind="class", file_path="b.py", line_start=1, line_end=2)
    c = Classification(kind="agent")
    assert _node_from_class(s1, c).id != _node_from_class(s2, c).id


def test_node_from_method_tool() -> None:
    symbol = TSASymbol(
        name="get_file",
        kind="method",
        file_path="mcp/servers/findrive/server.py",
        line_start=10,
        line_end=15,
        class_name="FinDriveServer",
    )
    c = Classification(kind="tool", attributes={"parent_class": "FinDriveServer"})
    node = _node_from_method(symbol, c)
    assert node.kind == "tool"
    assert node.name == "get_file"
    assert node.attributes["parent_class"] == "FinDriveServer"


# ============================================================
# normalize() — end to end
# ============================================================

def test_normalize_empty(manifest_with_findrive: FileManifest) -> None:
    """Empty TSA output still produces MCP server nodes."""
    tsa = TSAOutput(symbols=[])
    delta = normalize(manifest_with_findrive, tsa)
    # Two files in mcp/servers/findrive/ → one mcp_server node
    assert len(delta.nodes) == 1
    assert delta.nodes[0].kind == "mcp_server"
    assert delta.nodes[0].name == "findrive"
    assert delta.edges == []


def test_normalize_mcp_server_dedup(
    manifest_with_findrive: FileManifest,
) -> None:
    """Two files in same server dir → one node."""
    tsa = TSAOutput(symbols=[])
    delta = normalize(manifest_with_findrive, tsa)
    mcp_nodes = [n for n in delta.nodes if n.kind == "mcp_server"]
    assert len(mcp_nodes) == 1


def test_normalize_agent_node(tsa_with_agent: TSAOutput, target: Target) -> None:
    """A single InvoiceAgent class → one agent node."""
    empty_manifest = FileManifest(target=target)
    delta = normalize(empty_manifest, tsa_with_agent)
    assert len(delta.nodes) == 1
    assert delta.nodes[0].kind == "agent"
    assert delta.nodes[0].name == "InvoiceAgent"


def test_normalize_provides_tool_edge(target: Target) -> None:
    """Tool in an mcp server file → provides_tool edge from server."""
    manifest = FileManifest(target=target)
    manifest.files = [
        FileEntry(
            path="mcp/servers/findrive/server.py",
            abs_path=target.root / "mcp/servers/findrive/server.py",
            language="python",
            content_hash="aaa",
            size=100,
        ),
    ]
    tsa = TSAOutput(symbols=[
        TSASymbol(
            name="get_file",
            kind="method",
            file_path="mcp/servers/findrive/server.py",
            line_start=10,
            line_end=15,
            class_name="FinDriveServer",
        ),
    ])
    delta = normalize(manifest, tsa)

    # Expect: 1 mcp_server node, 1 tool node, 1 provides_tool edge
    assert len([n for n in delta.nodes if n.kind == "mcp_server"]) == 1
    assert len([n for n in delta.nodes if n.kind == "tool"]) == 1
    assert len(delta.edges) == 1

    edge = delta.edges[0]
    assert edge.kind == "provides_tool"
    mcp_node = next(n for n in delta.nodes if n.kind == "mcp_server")
    tool_node = next(n for n in delta.nodes if n.kind == "tool")
    assert edge.source_id == mcp_node.id
    assert edge.target_id == tool_node.id


def test_normalize_has_access_to_edge(target: Target) -> None:
    """Tool and sink in the same file → has_access_to edge."""
    manifest = FileManifest(target=target)
    manifest.files = [
        FileEntry(
            path="mcp/servers/systemutils/server.py",
            abs_path=target.root / "mcp/servers/systemutils/server.py",
            language="python",
            content_hash="ccc",
            size=100,
        ),
    ]
    tsa = TSAOutput(symbols=[
        TSASymbol(
            name="run_diagnostics",
            kind="method",
            file_path="mcp/servers/systemutils/server.py",
            line_start=10,
            line_end=15,
            class_name="SystemUtilsServer",
        ),
        TSASymbol(
            name="execute_script",
            kind="method",
            file_path="mcp/servers/systemutils/server.py",
            line_start=20,
            line_end=30,
            class_name="SystemUtilsServer",
        ),
    ])
    delta = normalize(manifest, tsa)

    # Expect: 1 mcp_server, 1 tool, 1 sink, 1 provides_tool edge,
    # 1 has_access_to edge
    tool_node = next(n for n in delta.nodes if n.kind == "tool")
    sink_node = next(n for n in delta.nodes if n.kind == "sink")

    has_access = [e for e in delta.edges if e.kind == "has_access_to"]
    assert len(has_access) == 1
    assert has_access[0].source_id == tool_node.id
    assert has_access[0].target_id == sink_node.id


def test_normalize_deterministic(target: Target) -> None:
    """Same input → same delta (IDs are deterministic)."""
    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[
        TSASymbol(
            name="InvoiceAgent", kind="class",
            file_path="agents/invoice.py", line_start=1, line_end=50,
        ),
    ])
    d1 = normalize(manifest, tsa)
    d2 = normalize(manifest, tsa)

    ids1 = sorted(n.id for n in d1.nodes)
    ids2 = sorted(n.id for n in d2.nodes)
    assert ids1 == ids2


def test_normalize_entry_point_node(target: Target) -> None:
    """Entry point produces a node with method + path in name."""
    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="POST",
                path="/api/chat",
                handler="chat_endpoint",
                file_path="apps/api.py",
                line=42,
            ),
        ],
    )
    delta = normalize(manifest, tsa, ep)
    eps = [n for n in delta.nodes if n.kind == "entry_point"]
    assert len(eps) == 1
    assert eps[0].name == "POST /api/chat"
    assert eps[0].attributes["handler"] == "chat_endpoint"


def test_normalize_entry_to_agent_edge(target: Target) -> None:
    """Entry point handler calling an agent produces delegates_to edge."""
    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[
        TSASymbol(
            name="InvoiceAgent", kind="class",
            file_path="agents/invoice.py", line_start=1, line_end=50,
        ),
    ])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="POST", path="/api/chat",
                handler="chat_endpoint", file_path="apps/api.py", line=42,
            ),
        ],
        agent_calls=[
            AgentCall(
                handler="chat_endpoint",
                agent_name="InvoiceAgent",
                file_path="apps/api.py",
                line=45,
            ),
        ],
    )
    delta = normalize(manifest, tsa, ep)

    entry_nodes = [n for n in delta.nodes if n.kind == "entry_point"]
    agent_nodes = [n for n in delta.nodes if n.kind == "agent"]
    assert len(entry_nodes) == 1
    assert len(agent_nodes) == 1

    delegates = [e for e in delta.edges if e.kind == "delegates_to"]
    assert len(delegates) == 1
    assert delegates[0].source_id == entry_nodes[0].id
    assert delegates[0].target_id == agent_nodes[0].id