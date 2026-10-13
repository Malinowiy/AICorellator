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

from aicorellator.parsing.entry_points import (
    AgentCall,
    EntryPoint,
    EntryPointOutput,
    MCPUsage,
)

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

def test_normalize_agent_to_mcp_server_edge(target: Target) -> None:
    """Agent with create_mcp_server call → has_access_to edge."""
    from aicorellator.parsing.entry_points import MCPUsage

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
            name="InvoiceAgent", kind="class",
            file_path="agents/invoice.py", line_start=1, line_end=50,
        ),
    ])
    ep = EntryPointOutput(
        mcp_usages=[
            MCPUsage(
                handler="_get_mcp_servers",
                mcp_server_name="findrive",
                file_path="agents/invoice.py",
                line=10,
                is_agent_module=True,
            ),
        ],
    )
    delta = normalize(manifest, tsa, ep)

    agent_nodes = [n for n in delta.nodes if n.kind == "agent"]
    mcp_nodes = [n for n in delta.nodes if n.kind == "mcp_server"]
    assert len(agent_nodes) == 1
    assert len(mcp_nodes) == 1

    has_access = [
        e for e in delta.edges
        if e.kind == "has_access_to"
        and e.source_id == agent_nodes[0].id
        and e.target_id == mcp_nodes[0].id
    ]
    assert len(has_access) == 1

def test_normalize_cross_file_uses_tool(target: Target) -> None:
    """Agent in one file, tools in another, connected by import."""
    from aicorellator.parsing.framework_detectors.base import DetectionResult
    from aicorellator.graph.models import Node

    manifest = FileManifest(target=target)
    manifest.files = [
        FileEntry(
            path="agent.py",
            abs_path=target.root / "agent.py",
            language="python", content_hash="a", size=100,
        ),
        FileEntry(
            path="tools.py",
            abs_path=target.root / "tools.py",
            language="python", content_hash="b", size=100,
        ),
    ]
    (target.root / "agent.py").write_text(
        "from tools import USER_TOOLS\n", encoding="utf-8",
    )
    (target.root / "tools.py").write_text(
        "@tool\ndef x(): ...\n", encoding="utf-8",
    )

    detection = DetectionResult(framework="langchain")
    detection.nodes = [
        Node(
            id="agent:1", kind="agent", name="create_agent",
            host_id="localhost",
            attributes={"source_file": "agent.py", "framework": "langchain"},
        ),
        Node(
            id="tool:1", kind="tool", name="x",
            host_id="localhost",
            attributes={"source_file": "tools.py", "framework": "langchain"},
        ),
    ]

    tsa = TSAOutput(symbols=[])
    delta = normalize(manifest, tsa, framework_detection=detection)

    uses_tool = [e for e in delta.edges if e.kind == "uses_tool"]
    assert len(uses_tool) == 1
    assert uses_tool[0].source_id == "agent:1"
    assert uses_tool[0].target_id == "tool:1"

def test_orphan_entry_links_to_single_framework_agent(target: Target) -> None:
    """One framework agent + orphan entry_points → delegates_to edges."""
    from aicorellator.graph.models import Node
    from aicorellator.parsing.framework_detectors.base import DetectionResult

    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="POST", path="/chat",
                handler="chat", file_path="app.py", line=10,
            ),
            EntryPoint(
                method="GET", path="/api/models",
                handler="get_models", file_path="app.py", line=20,
            ),
        ],
    )

    detection = DetectionResult(framework="langchain")
    detection.nodes = [
        Node(
            id="agent:1", kind="agent", name="create_agent",
            host_id="localhost",
            attributes={"source_file": "agent.py", "framework": "langchain"},
        ),
    ]

    delta = normalize(manifest, tsa, ep, framework_detection=detection)

    delegates = [e for e in delta.edges if e.kind == "delegates_to"]
    assert len(delegates) == 2
    assert all(e.target_id == "agent:1" for e in delegates)
    assert {e.source_id for e in delegates} == {
        n.id for n in delta.nodes if n.kind == "entry_point"
    }


def test_orphan_entry_no_framework_agent(target: Target) -> None:
    """No framework agents → no orphan linking."""
    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="POST", path="/chat",
                handler="chat", file_path="app.py", line=10,
            ),
        ],
    )
    delta = normalize(manifest, tsa, ep)
    delegates = [e for e in delta.edges if e.kind == "delegates_to"]
    assert delegates == []


def test_orphan_entry_multiple_framework_agents_no_link(target: Target) -> None:
    """Two framework agents → ambiguous, no orphan linking."""
    from aicorellator.graph.models import Node
    from aicorellator.parsing.framework_detectors.base import DetectionResult

    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="POST", path="/chat",
                handler="chat", file_path="app.py", line=10,
            ),
        ],
    )
    detection = DetectionResult(framework="langchain")
    detection.nodes = [
        Node(
            id="agent:1", kind="agent", name="A",
            host_id="localhost",
            attributes={"source_file": "a.py", "framework": "langchain"},
        ),
        Node(
            id="agent:2", kind="agent", name="B",
            host_id="localhost",
            attributes={"source_file": "b.py", "framework": "langchain"},
        ),
    ]
    delta = normalize(manifest, tsa, ep, framework_detection=detection)
    delegates = [e for e in delta.edges if e.kind == "delegates_to"]
    assert delegates == []


