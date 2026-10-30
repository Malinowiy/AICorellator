"""Unit tests for entry point detection via stdlib ast.

Four categories of tests:
  - Route detection (HTTP and WebSocket decorators)
  - Symbol call records (raw call sites, ADR-0016 decision 7)
  - MCP usage detection (create_mcp_server calls)
  - CLI and Streamlit entry points

Files are written to a tmp directory and parsed in-memory —
no external processes, no fixtures in the repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicorellator.ingestion.discovery import FileEntry, FileManifest
from aicorellator.ingestion.target import Target
from aicorellator.parsing.entry_points import DJANGO_PATH, run
from aicorellator.parsing.normalizer_helpers import entry_point_id


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


def test_detect_django_require_post(tmp_manifest) -> None:
    """`@require_POST` is a route: the method is the decorator's name.

    The path is `DJANGO_PATH` — empty — because a Django view's path is
    in `urls.py`, which the AST does not see. The handler and the line
    are the view's, which is what the upload detectors match.
    """
    manifest, target = tmp_manifest
    _add_py(manifest, target, "views.py", '''\
"""A Django view."""
from django.views.decorators.http import require_POST


@require_POST
def upload(request):
    return request.FILES["file"]
''')
    out = run(manifest)
    assert len(out.entry_points) == 1
    ep = out.entry_points[0]
    assert ep.method == "POST"
    assert ep.path == DJANGO_PATH == ""
    assert ep.handler == "upload"
    assert ep.line == 6


def test_detect_django_require_get(tmp_manifest) -> None:
    """`@require_GET` names GET, and `@require_safe` names it too."""
    manifest, target = tmp_manifest
    _add_py(manifest, target, "views.py", '''\
"""A Django view."""
from django.views.decorators.http import require_GET


@require_GET
def report(request):
    return request.FILES["file"]
''')
    _add_py(manifest, target, "safe.py", '''\
"""A Django view guarded for safe methods."""
from django.views.decorators.http import require_safe


@require_safe
def index(request):
    return request.FILES["file"]
''')
    out = run(manifest)
    assert [
        (ep.method, ep.path, ep.handler) for ep in out.entry_points
    ] == [("GET", "", "report"), ("GET", "", "index")]


def test_detect_django_require_http_methods_takes_the_first(
    tmp_manifest,
) -> None:
    """`@require_http_methods(["POST", "PUT"])` is one route, method POST.

    A route that accepts both is one node with the primary method, the
    convention every other route shape follows; the PUT is a false
    negative at the entry-point level, stated.
    """
    manifest, target = tmp_manifest
    _add_py(manifest, target, "views.py", '''\
"""A Django view."""
from django.views.decorators.http import require_http_methods


@require_http_methods(["POST", "PUT"])
def upload(request):
    return request.FILES["file"]
''')
    out = run(manifest)
    assert len(out.entry_points) == 1
    assert out.entry_points[0].method == "POST"


def test_detect_django_api_view_takes_the_first(tmp_manifest) -> None:
    """DRF's `@api_view(["POST"])` is a call on a name, and a route."""
    manifest, target = tmp_manifest
    _add_py(manifest, target, "views.py", '''\
"""A DRF view."""
from rest_framework.decorators import api_view


@api_view(["POST"])
def upload(request):
    return request.FILES["file"]
''')
    out = run(manifest)
    assert len(out.entry_points) == 1
    assert out.entry_points[0].method == "POST"
    assert out.entry_points[0].path == DJANGO_PATH


def test_two_django_views_in_one_file_are_two_routes(tmp_manifest) -> None:
    """The id is `(file, method, handler)`, so the empty path cannot merge.

    Two `@require_POST` views in one file share the file, the method and
    the empty path; the handler is the only thing that tells them apart,
    and `entry_point_id` keys on it when there is no path.
    """
    manifest, target = tmp_manifest
    _add_py(manifest, target, "views.py", '''\
"""Two Django views in one file."""
from django.views.decorators.http import require_POST


@require_POST
def upload(request):
    return request.FILES["file"]


@require_POST
def upload_other(request):
    return request.FILES["other"]
''')
    out = run(manifest)
    assert [(ep.method, ep.path, ep.handler) for ep in out.entry_points] == [
        ("POST", "", "upload"),
        ("POST", "", "upload_other"),
    ]
    first, second = out.entry_points
    assert entry_point_id(first) != entry_point_id(second)


