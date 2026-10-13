"""Unit tests for the FastMCP detector."""

from __future__ import annotations

import ast

from aicorellator.parsing.framework_detectors.fastmcp import FastMCPDetector


def _detect(code: str):
    tree = ast.parse(code)
    return FastMCPDetector().detect("test.py", tree, code)


def test_no_fastmcp_import_returns_empty() -> None:
    result = _detect("x = 1\n")
    assert result.nodes == []
    assert result.edges == []


def test_detect_fastmcp_server() -> None:
    result = _detect("""
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("my-server")
""")
    servers = [n for n in result.nodes if n.kind == "mcp_server"]
    assert len(servers) == 1
    assert servers[0].name == "my-server"
    assert servers[0].attributes["framework"] == "fastmcp"
    assert servers[0].attributes["server_variable"] == "mcp"


def test_detect_fastmcp_server_name_keyword() -> None:
    result = _detect("""
from mcp.server.fastmcp import FastMCP
mcp = FastMCP(name="ops-orchestrator", description="x")
""")
    servers = [n for n in result.nodes if n.kind == "mcp_server"]
    assert len(servers) == 1
    assert servers[0].name == "ops-orchestrator"


def test_detect_fastmcp_no_name_falls_back() -> None:
    result = _detect("""
from mcp.server.fastmcp import FastMCP
mcp = FastMCP()
""")
    servers = [n for n in result.nodes if n.kind == "mcp_server"]
    assert len(servers) == 1
    assert servers[0].name == "unnamed_mcp_server"


def test_detect_tool_with_parens() -> None:
    result = _detect("""
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("srv")

@mcp.tool()
def search(query: str) -> str:
    return "ok"
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert len(tools) == 1
    assert tools[0].name == "search"
    assert tools[0].attributes["parent_server"] == "srv"


def test_detect_tool_without_parens() -> None:
    result = _detect("""
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("srv")

@mcp.tool
def search(query: str) -> str:
    return "ok"
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert len(tools) == 1
    assert tools[0].name == "search"


def test_detect_tool_with_description() -> None:
    result = _detect("""
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("srv")

@mcp.tool(description="A search tool")
def search(query: str) -> str:
    return "ok"
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert len(tools) == 1
    assert tools[0].name == "search"


def test_tool_capabilities_extracted() -> None:
    result = _detect("""
import subprocess
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("srv")

@mcp.tool()
def run_cmd(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True).stdout.decode()
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert len(tools) == 1
    caps = tools[0].capabilities
    assert "executes_code" in caps
    assert "executes_shell" in caps


def test_multiple_servers_in_one_file() -> None:
    result = _detect("""
from mcp.server.fastmcp import FastMCP
a = FastMCP("alpha")
b = FastMCP("beta")

@a.tool()
def alpha_tool() -> str:
    return "a"

@b.tool()
def beta_tool() -> str:
    return "b"
""")
    servers = {n.name for n in result.nodes if n.kind == "mcp_server"}
    assert servers == {"alpha", "beta"}
    tools = {n.name: n.attributes["parent_server"] for n in result.nodes if n.kind == "tool"}
    assert tools == {"alpha_tool": "alpha", "beta_tool": "beta"}


def test_unrelated_decorator_ignored() -> None:
    result = _detect("""
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("srv")

@other.tool()
def not_our_tool() -> str:
    return "x"
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert tools == []


def test_provenance_in_attributes() -> None:
    result = _detect("""
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("srv")

@mcp.tool()
def t() -> str:
    return "x"
""")
    for node in result.nodes:
        assert node.attributes.get("framework") == "fastmcp"