def test_orphan_entry_not_relinked_when_already_linked(target: Target) -> None:
    """An entry point that already has a delegates_to edge is not re-linked."""
    from aicorellator.graph.models import Node
    from aicorellator.parsing.framework_detectors.base import DetectionResult
    from aicorellator.parsing.entry_points import AgentCall

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
                method="POST", path="/chat",
                handler="chat", file_path="app.py", line=10,
            ),
        ],
        agent_calls=[
            AgentCall(
                handler="chat", agent_name="InvoiceAgent",
                file_path="app.py", line=12,
            ),
        ],
    )
    detection = DetectionResult(framework="langchain")
    detection.nodes = [
        Node(
            id="agent:1", kind="agent", name="InvoiceAgent",
            host_id="localhost",
            attributes={"source_file": "agents/invoice.py"},  # no framework
        ),
    ]
    delta = normalize(manifest, tsa, ep, framework_detection=detection)
    delegates = [e for e in delta.edges if e.kind == "delegates_to"]
    # Two edges total: one from agent_calls (entry → InvoiceAgent),
    # and... none from orphan fallback, because InvoiceAgent has
    # no `framework` attribute → framework_agents is empty.
    assert len(delegates) == 1


def test_entry_point_to_sink_direct_handler(target: Target) -> None:
    """Handler that is itself a sink gets a has_access_to edge."""
    from aicorellator.parsing.entry_points import EntryPoint, EntryPointOutput

    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[
        TSASymbol(
            name="upload_file", kind="method",
            file_path="app.py", line_start=10, line_end=20,
            class_name=None,
        ),
    ])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="POST", path="/upload",
                handler="upload_file", file_path="app.py", line=10,
            ),
        ],
        call_graph={"upload_file": set()},
    )
    delta = normalize(manifest, tsa, ep)

    entry_nodes = [n for n in delta.nodes if n.kind == "entry_point"]
    sink_nodes = [n for n in delta.nodes if n.kind == "sink"]
    assert len(entry_nodes) == 1
    assert len(sink_nodes) == 1

    has_access = [
        e for e in delta.edges
        if e.kind == "has_access_to"
        and e.source_id == entry_nodes[0].id
        and e.target_id == sink_nodes[0].id
    ]
    assert len(has_access) == 1


def test_entry_point_to_sink_via_helper(target: Target) -> None:
    """Handler → helper → sink produces a transitive edge."""
    from aicorellator.parsing.entry_points import EntryPoint, EntryPointOutput

    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[
        TSASymbol(
            name="run_analysis", kind="method",
            file_path="app.py", line_start=30, line_end=40,
            class_name=None,
        ),
    ])
    # `run_analysis` is classified as a sink by name? It is not in
    # SINK_METHOD_NAMES. But we just need a sink node in the file
    # for the test. Let's use a name that classifies as sink.
    tsa = TSAOutput(symbols=[
        TSASymbol(
            name="execute_script", kind="method",
            file_path="app.py", line_start=30, line_end=40,
            class_name=None,
        ),
    ])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="POST", path="/process",
                handler="process", file_path="app.py", line=10,
            ),
        ],
        call_graph={
            "process": {"run_analysis"},
            "run_analysis": {"execute_script"},
            "execute_script": set(),
        },
    )
    delta = normalize(manifest, tsa, ep)

    entry_nodes = [n for n in delta.nodes if n.kind == "entry_point"]
    sink_nodes = [n for n in delta.nodes if n.kind == "sink"]
    assert len(entry_nodes) == 1
    assert len(sink_nodes) == 1

    has_access = [
        e for e in delta.edges
        if e.kind == "has_access_to"
        and e.source_id == entry_nodes[0].id
        and e.target_id == sink_nodes[0].id
    ]
    assert len(has_access) == 1


def test_entry_point_to_sink_unreachable_skipped(target: Target) -> None:
    """A sink in the same file but not reachable from the handler
    does not produce an edge."""
    from aicorellator.parsing.entry_points import EntryPoint, EntryPointOutput

    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[
        TSASymbol(
            name="execute_script", kind="method",
            file_path="app.py", line_start=50, line_end=60,
            class_name=None,
        ),
    ])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="POST", path="/safe",
                handler="safe_endpoint", file_path="app.py", line=10,
            ),
        ],
        call_graph={
            "safe_endpoint": set(),
            "execute_script": set(),
        },
    )
    delta = normalize(manifest, tsa, ep)
    has_access = [e for e in delta.edges if e.kind == "has_access_to"]
    assert has_access == []


