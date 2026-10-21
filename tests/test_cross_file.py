"""Unit tests for cross-file edge resolution (uses_tool)."""

from __future__ import annotations

from pathlib import Path

import pytest
import hashlib

from aicorellator.graph.models import Node
from aicorellator.ingestion.discovery import FileEntry, FileManifest
from aicorellator.ingestion.target import Target
from aicorellator.parsing.framework_detectors.cross_file import (
    _collect_imports,
    _module_matches_file,
    resolve_agent_tool_edges,
)


# ============================================================
# _module_matches_file
# ============================================================

def test_module_matches_file_exact() -> None:
    assert _module_matches_file("tools", "tools.py")


def test_module_matches_file_nested() -> None:
    assert _module_matches_file("mcp.servers.foo", "mcp/servers/foo.py")


def test_module_matches_file_with_package_root() -> None:
    assert _module_matches_file("tools", "pkg/tools.py")
    assert _module_matches_file("mcp.servers.foo", "pkg/mcp/servers/foo.py")


def test_module_does_not_match_submodule() -> None:
    # "tools" should not match "tools/sub.py"
    assert not _module_matches_file("tools", "tools/sub.py")


def test_module_does_not_match_unrelated() -> None:
    assert not _module_matches_file("other", "tools.py")
    assert not _module_matches_file("", "tools.py")


# ============================================================
# _collect_imports
# ============================================================