def test_a_django_guard_with_a_variable_list_is_not_a_route(
    tmp_manifest,
) -> None:
    """`@require_http_methods(METHODS)` names no method the AST can read."""
    manifest, target = tmp_manifest
    _add_py(manifest, target, "views.py", '''\
"""A Django view."""
from django.views.decorators.http import require_http_methods

METHODS = ["POST"]


@require_http_methods(METHODS)
def upload(request):
    return request.FILES["file"]
''')
    out = run(manifest)
    assert out.entry_points == []


def test_detect_csrf_exempt_alone_is_not_a_route(tmp_manifest) -> None:
    """`@csrf_exempt` is a wrapper: no method, no path, no route."""
    manifest, target = tmp_manifest
    _add_py(manifest, target, "views.py", '''\
"""A Django view."""
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def upload(request):
    return request.FILES["file"]
''')
    out = run(manifest)
    assert out.entry_points == []


def test_detect_django_route_below_csrf_exempt(tmp_manifest) -> None:
    """The decorator walk reaches the route under the wrapper.

    `@csrf_exempt` is not a route and needs no special case: the walk
    over `func.decorator_list` sees both decorators, and the guard is the
    one that produces the record.
    """
    manifest, target = tmp_manifest
    _add_py(manifest, target, "views.py", '''\
"""A Django view."""
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


@csrf_exempt
@require_POST
def upload(request):
    return request.FILES["file"]
''')
    out = run(manifest)
    assert len(out.entry_points) == 1
    ep = out.entry_points[0]
    assert (ep.method, ep.path, ep.handler, ep.line) == (
        "POST", "", "upload", 8,
    )


def test_detect_flask_route_with_methods(tmp_manifest) -> None:
    """`@app.route("/upload", methods=["POST"])` — Flask's canonical form.

    The last segment is `route`, not an HTTP method, so the method comes
    out of the `methods=` keyword; the path is the first positional
    string, exactly as for `@app.post`.
    """
    manifest, target = tmp_manifest
    _add_py(manifest, target, "app.py", '''\
"""A Flask view."""
from flask import Flask, request

app = Flask(__name__)


@app.route("/upload", methods=["POST"])
def upload():
    return request.files["file"]
''')
    out = run(manifest)
    assert len(out.entry_points) == 1
    ep = out.entry_points[0]
    assert ep.method == "POST"
    assert ep.path == "/upload"
    assert ep.handler == "upload"
    assert ep.line == 8


def test_detect_flask_route_takes_the_first_method(tmp_manifest) -> None:
    """`methods=["PUT", "POST"]` is one route, method PUT — the first.

    A tuple of methods is read the same way, because Flask accepts both
    spellings and the node's method is the primary one either way.
    """
    manifest, target = tmp_manifest
    _add_py(manifest, target, "app.py", '''\
"""A Flask view."""
from flask import Flask, request

app = Flask(__name__)


@app.route("/upload", methods=["PUT", "POST"])
def upload():
    return request.files["file"]


@app.route("/store", methods=("PATCH",))
def store():
    return request.files["file"]
''')
    out = run(manifest)
    assert [(ep.method, ep.path) for ep in out.entry_points] == [
        ("PUT", "/upload"), ("PATCH", "/store"),
    ]


def test_detect_flask_route_defaults_to_get(tmp_manifest) -> None:
    """`@app.route("/health")` declares no method, so the default is GET."""
    manifest, target = tmp_manifest
    _add_py(manifest, target, "app.py", '''\
"""A Flask view."""
from flask import Flask

app = Flask(__name__)


@app.route("/health")
def health():
    return {"status": "ok"}
''')
    out = run(manifest)
    assert len(out.entry_points) == 1
    assert out.entry_points[0].method == "GET"
    assert out.entry_points[0].path == "/health"


def test_detect_flask_route_with_a_variable_methods_is_get(
    tmp_manifest,
) -> None:
    """`methods=METHODS` is unresolved, so it is read as the default.

    A false negative, stated: the route exists and the method does not
    reach the AST, so the node carries GET — the same reading Flask
    itself gives a route that declares no method.
    """
    manifest, target = tmp_manifest
    _add_py(manifest, target, "app.py", '''\
"""A Flask view."""
from flask import Flask, request

app = Flask(__name__)
METHODS = ["POST"]


@app.route("/upload", methods=METHODS)
def upload():
    return request.files["file"]
''')
    out = run(manifest)
    assert len(out.entry_points) == 1
    assert out.entry_points[0].method == "GET"