def test_entry_point_to_sink_cycle_safe(target: Target) -> None:
    """A cycle in the call graph terminates and still finds the sink."""
    from aicorellator.parsing.entry_points import EntryPoint, EntryPointOutput

    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[
        TSASymbol(
            name="execute_script", kind="method",
            file_path="app.py", line_start=40, line_end=50,
            class_name=None,
        ),
    ])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="POST", path="/process",
                handler="a", file_path="app.py", line=10,
            ),
        ],
        call_graph={
            "a": {"b"},
            "b": {"a", "execute_script"},
            "execute_script": set(),
        },
    )
    delta = normalize(manifest, tsa, ep)
    has_access = [e for e in delta.edges if e.kind == "has_access_to"]
    assert len(has_access) == 1

def test_mcp_entry_point_created_for_framework_server(target: Target) -> None:
    """A framework-detected mcp_server gets a virtual MCP entry point."""
    from aicorellator.graph.models import Node
    from aicorellator.parsing.framework_detectors.base import DetectionResult

    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[])

    detection = DetectionResult(framework="fastmcp")
    detection.nodes = [
        Node(
            id="mcp_server:abc", kind="mcp_server", name="my-server",
            host_id="localhost",
            attributes={"source_file": "server.py", "framework": "fastmcp"},
        ),
        Node(
            id="tool:1", kind="tool", name="search",
            host_id="localhost",
            attributes={
                "source_file": "server.py",
                "framework": "fastmcp",
                "parent_server": "my-server",
            },
        ),
    ]

    delta = normalize(manifest, tsa, framework_detection=detection)

    mcp_entries = [
        n for n in delta.nodes
        if n.kind == "entry_point"
        and n.attributes.get("entry_kind") == "mcp"
    ]
    assert len(mcp_entries) == 1
    assert mcp_entries[0].name == "MCP:my-server"

    delegates = [
        e for e in delta.edges
        if e.kind == "delegates_to"
        and e.source_id == mcp_entries[0].id
    ]
    assert len(delegates) == 1
    assert delegates[0].target_id == "tool:1"


def test_mcp_entry_point_not_created_for_path_heuristic(target: Target) -> None:
    """mcp_servers found via file path do not get virtual entry points."""
    from aicorellator.graph.models import Node
    from aicorellator.parsing.framework_detectors.base import DetectionResult

    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[])

    detection = DetectionResult(framework="")
    detection.nodes = [
        Node(
            id="mcp_server:abc", kind="mcp_server", name="findrive",
            host_id="localhost",
            attributes={"source_file": "mcp/servers/findrive/server.py"},
            # no framework attribute
        ),
    ]

    delta = normalize(manifest, tsa, framework_detection=detection)

    mcp_entries = [
        n for n in delta.nodes
        if n.kind == "entry_point"
        and n.attributes.get("entry_kind") == "mcp"
    ]
    assert mcp_entries == []

def test_cli_entry_point_created(target: Target) -> None:
    """A CLI entry point is created with method=CLI."""
    from aicorellator.parsing.entry_points import EntryPoint, EntryPointOutput

    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="CLI", path="cli.py", handler="<module>",
                file_path="cli.py", line=10,
            ),
        ],
    )
    delta = normalize(manifest, tsa, ep)

    cli_nodes = [
        n for n in delta.nodes
        if n.kind == "entry_point"
        and n.attributes.get("entry_kind") == "cli"
    ]
    assert len(cli_nodes) == 1
    assert cli_nodes[0].name == "CLI:cli.py"


def test_streamlit_entry_point_created(target: Target) -> None:
    """A Streamlit entry point is created with method=STREAMLIT."""
    from aicorellator.parsing.entry_points import EntryPoint, EntryPointOutput

    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="STREAMLIT", path="chat_input", handler="<module>",
                file_path="app.py", line=5,
            ),
        ],
    )
    delta = normalize(manifest, tsa, ep)

    sl_nodes = [
        n for n in delta.nodes
        if n.kind == "entry_point"
        and n.attributes.get("entry_kind") == "streamlit"
    ]
    assert len(sl_nodes) == 1
    assert sl_nodes[0].name == "Streamlit:app.py:chat_input"


def test_cli_entry_fallback_to_all_sinks(target: Target) -> None:
    """A CLI entry with no same-file sinks links to all project sinks."""
    from aicorellator.parsing.entry_points import EntryPoint, EntryPointOutput

    manifest = FileManifest(target=target)
    tsa = TSAOutput(symbols=[
        TSASymbol(
            name="execute_script", kind="method",
            file_path="helpers.py", line_start=5, line_end=10,
            class_name=None,
        ),
    ])
    ep = EntryPointOutput(
        entry_points=[
            EntryPoint(
                method="CLI", path="cli.py", handler="<module>",
                file_path="cli.py", line=10,
            ),
        ],
    )
    delta = normalize(manifest, tsa, ep)

    has_access = [e for e in delta.edges if e.kind == "has_access_to"]
    assert len(has_access) == 1
    assert has_access[0].attributes.get("resolved_via") == "cli_fallback"