def _write(tmp_path: Path, name: str, code: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return path


def _manifest(tmp_path: Path, files: dict[str, str]) -> FileManifest:
    target = Target(root=tmp_path, kind="folder")
    manifest = FileManifest(target=target)
    for name, code in files.items():
        path = _write(tmp_path, name, code)
        manifest.files.append(FileEntry(
            path=name,
            abs_path=path,
            language="python",
            content_hash="x",
            size=len(code),
        ))
    return manifest


def test_collect_imports_simple(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, {
        "agent.py": "from tools import USER_TOOLS\nimport os\n",
    })
    imports = _collect_imports(manifest)
    assert imports["agent.py"] == ["tools", "os"]


def test_collect_imports_dedup(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, {
        "agent.py": "from tools import A\nfrom tools import B\n",
    })
    imports = _collect_imports(manifest)
    assert imports["agent.py"] == ["tools"]


def test_collect_imports_skips_relative(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, {
        "agent.py": "from .tools import X\nfrom . import y\n",
    })
    imports = _collect_imports(manifest)
    assert "agent.py" not in imports


def test_collect_imports_syntax_error(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, {
        "broken.py": "this is ( not valid",
    })
    imports = _collect_imports(manifest)
    assert imports == {}


# ============================================================
# resolve_agent_tool_edges — end to end
# ============================================================

def _detector_id(kind: str, name: str, source_file: str) -> str:
    """Replicate LangChainDetector._make_node ID derivation.

    The real detector hashes (framework, kind, file_path, name, host)
    so that two nodes with the same name in different files get
    different IDs. Tests must mirror that, or edge deduplication
    collapses distinct agents into one.
    """
    h = hashlib.sha256(
        f"framework:langchain:{kind}:{source_file}:{name}:localhost".encode("utf-8")
    ).hexdigest()
    return f"{kind}:{h}"


def _agent_node(name: str, source_file: str) -> Node:
    return Node(
        id=_detector_id("agent", name, source_file),
        kind="agent",
        name=name,
        host_id="localhost",
        attributes={"source_file": source_file, "framework": "langchain"},
    )


def _tool_node(name: str, source_file: str) -> Node:
    return Node(
        id=_detector_id("tool", name, source_file),
        kind="tool",
        name=name,
        host_id="localhost",
        attributes={"source_file": source_file, "framework": "langchain"},
    )

def test_resolve_edges_nmb_pattern(tmp_path: Path) -> None:
    """Agent in agent.py imports tools from tools.py → uses_tool edges."""
    manifest = _manifest(tmp_path, {
        "agent.py": "from tools import USER_TOOLS\n",
        "tools.py": "@tool\ndef read_ticket(): ...\n",
    })
    nodes = [
        _agent_node("create_agent", "agent.py"),
        _tool_node("read_ticket", "tools.py"),
        _tool_node("search_docs", "tools.py"),
    ]
    edges = resolve_agent_tool_edges(manifest, nodes)
    assert len(edges) == 2
    assert all(e.kind == "uses_tool" for e in edges)
    # ADR-0015: control flow, data flow unconfirmed.
    assert all(e.flow_kind == "control" for e in edges)
    assert all(e.data_flow_confirmed == "unknown" for e in edges)

    # Edge IDs are hashes, so compare by target node name/source_file,
    # not by raw ID.
    target_ids = {e.target_id for e in edges}
    target_nodes = {n.id: n for n in nodes if n.kind == "tool"}
    target_names = {target_nodes[tid].name for tid in target_ids}
    assert target_names == {"read_ticket", "search_docs"}


def test_resolve_edges_no_import_no_edge(tmp_path: Path) -> None:
    """Agent does not import tools.py → no edges."""
    manifest = _manifest(tmp_path, {
        "agent.py": "import unrelated\n",
        "tools.py": "@tool\ndef x(): ...\n",
    })
    nodes = [
        _agent_node("create_agent", "agent.py"),
        _tool_node("x", "tools.py"),
    ]
    edges = resolve_agent_tool_edges(manifest, nodes)
    assert edges == []


def test_resolve_edges_same_file_not_cross_file(tmp_path: Path) -> None:
    """Tools in the agent's own file are not cross-file edges."""
    manifest = _manifest(tmp_path, {
        "agent.py": "@tool\ndef local_tool(): ...\n",
    })
    nodes = [
        _agent_node("create_agent", "agent.py"),
        _tool_node("local_tool", "agent.py"),
    ]
    edges = resolve_agent_tool_edges(manifest, nodes)
    assert edges == []


def test_resolve_edges_no_tools_no_edge(tmp_path: Path) -> None:
    """Agent imports a module that has no tool nodes → no edge."""
    manifest = _manifest(tmp_path, {
        "agent.py": "from utils import helper\n",
        "utils.py": "def helper(): ...\n",
    })
    nodes = [
        _agent_node("create_agent", "agent.py"),
    ]
    edges = resolve_agent_tool_edges(manifest, nodes)
    assert edges == []


def test_resolve_edges_agent_without_framework_skipped(tmp_path: Path) -> None:
    """Agent without framework provenance is not a detector agent."""
    manifest = _manifest(tmp_path, {
        "agent.py": "from tools import X\n",
        "tools.py": "@tool\ndef x(): ...\n",
    })
    agent = Node(
        id="agent:plain", kind="agent", name="PlainAgent",
        host_id="localhost",
        attributes={"source_file": "agent.py"},  # no framework
    )
    nodes = [agent, _tool_node("x", "tools.py")]
    edges = resolve_agent_tool_edges(manifest, nodes)
    assert edges == []


def test_resolve_edges_nested_module_path(tmp_path: Path) -> None:
    """from mcp.servers.foo import X → matches mcp/servers/foo.py."""
    manifest = _manifest(tmp_path, {
        "agent.py": "from mcp.servers.foo import X\n",
        "mcp/servers/foo.py": "@tool\ndef x(): ...\n",
    })
    nodes = [
        _agent_node("create_agent", "agent.py"),
        _tool_node("x", "mcp/servers/foo.py"),
    ]
    edges = resolve_agent_tool_edges(manifest, nodes)
    assert len(edges) == 1
    assert edges[0].kind == "uses_tool"


def test_resolve_edges_deterministic(tmp_path: Path) -> None:
    """Same input → same output (edge IDs are deterministic)."""
    manifest = _manifest(tmp_path, {
        "agent.py": "from tools import X\n",
        "tools.py": "@tool\ndef x(): ...\n",
    })
    nodes = [
        _agent_node("create_agent", "agent.py"),
        _tool_node("x", "tools.py"),
    ]
    e1 = resolve_agent_tool_edges(manifest, nodes)
    e2 = resolve_agent_tool_edges(manifest, nodes)
    assert [e.id for e in e1] == [e.id for e in e2]


def test_resolve_edges_multiple_agents(tmp_path: Path) -> None:
    """Two agents importing the same tools module → each gets edges."""
    manifest = _manifest(tmp_path, {
        "agent_a.py": "from tools import X\n",
        "agent_b.py": "from tools import X\n",
        "tools.py": "@tool\ndef x(): ...\n",
    })
    nodes = [
        _agent_node("create_agent", "agent_a.py"),
        _agent_node("create_agent", "agent_b.py"),
        _tool_node("x", "tools.py"),
    ]
    edges = resolve_agent_tool_edges(manifest, nodes)
    assert len(edges) == 2

    # Two distinct agent nodes (same name, different files) → two
    # distinct source IDs, each getting one edge.
    source_ids = {e.source_id for e in edges}
    assert len(source_ids) == 2
    source_nodes = {n.id: n for n in nodes if n.kind == "agent"}
    source_files = {source_nodes[sid].attributes["source_file"] for sid in source_ids}
    assert source_files == {"agent_a.py", "agent_b.py"}