"""Unit tests for AST Normalizer.

Tests use hand-built FileManifest and TSAOutput objects — no file
system access, no TSA subprocess. Fast and deterministic.
"""

import ast
from pathlib import Path

import pytest

from aicorellator.graph.models import VALID_FLOW_KINDS, Node
from aicorellator.ingestion.discovery import FileEntry, FileManifest
from aicorellator.ingestion.target import Target
from aicorellator.parsing.ast_normalizer import (
    GraphDelta,
    _edge_id,
    _index_functions,
    _mcp_server_id,
    _node_from_class,
    _node_from_method,
    _resolve_definition,
    _server_name_for_file,
    _sha256,
    normalize,
)
from aicorellator.parsing.capability_extractor import (
    FileCapabilities,
    SymbolCapabilities,
)
from aicorellator.parsing.classification import Classification
from aicorellator.parsing.flow_analyzer import DATA_FLOW_DATA_VALUES
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
    # ADR-0015: the access path is the candidate attack path.
    assert edge.flow_kind == "control"
    assert edge.data_flow_confirmed == "unknown"
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
    # The abs_path does not exist on disk, so the Flow Analyzer cannot
    # confirm anything (ADR-0015): control / unknown, never a crash.
    assert has_access[0].flow_kind == "control"
    assert has_access[0].data_flow_confirmed == "unknown"


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
    # ADR-0015: control flow, data flow unconfirmed.
    assert delegates[0].flow_kind == "control"
    assert delegates[0].data_flow_confirmed == "unknown"

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
    # ADR-0015: reachability, not a call into a sink.
    assert has_access[0].flow_kind == "control"
    assert has_access[0].data_flow_confirmed == "unknown"

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
    # ADR-0015: control flow, data flow unconfirmed.
    assert uses_tool[0].flow_kind == "control"
    assert uses_tool[0].data_flow_confirmed == "unknown"

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


# ============================================================
# ADR-0015 — flow_kind / data_flow_confirmed
# ============================================================

SYSTEMUTILS_PATH = "mcp/servers/systemutils/server.py"

# Two tools in one file, one sink. `read_file` forwards its parameter
# to the sink (data flow); `list_files` passes only a literal (control
# flow). `delete_file` is a sink by name (SINK_METHOD_NAMES).
TWO_TOOLS_ONE_SINK = """\
class SystemUtilsServer:
    def read_file(self, query):
        return self.delete_file(query)

    def list_files(self):
        return self.delete_file("/tmp/scratch")

    def delete_file(self, path):
        return path
"""


def _write_python_file(target: Target, rel_path: str, source: str) -> FileEntry:
    """Write `source` to disk and return the matching FileEntry."""
    abs_path = target.root / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(source, encoding="utf-8")
    return FileEntry(
        path=rel_path,
        abs_path=abs_path,
        language="python",
        content_hash="hash",
        size=len(source),
    )


def _server_manifest(target: Target, source: str | None) -> FileManifest:
    """A one-file mcp/servers/systemutils project.

    `source` is written to disk. When it is None the manifest still
    lists the file, but the abs_path does not exist — the shape the
    pre-existing unit tests have.
    """
    manifest = FileManifest(target=target)
    if source is None:
        manifest.files = [
            FileEntry(
                path=SYSTEMUTILS_PATH,
                abs_path=target.root / SYSTEMUTILS_PATH,
                language="python",
                content_hash="hash",
                size=0,
            ),
        ]
    else:
        manifest.files = [_write_python_file(target, SYSTEMUTILS_PATH, source)]
    return manifest


def _systemutils_tsa(names: list[str]) -> TSAOutput:
    """TSA output with one method symbol per name in SystemUtilsServer."""
    return TSAOutput(symbols=[
        TSASymbol(
            name=name,
            kind="method",
            file_path=SYSTEMUTILS_PATH,
            line_start=1,
            line_end=2,
            class_name="SystemUtilsServer",
        )
        for name in names
    ])