def test_a_flask_route_without_a_literal_path_is_not_a_route(
    tmp_manifest,
) -> None:
    """`@app.route(path)` names no path, as `@app.post(path)` does not."""
    manifest, target = tmp_manifest
    _add_py(manifest, target, "app.py", '''\
"""A Flask view."""
from flask import Flask, request

app = Flask(__name__)
path = "/upload"


@app.route(path, methods=["POST"])
def upload():
    return request.files["file"]
''')
    out = run(manifest)
    assert out.entry_points == []


def test_no_routes_in_plain_function(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "util.py", """\
def helper(x):
    return x + 1
""")
    out = run(manifest)
    assert out.entry_points == []
    assert out.symbol_calls == []
    assert out.mcp_usages == []


def test_syntax_error_handled(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "broken.py", "this is not ( valid python")
    out = run(manifest)
    assert out.files_failed == 1
    assert out.entry_points == []


# ============================================================
# Symbol call records (ADR-0016, decision 7)
# ============================================================
# A call site never names an agent: the detector records the callee as
# written and decides nothing. Which calls become `delegates_to` edges
# is the normalizer's job (see
# `test_normalizer_stages/test_delegation_edges.py`).

def test_symbol_call_records_attribute_callee(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "api.py", """\
@router.post("/chat")
async def chat(request):
    return await orchestrator.delegate_to_invoice(request)
""")
    out = run(manifest)
    assert len(out.entry_points) == 1
    # Two records: the decorator is part of the FunctionDef node, so the
    # walk sees `router.post` too. It is recorded like any other call —
    # nothing here decides which calls matter.
    call = next(
        c for c in out.symbol_calls
        if c.callee == "orchestrator.delegate_to_invoice"
    )
    assert call.handler == "chat"
    assert call.file_path == "api.py"
    assert call.line == 3


def test_symbol_call_records_plain_callee(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "api.py", """\
@router.post("/chat")
async def chat(request):
    return await run_orchestrator_agent(request)
""")
    out = run(manifest)
    assert "run_orchestrator_agent" in [c.callee for c in out.symbol_calls]


def test_symbol_call_carries_no_agent_verdict(tmp_manifest) -> None:
    """No agent name, no agent-to-agent flag: the record is a position.

    `agent_name` and `is_agent_to_agent` were the two fields the deleted
    heuristics filled in. Their absence is the point of A.3b.
    """
    manifest, target = tmp_manifest
    _add_py(manifest, target, "agents/orchestrator.py", """\
class OrchestratorAgent:
    async def delegate_to_invoice(self, task):
        return await self.llm_client.chat(task)
""")
    out = run(manifest)
    call = out.symbol_calls[0]
    assert call.callee == "self.llm_client.chat"
    assert not hasattr(call, "agent_name")
    assert not hasattr(call, "is_agent_to_agent")


def test_symbol_calls_recorded_outside_routes(tmp_manifest) -> None:
    """Every function is scanned, not just route handlers and agents/.

    The deleted gate was `is_route or is_agent_module`; a helper in a
    plain module was invisible to it. The resolver filters instead, and
    it can only filter what it is given.
    """
    manifest, target = tmp_manifest
    _add_py(manifest, target, "services/plumbing.py", """\
def helper(client):
    return client.send("ping")
""")
    out = run(manifest)
    assert out.entry_points == []
    assert [c.callee for c in out.symbol_calls] == ["client.send"]


def test_symbol_call_line_follows_the_call(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "api.py", """\
def handler():
    first()
    second()
""")
    out = run(manifest)
    assert [(c.callee, c.line) for c in out.symbol_calls] == [
        ("first", 2), ("second", 3),
    ]


def test_call_target_without_a_name_is_skipped(tmp_manifest) -> None:
    """`factory()()` has no dotted name of its own.

    The walk still sees the inner `factory()`, which is a call like any
    other; only the outer nesting has nothing to name.
    """
    manifest, target = tmp_manifest
    _add_py(manifest, target, "api.py", """\
def handler(factory):
    return factory()()
""")
    out = run(manifest)
    assert [c.callee for c in out.symbol_calls] == ["factory"]


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
    assert usage.handler == "_get_mcp_servers"
    # No path flag: whether the enclosing symbol is an agent is resolved
    # from the node it maps to (ADR-0016, decision 7).
    assert not hasattr(usage, "is_agent_module")


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

