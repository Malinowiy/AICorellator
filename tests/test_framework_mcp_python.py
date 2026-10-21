"""Unit tests for the MCP Python SDK detector."""

from __future__ import annotations

import ast

from aicorellator.parsing.framework_detectors.mcp_python import MCPPythonDetector


def _detect(code: str):
    tree = ast.parse(code)
    return MCPPythonDetector().detect("test.py", tree, code)


def test_no_mcp_server_import_returns_empty() -> None:
    result = _detect("x = 1\n")
    assert result.nodes == []
    assert result.edges == []


def test_fastmcp_import_not_matched() -> None:
    """The FastMCP import is handled by a different detector."""
    result = _detect("""
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("srv")
""")
    assert result.nodes == []


def test_detect_server_module_level() -> None:
    result = _detect("""
from mcp.server import Server
server = Server("my-server")
""")
    servers = [n for n in result.nodes if n.kind == "mcp_server"]
    assert len(servers) == 1
    assert servers[0].name == "my-server"
    assert servers[0].attributes["framework"] == "mcp_python"


def test_detect_server_as_self_attribute() -> None:
    result = _detect("""
from mcp.server import Server

class MyServer:
    def __init__(self):
        self.server = Server("my-server")
""")
    servers = [n for n in result.nodes if n.kind == "mcp_server"]
    assert len(servers) == 1
    assert servers[0].name == "my-server"
    assert servers[0].attributes["server_variable"] == "self.server"


def test_detect_tool_decorator_self_server() -> None:
    result = _detect("""
from mcp.server import Server

class MyServer:
    def __init__(self):
        self.server = Server("srv")

    def setup(self):
        @self.server.call_tool()
        async def search(query: str) -> str:
            return "ok"
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert len(tools) == 1
    assert tools[0].name == "search"
    assert tools[0].attributes["parent_server"] == "srv"


def test_detect_tool_decorator_module_server() -> None:
    result = _detect("""
from mcp.server import Server
server = Server("srv")

@server.call_tool()
async def search(query: str) -> str:
    return "ok"
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert len(tools) == 1
    assert tools[0].name == "search"
    assert tools[0].attributes["parent_server"] == "srv"


def test_tool_capabilities_extracted() -> None:
    result = _detect("""
import subprocess
from mcp.server import Server

server = Server("srv")

@server.call_tool()
async def run_cmd(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True).stdout.decode()
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert len(tools) == 1
    caps = tools[0].capabilities
    assert "executes_code" in caps
    assert "executes_shell" in caps


def test_multiple_tools_on_one_server() -> None:
    result = _detect("""
from mcp.server import Server

server = Server("srv")

@server.call_tool()
async def tool_a() -> str:
    return "a"

@server.call_tool()
async def tool_b() -> str:
    return "b"
""")
    tools = {n.name for n in result.nodes if n.kind == "tool"}
    assert tools == {"tool_a", "tool_b"}


def test_unrelated_decorator_ignored() -> None:
    result = _detect("""
from mcp.server import Server

server = Server("srv")

@other.call_tool()
async def not_a_tool() -> str:
    return "x"
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert tools == []


def test_other_server_decorators_ignored() -> None:
    """list_resources, read_resource, etc. are not tools."""
    result = _detect("""
from mcp.server import Server

server = Server("srv")

@server.list_resources()
async def list_res():
    return []

@server.read_resource()
async def read_res(uri: str) -> str:
    return ""
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert tools == []


def test_provenance_in_attributes() -> None:
    result = _detect("""
from mcp.server import Server
server = Server("srv")

@server.call_tool()
async def t() -> str:
    return "x"
""")
    for node in result.nodes:
        assert node.attributes.get("framework") == "mcp_python"


# ------------------------------------------------------------
# 1.2a.2 — tool_data_flow for the normalizer's server → tool edge
# ------------------------------------------------------------

def test_tool_with_parameters_records_parameter_match() -> None:
    """A tool with a parameter receives the client's data (MCP channel)."""
    result = _detect("""
from mcp.server import Server
server = Server("srv")

@server.call_tool()
async def search(query: str) -> str:
    return "ok"
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert len(tools) == 1
    assert tools[0].attributes["tool_data_flow"] == "parameter_match"
    # The detector emits no edges; the normalizer copies the attribute
    # onto the edge it builds.
    assert result.edges == []


def test_tool_without_parameters_records_no_args() -> None:
    """A parameterless tool has nothing to receive."""
    result = _detect("""
from mcp.server import Server
server = Server("srv")

@server.call_tool()
async def health() -> str:
    return "ok"
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert len(tools) == 1
    assert tools[0].attributes["tool_data_flow"] == "no_args"


def test_tool_method_ignores_self() -> None:
    """`self` is not a client-supplied argument."""
    result = _detect("""
from mcp.server import Server

class MyServer:
    def __init__(self):
        self.server = Server("srv")

    def setup(self):
        @self.server.call_tool()
        async def ping(self) -> str:
            return "ok"
""")
    tools = [n for n in result.nodes if n.kind == "tool"]
    assert len(tools) == 1
    assert tools[0].attributes["tool_data_flow"] == "no_args"