def _has_access_edges(delta: GraphDelta, tool_name: str) -> list:
    """has_access_to edges whose source tool node is named `tool_name`."""
    tool = next(n for n in delta.nodes if n.name == tool_name)
    return [
        e for e in delta.edges
        if e.kind == "has_access_to" and e.source_id == tool.id
    ]


def test_has_access_to_parameter_is_data(target: Target) -> None:
    """Tool forwards its own parameter to the sink → data / parameter_match."""
    manifest = _server_manifest(target, TWO_TOOLS_ONE_SINK)
    tsa = _systemutils_tsa(["read_file", "list_files", "delete_file"])
    delta = normalize(manifest, tsa)

    edges = _has_access_edges(delta, "read_file")
    assert len(edges) == 1
    assert edges[0].flow_kind == "data"
    assert edges[0].data_flow_confirmed == "parameter_match"


def test_has_access_to_literal_is_control(target: Target) -> None:
    """Sink called with a literal only → control / literal_only."""
    manifest = _server_manifest(target, TWO_TOOLS_ONE_SINK)
    tsa = _systemutils_tsa(["read_file", "list_files", "delete_file"])
    delta = normalize(manifest, tsa)

    edges = _has_access_edges(delta, "list_files")
    assert len(edges) == 1
    assert edges[0].flow_kind == "control"
    assert edges[0].data_flow_confirmed == "literal_only"


def test_two_has_access_to_edges_differ_in_flow_kind(target: Target) -> None:
    """One file, one sink, two tools → one data edge and one control edge."""
    manifest = _server_manifest(target, TWO_TOOLS_ONE_SINK)
    tsa = _systemutils_tsa(["read_file", "list_files", "delete_file"])
    delta = normalize(manifest, tsa)

    has_access = [e for e in delta.edges if e.kind == "has_access_to"]
    assert len(has_access) == 2
    assert {e.flow_kind for e in has_access} == {"data", "control"}
    assert {e.data_flow_confirmed for e in has_access} == {
        "parameter_match", "literal_only",
    }


def test_has_access_to_tool_never_calls_sink_is_unknown(target: Target) -> None:
    """Tool body does not call the sink → control / unknown."""
    source = """\
class SystemUtilsServer:
    def read_file(self, query):
        return query

    def delete_file(self, path):
        return path
"""
    manifest = _server_manifest(target, source)
    tsa = _systemutils_tsa(["read_file", "delete_file"])
    delta = normalize(manifest, tsa)

    edges = _has_access_edges(delta, "read_file")
    assert len(edges) == 1
    assert edges[0].flow_kind == "control"
    assert edges[0].data_flow_confirmed == "unknown"


def test_has_access_to_mixed_args_is_unknown(target: Target) -> None:
    """A literal plus an unresolved attribute → unknown, not literal_only."""
    source = """\
class SystemUtilsServer:
    def process_file(self, config):
        return self.delete_file(config.path, "/tmp/scratch")

    def delete_file(self, path, extra=None):
        return path
"""
    manifest = _server_manifest(target, source)
    tsa = _systemutils_tsa(["process_file", "delete_file"])
    delta = normalize(manifest, tsa)

    edges = _has_access_edges(delta, "process_file")
    assert len(edges) == 1
    assert edges[0].flow_kind == "control"
    assert edges[0].data_flow_confirmed == "unknown"



def test_has_access_to_missing_file_is_unknown(target: Target) -> None:
    """abs_path does not exist on disk → control / unknown, no crash."""
    manifest = _server_manifest(target, None)
    tsa = _systemutils_tsa(["read_file", "delete_file"])
    delta = normalize(manifest, tsa)

    edges = _has_access_edges(delta, "read_file")
    assert len(edges) == 1
    assert edges[0].flow_kind == "control"
    assert edges[0].data_flow_confirmed == "unknown"


def test_has_access_to_syntax_error_is_unknown(target: Target) -> None:
    """Unparseable file → control / unknown, no crash."""
    manifest = _server_manifest(target, "def broken(:\n")
    tsa = _systemutils_tsa(["read_file", "delete_file"])
    delta = normalize(manifest, tsa)

    edges = _has_access_edges(delta, "read_file")
    assert len(edges) == 1
    assert edges[0].flow_kind == "control"
    assert edges[0].data_flow_confirmed == "unknown"