def test_call_graph_collected(tmp_manifest) -> None:
    """Call graph records function-to-function calls per file."""
    manifest, target = tmp_manifest
    _add_py(manifest, target, "app.py", """\
@app.post("/process")
def process(request):
    data = parse_request(request)
    result = run_analysis(data)
    return result

def parse_request(request):
    return request.json()

def run_analysis(data):
    return "ok"
""")
    out = run(manifest)
    cg = out.call_graph
    assert "process" in cg
    assert "parse_request" in cg["process"]
    assert "run_analysis" in cg["process"]
    assert "parse_request" in cg
    assert "run_analysis" in cg


def test_call_graph_includes_helpers_without_routes(tmp_manifest) -> None:
    """Helpers not decorated as routes are still in the call graph."""
    manifest, target = tmp_manifest
    _add_py(manifest, target, "util.py", """\
def main():
    return helper()

def helper():
    return 42
""")
    out = run(manifest)
    assert "main" in out.call_graph
    assert "helper" in out.call_graph
    assert "helper" in out.call_graph["main"]


def test_call_graph_empty_for_plain_module(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "empty.py", "x = 1\n")
    out = run(manifest)
    assert out.call_graph == {}

    # ============================================================
# CLI entry points
# ============================================================

def test_detect_cli_entry(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "cli.py", """\
from app.menu import run

if __name__ == "__main__":
    run()
""")
    out = run(manifest)
    cli = [ep for ep in out.entry_points if ep.method == "CLI"]
    assert len(cli) == 1
    assert cli[0].path == "cli.py"
    assert cli[0].handler == "<module>"


def test_detect_cli_entry_reversed_guard(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "cli.py", """\
if "__main__" == __name__:
    print("hello")
""")
    out = run(manifest)
    cli = [ep for ep in out.entry_points if ep.method == "CLI"]
    assert len(cli) == 1


def test_no_cli_entry_without_guard(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "lib.py", """\
def run():
    print("hi")
""")
    out = run(manifest)
    cli = [ep for ep in out.entry_points if ep.method == "CLI"]
    assert cli == []


def test_no_cli_entry_for_nested_guard(tmp_manifest) -> None:
    """A `if __name__` inside a function is not a script entry."""
    manifest, target = tmp_manifest
    _add_py(manifest, target, "lib.py", """\
def main():
    if __name__ == "__main__":
        print("nested")
""")
    out = run(manifest)
    cli = [ep for ep in out.entry_points if ep.method == "CLI"]
    assert cli == []


def test_call_graph_after_cli_change(tmp_manifest) -> None:
    """Call graph is still collected after the CLI change."""
    manifest, target = tmp_manifest
    _add_py(manifest, target, "cli.py", """\
def main():
    helper()

def helper():
    return 42

if __name__ == "__main__":
    main()
""")
    out = run(manifest)
    assert "main" in out.call_graph
    assert "helper" in out.call_graph["main"]


def test_imports_collected(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "cli.py", """\
import os
from app.menu import run
from app.runner import Runner
""")
    out = run(manifest)
    imports = out.imports.get("cli.py", [])
    assert "os" in imports
    assert "app.menu" in imports
    assert "app.runner" in imports


# ============================================================
# Streamlit entry points
# ============================================================

def test_detect_streamlit_chat_input(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "app.py", """\
import streamlit as st

if prompt := st.chat_input("Ask"):
    st.write(prompt)
""")
    out = run(manifest)
    sl = [ep for ep in out.entry_points if ep.method == "STREAMLIT"]
    assert len(sl) == 1
    assert sl[0].path == "chat_input"


def test_detect_streamlit_multiple_calls(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "app.py", """\
import streamlit as st

if st.button("Go"):
    st.write("go")
if st.file_uploader("File"):
    st.write("uploaded")
""")
    out = run(manifest)
    sl = sorted(ep.path for ep in out.entry_points if ep.method == "STREAMLIT")
    assert sl == ["button", "file_uploader"]


def test_no_streamlit_entry_without_import(tmp_manifest) -> None:
    manifest, target = tmp_manifest
    _add_py(manifest, target, "app.py", """\
class st:
    @staticmethod
    def chat_input(x):
        return x

st.chat_input("test")
""")
    out = run(manifest)
    sl = [ep for ep in out.entry_points if ep.method == "STREAMLIT"]
    assert sl == []