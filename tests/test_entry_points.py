"""Unit tests for entry point detection via stdlib ast.

Two categories of tests:
  - Route detection (HTTP and WebSocket decorators)
  - Agent call detection (four heuristics)
  - MCP usage detection (create_mcp_server calls)

Files are written to a tmp directory and parsed in-memory —
no external processes, no fixtures in the repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicorellator.ingestion.discovery import FileEntry, FileManifest
from aicorellator.ingestion.target import Target
from aicorellator.parsing.entry_points import run


@pytest.fixture
def tmp_manifest(tmp_path: Path) -> tuple[FileManifest, Target]:
    """An empty manifest and target pointing at tmp_path."""
    target = Target(root=tmp_path, kind="folder")
    m = FileManifest(target=target)
    return m, target


def _add_py(manifest: FileManifest, target: Target, name: str, code: str) -> None:
    """Write a Python file into the target and register it in the manifest."""
    path = target.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code)
    manifest.files.append(FileEntry(
        path=name,
        abs_path=path,
        language="python",
        content_hash="x",
        size=len(code),
    ))


# ============================================================
# Route detection
# ============================================================

def test_detect_fastapi_get(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "api.py", """\
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
def health():
    return {"status": "ok"}
""")
    out = run(manifest)
    assert len(out.entry_points) == 1
    ep = out.entry_points[0]
    assert ep.method == "GET"
    assert ep.path == "/health"
    assert ep.handler == "health"


def test_detect_post(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "api.py", """\
@router.post("/api/chat")
async def chat_endpoint():
    pass
""")
    out = run(manifest)
    assert len(out.entry_points) == 1
    assert out.entry_points[0].method == "POST"
    assert out.entry_points[0].path == "/api/chat"


def test_detect_websocket(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "ws.py", """\
@router.websocket("/connect")
async def websocket_endpoint(websocket):
    await websocket.accept()
""")
    out = run(manifest)
    assert len(out.entry_points) == 1
    ep = out.entry_points[0]
    assert ep.method == "WS"
    assert ep.path == "/connect"
    assert ep.handler == "websocket_endpoint"


def test_no_routes_in_plain_function(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "util.py", """\
def helper(x):
    return x + 1
""")
    out = run(manifest)
    assert out.entry_points == []
    assert out.agent_calls == []
    assert out.mcp_usages == []


def test_syntax_error_handled(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "broken.py", "this is not ( valid python")
    out = run(manifest)
    assert out.files_failed == 1
    assert out.entry_points == []


# ============================================================
# Agent call detection
# ============================================================

def test_detect_delegate_to_call(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "api.py", """\
@router.post("/chat")
async def chat(request):
    return await orchestrator.delegate_to_invoice(request)
""")
    out = run(manifest)
    assert len(out.entry_points) == 1
    assert len(out.agent_calls) == 1
    assert out.agent_calls[0].agent_name == "InvoiceAgent"
    assert out.agent_calls[0].handler == "chat"


def test_detect_direct_agent_call(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "api.py", """\
@router.post("/chat")
async def chat(request):
    return await InvoiceAgent.process(request)
""")
    out = run(manifest)
    assert len(out.agent_calls) == 1
    assert out.agent_calls[0].agent_name == "InvoiceAgent"


def test_detect_runner_function_call(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "api.py", """\
@router.post("/chat")
async def chat(request):
    return await run_orchestrator_agent(request)
""")
    out = run(manifest)
    assert len(out.agent_calls) == 1
    assert out.agent_calls[0].agent_name == "OrchestratorAgent"


def test_detect_copilot_assistant(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "api.py", """\
@router.post("/copilot/chat")
async def copilot_chat(request):
    copilot = CoPilotAssistant(request)
    return copilot
""")
    out = run(manifest)
    assert len(out.agent_calls) == 1
    assert out.agent_calls[0].agent_name == "CoPilotAssistant"


def test_agent_to_agent_call_flagged(tmp_manifest) -> None:
    """Call inside agents/ module → is_agent_to_agent=True."""
    manifest, target = tmp_manifest
    _add_py(manifest, target, "agents/orchestrator.py", """\
class OrchestratorAgent:
    async def delegate_to_invoice(self, task):
        from finbot.agents.runner import run_invoice_agent
        return await run_invoice_agent(task)
""")
    out = run(manifest)
    assert len(out.agent_calls) == 1
    assert out.agent_calls[0].agent_name == "InvoiceAgent"
    assert out.agent_calls[0].is_agent_to_agent is True


def test_route_call_not_flagged_as_agent_to_agent(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "apps/vendor/routes/api.py", """\
@router.post("/register")
async def register(request):
    return await run_orchestrator_agent(request)
""")
    out = run(manifest)
    assert len(out.agent_calls) == 1
    assert out.agent_calls[0].is_agent_to_agent is False


# ============================================================
# MCP usage detection
# ============================================================

def test_detect_create_mcp_server(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "agents/invoice.py", """\
from finbot.mcp.factory import create_mcp_server

class InvoiceAgent:
    async def _get_mcp_servers(self):
        findrive = await create_mcp_server("findrive", self.session_context)
        return findrive
""")
    out = run(manifest)
    assert len(out.mcp_usages) == 1
    usage = out.mcp_usages[0]
    assert usage.mcp_server_name == "findrive"
    assert usage.is_agent_module is True
    assert usage.handler == "_get_mcp_servers"


def test_detect_multiple_mcp_usages(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "agents/invoice.py", """\
class InvoiceAgent:
    async def _get_mcp_servers(self):
        taxcalc = await create_mcp_server("taxcalc", self.session_context)
        findrive = await create_mcp_server("findrive", self.session_context)
        return {"taxcalc": taxcalc, "findrive": findrive}
""")
    out = run(manifest)
    assert len(out.mcp_usages) == 2
    names = {u.mcp_server_name for u in out.mcp_usages}
    assert names == {"taxcalc", "findrive"}


def test_no_mcp_usage_in_plain_code(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "utils.py", """\
def helper():
    return 42
""")
    out = run(manifest)
    assert out.mcp_usages == []