def test_source_file_parsed_at_most_once_per_scan(
    target: Target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two tools, one sink, one file → exactly one ast.parse call."""
    manifest = _server_manifest(target, TWO_TOOLS_ONE_SINK)
    tsa = _systemutils_tsa(["read_file", "list_files", "delete_file"])

    calls: list[str] = []
    real_parse = ast.parse

    def counting_parse(source, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(str(kwargs.get("filename", "")))
        return real_parse(source, *args, **kwargs)

    monkeypatch.setattr(ast, "parse", counting_parse)
    delta = normalize(manifest, tsa)

    assert len([e for e in delta.edges if e.kind == "has_access_to"]) == 2
    assert len(calls) == 1


def test_every_edge_carries_flow_kind(target: Target) -> None:
    """Normalizing leaves no edge without a valid flow_kind (ADR-0015)."""
    manifest = _server_manifest(target, TWO_TOOLS_ONE_SINK)
    tsa = _systemutils_tsa(["read_file", "list_files", "delete_file"])
    delta = normalize(manifest, tsa)

    assert delta.edges
    for edge in delta.edges:
        assert edge.flow_kind in VALID_FLOW_KINDS, edge
        if edge.flow_kind == "structural":
            # Declarations carry no data-flow verdict.
            assert edge.data_flow_confirmed is None, edge
        else:
            # Control and data edges always record a verdict.
            assert edge.data_flow_confirmed is not None, edge

    data_edges = [e for e in delta.edges if e.flow_kind == "data"]
    assert data_edges
    assert all(
        e.data_flow_confirmed in DATA_FLOW_DATA_VALUES for e in data_edges
    )

    # `provides_tool` is control (ADR-0015): the access path to a tool
    # is the candidate attack path.
    provides = [e for e in delta.edges if e.kind == "provides_tool"]
    assert provides
    assert all(e.flow_kind == "control" for e in provides)
    assert all(e.data_flow_confirmed == "unknown" for e in provides)

    # No resolver emits a structural edge yet (`guards`). When one does,
    # it must carry no data-flow verdict.
    assert [e for e in delta.edges if e.flow_kind == "structural"] == []


def test_provides_tool_is_control(target: Target) -> None:
    """A parseable file does not change provides_tool's flow_kind."""
    manifest = _server_manifest(target, TWO_TOOLS_ONE_SINK)
    tsa = _systemutils_tsa(["read_file", "list_files", "delete_file"])
    delta = normalize(manifest, tsa)

    provides = [e for e in delta.edges if e.kind == "provides_tool"]
    assert len(provides) == 2
    assert all(e.flow_kind == "control" for e in provides)
    assert all(e.data_flow_confirmed == "unknown" for e in provides)


# ============================================================
# 1.2a.1 — sink_data_flow / returns_data node attributes
# ============================================================

# The tool returns data to its caller; the sink forwards its own
# parameter to a dangerous call.
SINK_FORWARDS_PARAMETER = """\
class SystemUtilsServer:
    def read_file(self, query):
        return self.search(query)

    def delete_file(self, path):
        os.remove(path)
"""

# Same shape, but the dangerous call receives a constant only and the
# tool hands nothing back to its caller.
SINK_FORWARDS_LITERAL = """\
class SystemUtilsServer:
    def read_file(self, query):
        self.log(query)

    def delete_file(self):
        os.remove("/tmp/scratch")
"""

# The dangerous call receives an attribute of the sink's own parameter —
# FinBot `create_vendor_file`'s shape, `unknown` before 1.2a.3j.
SINK_FORWARDS_PARAMETER_ATTRIBUTE = """\
class SystemUtilsServer:
    def read_file(self, query):
        return self.search(query)

    def delete_file(self, file_data):
        os.remove(file_data.filename)
"""

# No `delete_file` function in the file: the sink node is heuristic
# (name-only), so its body cannot be analyzed.
SINK_WITHOUT_BODY = """\
class SystemUtilsServer:
    def read_file(self, query):
        return query
"""


def _node(delta: GraphDelta, name: str) -> Node:
    """The single node with `name`."""
    matches = [n for n in delta.nodes if n.name == name]
    assert len(matches) == 1, name
    return matches[0]


def test_sink_node_records_parameter_match(target: Target) -> None:
    """A sink that forwards its parameter to `os.remove` (1.2a.1)."""
    manifest = _server_manifest(target, SINK_FORWARDS_PARAMETER)
    tsa = _systemutils_tsa(["read_file", "delete_file"])
    delta = normalize(manifest, tsa)

    sink = _node(delta, "delete_file")
    assert sink.kind == "sink"
    assert sink.attributes["sink_data_flow"] == "parameter_match"


def test_sink_node_records_parameter_attribute(target: Target) -> None:
    """`os.remove(file_data.filename)` reaches the node (1.2a.3j).

    An attribute of the sink's own parameter is parameter-derived, so the
    node records `parameter_match` instead of the `unknown` the
    attribute spelling produced before 1.2a.3j — the FinBot
    `create_vendor_file` false negative, at node level.
    """
    manifest = _server_manifest(target, SINK_FORWARDS_PARAMETER_ATTRIBUTE)
    tsa = _systemutils_tsa(["read_file", "delete_file"])
    delta = normalize(manifest, tsa)

    sink = _node(delta, "delete_file")
    assert sink.kind == "sink"
    assert sink.attributes["sink_data_flow"] == "parameter_match"


def test_tool_node_records_returns_data(target: Target) -> None:
    """A tool whose body returns a value to its caller (1.2a.1)."""
    manifest = _server_manifest(target, SINK_FORWARDS_PARAMETER)
    tsa = _systemutils_tsa(["read_file", "delete_file"])
    delta = normalize(manifest, tsa)

    tool = _node(delta, "read_file")
    assert tool.kind == "tool"
    assert tool.attributes["returns_data"] is True


def test_tool_node_without_return_records_false(target: Target) -> None:
    """A tool that never returns a value is not a data source."""
    manifest = _server_manifest(target, SINK_FORWARDS_LITERAL)
    tsa = _systemutils_tsa(["read_file", "delete_file"])
    delta = normalize(manifest, tsa)

    assert _node(delta, "read_file").attributes["returns_data"] is False


def test_sink_node_records_literal_only(target: Target) -> None:
    """A sink whose dangerous call takes a constant only (1.2a.1)."""
    manifest = _server_manifest(target, SINK_FORWARDS_LITERAL)
    tsa = _systemutils_tsa(["read_file", "delete_file"])
    delta = normalize(manifest, tsa)

    assert _node(delta, "delete_file").attributes["sink_data_flow"] == "literal_only"


def test_sink_node_without_body_is_unknown(target: Target) -> None:
    """A heuristic sink node with no function of that name (1.2a.1)."""
    manifest = _server_manifest(target, SINK_WITHOUT_BODY)
    tsa = _systemutils_tsa(["read_file", "delete_file"])
    delta = normalize(manifest, tsa)

    assert _node(delta, "delete_file").attributes["sink_data_flow"] == "unknown"


def test_sink_node_missing_file_is_unknown(target: Target) -> None:
    """abs_path does not exist on disk → unknown, no crash."""
    manifest = _server_manifest(target, None)
    tsa = _systemutils_tsa(["read_file", "delete_file"])
    delta = normalize(manifest, tsa)

    assert _node(delta, "delete_file").attributes["sink_data_flow"] == "unknown"
    assert _node(delta, "read_file").attributes["returns_data"] is False


def test_only_tools_and_sinks_carry_flow_attributes(target: Target) -> None:
    """The attributes are node-kind specific (1.2a.1)."""
    manifest = _server_manifest(target, SINK_FORWARDS_PARAMETER)
    tsa = TSAOutput(symbols=[
        TSASymbol(
            name="SystemUtilsServer", kind="class",
            file_path=SYSTEMUTILS_PATH, line_start=1, line_end=2,
        ),
        *_systemutils_tsa(["read_file", "delete_file"]).symbols,
    ])
    delta = normalize(manifest, tsa)

    for node in delta.nodes:
        if node.kind == "tool":
            assert "returns_data" in node.attributes
            assert "sink_data_flow" not in node.attributes
        elif node.kind == "sink":
            assert "sink_data_flow" in node.attributes
            assert "returns_data" not in node.attributes
        else:
            assert "returns_data" not in node.attributes
            assert "sink_data_flow" not in node.attributes


def test_body_attributes_do_not_change_flow_kind(target: Target) -> None:
    """The node signals are prompt data, not chain filters (1.2a.1).

    The tool does not call the sink by name, so the `has_access_to`
    edge stays `control` / `unknown` even though the sink forwards a
    parameter to a dangerous call.
    """
    manifest = _server_manifest(target, SINK_FORWARDS_PARAMETER)
    tsa = _systemutils_tsa(["read_file", "delete_file"])
    delta = normalize(manifest, tsa)

    sink = _node(delta, "delete_file")
    assert sink.attributes["sink_data_flow"] == "parameter_match"

    edges = _has_access_edges(delta, "read_file")
    assert len(edges) == 1
    assert edges[0].flow_kind == "control"
    assert edges[0].data_flow_confirmed == "unknown"


def test_body_attributes_survive_detector_node_replacement(
    target: Target,
) -> None:
    """A detector node that replaces a TSA tool node still gets the
    attribute (the backfill in `normalize`)."""
    from aicorellator.parsing.framework_detectors import DetectionResult

    manifest = _server_manifest(target, SINK_FORWARDS_PARAMETER)
    tsa = _systemutils_tsa(["read_file", "delete_file"])

    detector_tool = Node(
        id="tool:detector",
        kind="tool",
        name="read_file",
        host_id="localhost",
        attributes={"source_file": SYSTEMUTILS_PATH, "framework": "fastmcp"},
    )
    detection = DetectionResult(nodes=[detector_tool], framework="fastmcp")

    delta = normalize(manifest, tsa, framework_detection=detection)

    tool = _node(delta, "read_file")
    assert tool.id == "tool:detector"
    assert tool.attributes["returns_data"] is True


# ============================================================
# Class-kind sinks (1.2a.5)
# ============================================================

CLASS_SINK_PATH = "finbot/core/data/repositories.py"

CLASS_SINK_FORWARDS_PARAMETER = """
class FileRepository:
    def __init__(self, p):
        self.db.delete(p)
"""

CLASS_SINK_DELETES_LITERAL = """
class FileRepository:
    def __init__(self):
        self.db.delete("scratch")
"""

CLASS_SINK_METHOD_ONLY = """
class FileRepository:
    def delete(self, p):
        return self.db.delete(p)
"""

CLASS_SINK_NO_DANGEROUS_CALL = """
class FileRepository:
    def delete(self, p):
        return p
"""

CLASS_SINK_STORED_ATTRIBUTE = """
class FileRepository:
    def __init__(self, q):
        self.q = q
        self.db.delete(self.q)
"""


def _class_sink_manifest(target: Target, source: str) -> FileManifest:
    """A one-file project whose only file defines the repository class."""
    manifest = FileManifest(target=target)
    manifest.files = [_write_python_file(target, CLASS_SINK_PATH, source)]
    return manifest


def _class_sink_tsa() -> TSAOutput:
    """TSA output with one *class* symbol — the capability-based sink."""
    return TSAOutput(symbols=[
        TSASymbol(
            name="FileRepository",
            kind="class",
            file_path=CLASS_SINK_PATH,
            line_start=1,
            line_end=20,
        ),
    ])


def _class_sink_caps() -> dict[str, FileCapabilities]:
    """Capabilities crediting `FileRepository` with `writes_db`.

    `classify_class` turns a sink capability into `kind = "sink"` with no
    name heuristic involved — the same route that makes FinBot's
    `UserProfileRepository` and dvmcp's `DVMCPClient` sink nodes.
    """
    return {
        CLASS_SINK_PATH: FileCapabilities(
            file_path=CLASS_SINK_PATH,
            symbols=[
                SymbolCapabilities(
                    symbol_name="FileRepository",
                    file_path=CLASS_SINK_PATH,
                    capabilities={"writes_db"},
                ),
            ],
        ),
    }


def test_class_sink_node_records_parameter_match(target: Target) -> None:
    """A class-kind sink resolves through the class index (1.2a.5).

    The node name is not a function, so the function index misses it and
    `analyze_sink_class` reads the class's methods instead.
    """
    manifest = _class_sink_manifest(target, CLASS_SINK_FORWARDS_PARAMETER)
    delta = normalize(
        manifest, _class_sink_tsa(), capabilities=_class_sink_caps(),
    )

    sink = _node(delta, "FileRepository")
    assert sink.kind == "sink"
    assert sink.attributes["sink_data_flow"] == "parameter_match"


def test_class_sink_node_records_literal_only(target: Target) -> None:
    """A class-kind sink whose `__init__` passes a constant only."""
    manifest = _class_sink_manifest(target, CLASS_SINK_DELETES_LITERAL)
    delta = normalize(
        manifest, _class_sink_tsa(), capabilities=_class_sink_caps(),
    )

    sink = _node(delta, "FileRepository")
    assert sink.attributes["sink_data_flow"] == "literal_only"


def test_class_sink_node_with_method_only_is_parameter_match(
    target: Target,
) -> None:
    """A class with no `__init__` still resolves through the class index.

    `analyze_sink_class` reads every method (1.2a.5 rework), so the
    ordinary `delete(self, p)` method — the entry point a caller drives
    — is what makes the node `parameter_match`.
    """
    manifest = _class_sink_manifest(target, CLASS_SINK_METHOD_ONLY)
    delta = normalize(
        manifest, _class_sink_tsa(), capabilities=_class_sink_caps(),
    )

    sink = _node(delta, "FileRepository")
    assert sink.attributes["sink_data_flow"] == "parameter_match"


def test_class_sink_node_without_dangerous_call_is_unknown(
    target: Target,
) -> None:
    """Methods that call nothing dangerous → unknown, and no crash."""
    manifest = _class_sink_manifest(target, CLASS_SINK_NO_DANGEROUS_CALL)
    delta = normalize(
        manifest, _class_sink_tsa(), capabilities=_class_sink_caps(),
    )

    sink = _node(delta, "FileRepository")
    assert sink.attributes["sink_data_flow"] == "unknown"


def test_class_sink_node_with_stored_attribute_is_parameter_match(
    target: Target,
) -> None:
    """`self.q = q; self.db.delete(self.q)` reaches the node (1.2a.3g).

    The attribute is parameter-derived, so the class-kind sink records
    `parameter_match` instead of the `unknown` the stored-attribute
    spelling produced before 1.2a.3g.
    """
    manifest = _class_sink_manifest(target, CLASS_SINK_STORED_ATTRIBUTE)
    delta = normalize(
        manifest, _class_sink_tsa(), capabilities=_class_sink_caps(),
    )

    sink = _node(delta, "FileRepository")
    assert sink.attributes["sink_data_flow"] == "parameter_match"


def test_class_sink_detector_node_resolves_the_class_body(
    target: Target,
) -> None:
    """A detector-created sink node gets the class body analyzed too.

    The backfill in `normalize` resolves bodies through the same
    `_body_attributes`, so the class fallback is not TSA-only (1.2a.5).
    """
    from aicorellator.parsing.framework_detectors import DetectionResult

    manifest = _class_sink_manifest(target, CLASS_SINK_FORWARDS_PARAMETER)
    detector_sink = Node(
        id="sink:detector",
        kind="sink",
        name="FileRepository",
        host_id="localhost",
        attributes={"source_file": CLASS_SINK_PATH, "framework": "fastmcp"},
    )
    detection = DetectionResult(nodes=[detector_sink], framework="fastmcp")

    delta = normalize(manifest, TSAOutput(), framework_detection=detection)

    sink = _node(delta, "FileRepository")
    assert sink.id == "sink:detector"
    assert sink.attributes["sink_data_flow"] == "parameter_match"


# ============================================================
# 1.2a.3f — function resolution by (name, line_start)
# ============================================================

# Two classes define `delete_file`: the first forwards its parameter to
# the dangerous call, the second passes a constant. Keying the function
# index by name alone made the second node read the first body — the
# shape of FinBot's `toggle_enabled` at repositories.py:664 / :1426.
DUPLICATE_SINK_METHODS = """\
class FirstRepository:
    def delete_file(self, path):
        os.remove(path)


class SecondRepository:
    def delete_file(self):
        os.remove("/tmp/scratch")
"""

# Three definitions of one name, so the middle one is identifiable by
# its value: literal, parameter, literal. A node whose line_start lands
# in the gap before the third definition must resolve to the second.
THREE_SAME_NAMED_SINKS = """\
class FirstRepository:
    def delete_file(self, path):
        os.remove("/tmp/first")


class SecondRepository:
    def delete_file(self, path):
        os.remove(path)


class ThirdRepository:
    def delete_file(self, path):
        os.remove("/tmp/third")
"""

# Two tools of one name; only the first hands data back to its caller.
DUPLICATE_TOOL_NAMES = """\
class FirstServer:
    def read_file(self, path):
        return open(path).read()


class SecondServer:
    def read_file(self, path):
        self.log(path)
"""


def _tsa_from_source(source: str, file_path: str) -> TSAOutput:
    """TSA output for a one-file module, with the **real** `line_start`
    of every method and the class that encloses it.

    What TSA reports, and what 1.2a.3f resolves against. The other
    helpers in this file use placeholder line numbers (`line_start=1`),
    which is the approximate path, not this one.
    """
    symbols: list[TSASymbol] = []
    for cls in ast.parse(source).body:
        if not isinstance(cls, ast.ClassDef):
            continue
        for statement in cls.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(TSASymbol(
                    name=statement.name,
                    kind="method",
                    file_path=file_path,
                    line_start=statement.lineno,
                    line_end=statement.end_lineno or statement.lineno,
                    class_name=cls.name,
                ))
    return TSAOutput(symbols=symbols)


def _definition_lines(source: str, name: str) -> list[int]:
    """Line numbers of every `def name` in `source`, in source order."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]


def _node_at_line(delta: GraphDelta, name: str, line_start: int) -> Node:
    """The node named `name` whose `line_start` is `line_start`.

    A name defined twice yields two nodes, so `_node` cannot be used.
    """
    matches = [
        n for n in delta.nodes
        if n.name == name and n.attributes.get("line_start") == line_start
    ]
    assert len(matches) == 1, (name, line_start)
    return matches[0]


def test_duplicate_method_names_resolve_by_line_start(target: Target) -> None:
    """Each node reads the definition that contains its `line_start`.

    Before 1.2a.3f both nodes read the first `delete_file` body, so the
    second recorded `parameter_match` for a body that passes a constant.
    """
    manifest = _server_manifest(target, DUPLICATE_SINK_METHODS)
    tsa = _tsa_from_source(DUPLICATE_SINK_METHODS, SYSTEMUTILS_PATH)
    delta = normalize(manifest, tsa)

    lines = _definition_lines(DUPLICATE_SINK_METHODS, "delete_file")
    assert len(lines) == 2

    first = _node_at_line(delta, "delete_file", lines[0])
    second = _node_at_line(delta, "delete_file", lines[1])
    assert first.attributes["sink_data_flow"] == "parameter_match"
    assert second.attributes["sink_data_flow"] == "literal_only"


def test_duplicate_method_names_without_line_start_use_the_first(
    target: Target,
) -> None:
    """A node with no `line_start` keeps the name-keyed behaviour.

    Detector-created nodes carry no `line_start` (`langchain.py`,
    `fastmcp.py`, `mcp_python.py` never set it), so the fallback — the
    first definition of that name — must be preserved.
    """
    from aicorellator.parsing.framework_detectors import DetectionResult

    manifest = _server_manifest(target, DUPLICATE_SINK_METHODS)
    detector_sink = Node(
        id="sink:detector",
        kind="sink",
        name="delete_file",
        host_id="localhost",
        attributes={"source_file": SYSTEMUTILS_PATH, "framework": "fastmcp"},
    )
    detection = DetectionResult(nodes=[detector_sink], framework="fastmcp")

    delta = normalize(manifest, TSAOutput(), framework_detection=detection)

    sink = _node(delta, "delete_file")
    assert "line_start" not in sink.attributes
    # The first `delete_file` forwards its parameter.
    assert sink.attributes["sink_data_flow"] == "parameter_match"


def test_approximate_line_start_uses_the_closest_preceding_definition(
    target: Target,
) -> None:
    """A line_start in the gap between definitions resolves backwards.

    Three definitions whose bodies are literal / parameter / literal. The
    node's `line_start` sits before the third definition, so the closest
    preceding one (the second) wins: `parameter_match`. The first
    definition — the name-keyed answer — would be `literal_only`, and so
    would the following definition, so the value identifies which
    definition was read.
    """
    manifest = _server_manifest(target, THREE_SAME_NAMED_SINKS)
    lines = _definition_lines(THREE_SAME_NAMED_SINKS, "delete_file")
    assert len(lines) == 3
    approximate = lines[2] - 2
    assert lines[1] < approximate < lines[2]

    tsa = TSAOutput(symbols=[TSASymbol(
        name="delete_file",
        kind="method",
        file_path=SYSTEMUTILS_PATH,
        line_start=approximate,
        line_end=approximate + 1,
        class_name="SecondRepository",
    )])
    delta = normalize(manifest, tsa)

    sink = _node(delta, "delete_file")
    assert sink.attributes["line_start"] == approximate
    assert sink.attributes["sink_data_flow"] == "parameter_match"


def test_duplicate_tool_names_resolve_by_line_start(target: Target) -> None:
    """The tool path resolves the same way (1.2a.3f).

    Only the first `read_file` returns a value; before 1.2a.3f both nodes
    recorded `returns_data = True`.
    """
    manifest = _server_manifest(target, DUPLICATE_TOOL_NAMES)
    tsa = _tsa_from_source(DUPLICATE_TOOL_NAMES, SYSTEMUTILS_PATH)
    delta = normalize(manifest, tsa)

    lines = _definition_lines(DUPLICATE_TOOL_NAMES, "read_file")
    assert len(lines) == 2

    first = _node_at_line(delta, "read_file", lines[0])
    second = _node_at_line(delta, "read_file", lines[1])
    assert first.kind == "tool"
    assert first.attributes["returns_data"] is True
    assert second.attributes["returns_data"] is False


def test_resolve_definition_exact_missing_and_gap() -> None:
    """`_resolve_definition` step by step, on a parsed module."""
    module = ast.parse(DUPLICATE_SINK_METHODS)
    index = _index_functions(module)
    lines = _definition_lines(DUPLICATE_SINK_METHODS, "delete_file")

    # Exact: the definition that starts at that line.
    assert _resolve_definition(index, "delete_file", lines[1]) is index[
        ("delete_file", lines[1])
    ]
    # Contained: a line inside the first body.
    assert _resolve_definition(index, "delete_file", lines[0] + 1) is index[
        ("delete_file", lines[0])
    ]
    # No such name at all → None (the `unknown` / `False` path).
    assert _resolve_definition(index, "no_such_name", 1) is None
    assert _resolve_definition(index, "no_such_name", None) is None
    # No `line_start` → the first definition of the name.
    assert _resolve_definition(index, "delete_file", None) is index[
        ("delete_file", lines[0])
    ]
    # Both definitions are kept: a name is not collapsed (1.2a.3f).
    assert len(index) == 2

