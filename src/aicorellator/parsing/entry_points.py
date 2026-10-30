"""Entry point detector — finds entry points and call sites in Python code.

Uses the standard library `ast` module. No external dependencies.

Detects five things from a single parse of each file:

1. Route decorators — HTTP and WebSocket, in four spellings:

     @router.get("/path")                     FastAPI, Flask, Starlette
     @app.websocket("/connect")
     @app.route("/path", methods=["POST"])    Flask's canonical form
     @require_POST                            Django's own guards
     @require_http_methods(["POST", "PUT"])
     @api_view(["POST"])                      Django REST Framework

2. CLI entries — `if __name__ == "__main__":` at module level.

3. Streamlit entries — `st.chat_input(...)`, `st.button(...)`, ...

4. MCP usages — calls to `create_mcp_server("name", ...)`.

5. Every call site inside every function, as a raw
   `SymbolCall(handler, callee, file_path, line)` record, plus the
   per-file call graph and import table.

**What a route carries.** A method, the function that handles it, the
file and the line — and a path, when the decorator spells one. A Django
view's path lives in `urls.py`, which the AST does not see, so `path` is
`""` for it (`DJANGO_PATH`) and the handler is what names the route. The
id the normalizer derives follows: `(file, method, path)` for a route
with a path, `(file, method, handler)` for one without — `(file, method,
"")` would make two `@require_POST` views in one file a single node
(`normalizer_helpers.entry_point_id` states it, and `EntryPointStage`
names a path-less route `"<METHOD> <handler>"`).

**A call site never names an agent.** This detector does not know what an
agent is, so it does not guess: `SymbolCall.callee` is the call target as
written (`InvoiceAgent.process`, `orchestrator.delegate_to_invoice`,
`run_invoice_agent`), and nothing else is decided here. Which call sites
become `delegates_to` edges is resolved by the normalizer against the
agent nodes the deterministic layers already produced — see
`normalizer_stages/delegation_edges.py` and ADR-0016 §Decision 7.

Before A.3b this module carried the last name→kind table in the
codebase (`delegate_to_<suffix>` → `<Suffix>Agent`, `RUNNER_TO_AGENT`,
`EXTRA_AGENT_CLASSES`, the bare `*Agent` class call, and the
`"agents" in path` rule that flagged agent-to-agent calls). All five are
gone; `test_entry_points.py` asserts the shape that replaced them.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..patterns import (
    HTTP_METHODS,
    MCP_CONNECT_FUNCTION,
    WEBSOCKET_METHOD,
)


log = logging.getLogger(__name__)


@dataclass
class EntryPoint:
    """A single HTTP or WebSocket route."""

    method: str                 # GET | POST | PUT | DELETE | PATCH | WS
    path: str                   # /api/chat
    handler: str                # function name
    file_path: str
    line: int


@dataclass
class SymbolCall:
    """A raw call site: who calls what, where — nothing more.

    `callee` is the call target exactly as written, dotted form kept:
    `InvoiceAgent.process`, `self.orchestrator.run`, `run_invoice_agent`.
    No segment of it is interpreted here — whether the call means
    anything is decided by the normalizer, against nodes that already
    exist (ADR-0016 §Decision 7).
    """

    handler: str                # enclosing function name
    callee: str                 # call target as written
    file_path: str
    line: int


@dataclass
class MCPUsage:
    """A call to create_mcp_server(name) inside a function body.

    Indicates that the enclosing agent (or handler) uses the named
    MCP server. Whether the enclosing symbol *is* an agent is decided by
    the normalizer from the node it resolves to, not from the file path.
    """

    handler: str                # enclosing function name
    mcp_server_name: str        # "findrive", "systemutils", etc.
    file_path: str
    line: int


@dataclass
class EntryPointOutput:
    """Aggregated result of parsing all files."""

    entry_points: list[EntryPoint] = field(default_factory=list)
    symbol_calls: list[SymbolCall] = field(default_factory=list)
    mcp_usages: list[MCPUsage] = field(default_factory=list)
    call_graph: dict[str, set[str]] = field(default_factory=dict)
    imports: dict[str, list[str]] = field(default_factory=dict)
    files_parsed: int = 0
    files_failed: int = 0



def run(manifest) -> EntryPointOutput:
    """Parse every Python file in the manifest.

    Args:
        manifest: FileManifest from ingestion.discover()

    Returns:
        EntryPointOutput with all entry points, symbol calls, MCP
        usages, call graph and imports found.
    """
    output = EntryPointOutput()

    for entry in manifest.files:
        if entry.language != "python":
            continue

        try:
            source = entry.abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning("cannot read %s: %s", entry.path, e)
            output.files_failed += 1
            continue

        try:
            tree = ast.parse(source, filename=str(entry.abs_path))
        except SyntaxError as e:
            log.warning("syntax error in %s: %s", entry.path, e)
            output.files_failed += 1
            continue

        output.files_parsed += 1
        _walk_module(tree, entry.path, output)

    return output


def _walk_module(
    tree: ast.Module,
    file_path: str,
    output: EntryPointOutput,
) -> None:
    """Walk a module: detect routes, symbol calls, MCP usages, CLI entries,
    Streamlit entries; build a per-file call graph and import table."""
    # Imports — collected once per file, used by cross-file resolution.
    output.imports[file_path] = _collect_module_imports(tree)

    # CLI entry: `if __name__ == "__main__":`.
    cli_entry = _detect_cli_entry(tree, file_path)
    if cli_entry is not None:
        output.entry_points.append(cli_entry)

    # Streamlit entry: `st.chat_input(...)`, `st.form_submit_button(...)`, etc.
    streamlit_entries = _detect_streamlit_entries(tree, file_path)
    output.entry_points.extend(streamlit_entries)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            output.call_graph[node.name] = _collect_call_names(node)

            _process_function(node, file_path, output)


def _process_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
    output: EntryPointOutput,
) -> None:
    """Handle one function: extract routes, then scan the body for
    symbol calls and MCP usages."""
    for decorator in func.decorator_list:
        route = _extract_route(decorator, func, file_path)
        if route is not None:
            output.entry_points.append(route)

    # Every function is scanned, route handler or not: a helper defined
    # next to a handler is as much a call site as the handler itself, and
    # a helper inside an agent class is where the delegation often lives.
    # The filter that used to sit here (`is_route or is_agent_module`)
    # was a path heuristic; the resolver decides instead, and it can only
    # decide about calls it was given.
    for child in ast.walk(func):
        usage = _extract_mcp_usage(child, func, file_path)
        if usage is not None:
            output.mcp_usages.append(usage)

        call = _extract_symbol_call(child, func, file_path)
        if call is not None:
            output.symbol_calls.append(call)


# ============================================================
# Route decorators
# ============================================================
#
# Four spellings produce a route, and they arrive in three AST shapes.
# `HTTP_METHODS` stays what it is — the set of *attribute* names FastAPI,
# Flask and Starlette use (`@app.post`, `@router.get`) — because the two
# Django families carry their method where an attribute name is not: in
# the decorator's own name, and in a list argument.

#: Django's method guards — a bare `Name`, and the method is its suffix.
#: `@require_safe` accepts GET and HEAD; the primary method is GET.
DJANGO_METHOD_DECORATORS: dict[str, str] = {
    "require_GET": "GET",
    "require_POST": "POST",
    "require_PUT": "PUT",
    "require_PATCH": "PATCH",
    "require_DELETE": "DELETE",
    "require_safe": "GET",
}

#: Django's list-taking decorators — a call on a `Name` whose first
#: argument is a literal list of method names: `@require_http_methods`
#: and DRF's `@api_view`. A route that accepts POST and PUT is one route
#: here, with POST as the primary method — the same one-node-per-route
#: convention `HTTP_METHODS` follows.
DJANGO_LIST_DECORATORS: frozenset[str] = frozenset({
    "require_http_methods",
    "api_view",
})

#: The path a Django route has in the AST: none. `urls.py` holds the
#: pattern, and this detector does not read it — the handler is what
#: identifies the route (`normalizer_helpers.entry_point_id`).
DJANGO_PATH = ""

#: Flask's canonical route decorator — a call on an attribute whose last
#: segment is `route`. The method is the first name in `methods=`, and
#: Flask's own default is GET. An unresolved `methods=` (a variable, a
#: splat, an empty list) is read as the default too: a false negative,
#: stated, and the reason a route declares one method here.
FLASK_ROUTE_ATTRIBUTE = "route"
FLASK_METHODS_KEYWORD = "methods"
FLASK_DEFAULT_METHOD = "GET"


def _extract_route(
    decorator: ast.expr,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> EntryPoint | None:
    """If the decorator is a route, return an EntryPoint.

    One dispatch per AST shape a route decorator arrives in:

      - a call on an attribute — `@app.post("/upload")`,
        `@router.websocket("/connect")`, and Flask's canonical
        `@app.route("/upload", methods=["POST"])`, which reads its method
        out of `methods=`;
      - a call on a name — `@require_http_methods([...])`,
        `@api_view([...])`;
      - a bare name — `@require_POST` and the other Django guards.

    `@csrf_exempt` is none of the three and needs no case of its own: it
    is a wrapper, and the walk over `func.decorator_list` reaches the
    route decorator under it.
    """
    if isinstance(decorator, ast.Call):
        if isinstance(decorator.func, ast.Attribute):
            return _extract_attribute_route(decorator, func, file_path)
        if isinstance(decorator.func, ast.Name):
            return _extract_django_list_route(decorator, func, file_path)
        return None
    if isinstance(decorator, ast.Name):
        return _extract_django_guard_route(decorator, func, file_path)
    return None


def _extract_attribute_route(
    decorator: ast.Call,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> EntryPoint | None:
    """`@app.post("/upload")`, `@router.websocket("/connect")`, `@app.route`.

    The HTTP-method and WebSocket spellings take their method from the
    attribute's last segment; Flask's canonical `route` takes it from
    `methods=`, and defaults to GET. All three need a literal string path
    as the first positional argument: `@app.post(path)` names no path, so
    it is not a route.
    """
    attr = decorator.func.attr

    if attr == FLASK_ROUTE_ATTRIBUTE:
        method = (
            _first_literal_method(_methods_keyword(decorator))
            or FLASK_DEFAULT_METHOD
        )
    elif attr in HTTP_METHODS:
        method = attr.upper()
    elif attr == WEBSOCKET_METHOD:
        method = "WS"
    else:
        return None

    path = _literal_path(decorator)
    if path is None:
        return None
    return _entry_point(method, path, func, file_path)


def _extract_django_guard_route(
    decorator: ast.Name,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> EntryPoint | None:
    """`@require_POST`, `@require_GET`, ... — Django's bare method guards.

    The method is the decorator's own name (`DJANGO_METHOD_DECORATORS`)
    and the path is `DJANGO_PATH`: a Django view's path is in `urls.py`.
    Every other bare name — `@csrf_exempt`, `@login_required`, a bare
    `@post` — is not in the table and is not a route.
    """
    method = DJANGO_METHOD_DECORATORS.get(decorator.id)
    if method is None:
        return None
    return _entry_point(method, DJANGO_PATH, func, file_path)


def _extract_django_list_route(
    decorator: ast.Call,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> EntryPoint | None:
    """`@require_http_methods([...])` and DRF's `@api_view([...])`.

    The method is the first literal name in the list argument, and the
    path is `DJANGO_PATH`. A list that is not a literal, or holds no
    literal, names no method the AST can read — a false negative, and the
    reason the method is the only thing this reads out of the argument.
    """
    if decorator.func.id not in DJANGO_LIST_DECORATORS:
        return None
    if not decorator.args:
        return None

    method = _first_literal_method(decorator.args[0])
    if method is None:
        return None
    return _entry_point(method, DJANGO_PATH, func, file_path)


def _entry_point(
    method: str,
    path: str,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> EntryPoint:
    """The record every route shape produces; the line is the function's."""
    return EntryPoint(
        method=method,
        path=path,
        handler=func.name,
        file_path=file_path,
        line=func.lineno,
    )


def _literal_path(decorator: ast.Call) -> str | None:
    """The decorator's first positional argument, if it is a literal string."""
    if not decorator.args:
        return None

    path = decorator.args[0]
    if isinstance(path, ast.Constant) and isinstance(path.value, str):
        return path.value
    return None


def _methods_keyword(decorator: ast.Call) -> ast.expr | None:
    """A Flask route's `methods=` value, or None when it is absent."""
    for keyword in decorator.keywords:
        if keyword.arg == FLASK_METHODS_KEYWORD:
            return keyword.value
    return None


def _first_literal_method(node: ast.expr | None) -> str | None:
    """The first literal string in a list or tuple of method names.

    `["POST", "PUT"]` → `"POST"`, and `["put"]` → `"PUT"`: the value is
    uppercased, because a Flask route may spell its methods in lower case
    while a node's `method` is upper. Returns None for anything else — a
    variable, a splat, an empty list, a `Name` — which every caller reads
    as "no method the AST can see".
    """
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None

    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(
            element.value, str
        ):
            return element.value.upper()
    return None


def _extract_symbol_call(
    node: ast.AST,
    enclosing: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> SymbolCall | None:
    """If the node is a call, return the raw call site.

    The callee is recorded as written: `orchestrator.delegate_to_invoice`,
    `InvoiceAgent.process`, `run_invoice_agent`. No segment of it is
    matched against a name table, and no argument is inspected — the
    record is provenance, not a verdict (ADR-0016 §Decision 7).
    """
    if not isinstance(node, ast.Call):
        return None

    callee = _resolve_call_target(node.func)
    if callee is None:
        return None

    return SymbolCall(
        handler=enclosing.name,
        callee=callee,
        file_path=file_path,
        line=node.lineno,
    )


def _extract_mcp_usage(
    node: ast.AST,
    enclosing: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> MCPUsage | None:
    """Detect `create_mcp_server("name", ...)` calls.

    Handles both:
        create_mcp_server("findrive", ctx)
        await create_mcp_server("findrive", ctx)
    — `ast.walk` visits the Call node in both cases.
    """
    if not isinstance(node, ast.Call):
        return None

    target = _resolve_call_target(node.func)
    if target is None:
        return None

    last = target.split(".")[-1]
    if last != MCP_CONNECT_FUNCTION:
        return None

    if not node.args:
        return None

    name_arg = node.args[0]
    if not isinstance(name_arg, ast.Constant) or not isinstance(name_arg.value, str):
        return None

    return MCPUsage(
        handler=enclosing.name,
        mcp_server_name=name_arg.value,
        file_path=file_path,
        line=node.lineno,
    )


def _resolve_call_target(node: ast.expr) -> str | None:
    """Return dotted name of the call target, or None if not resolvable.

    InvoiceAgent.process       → "InvoiceAgent.process"
    self.orchestrator.process  → "self.orchestrator.process"
    run_invoice_agent          → "run_invoice_agent"
    """
    parts: list[str] = []
    current: ast.expr = node

    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value

    if isinstance(current, ast.Name):
        parts.append(current.id)
        parts.reverse()
        return ".".join(parts)

    if isinstance(current, ast.Call):
        return None

    return None


def _collect_call_names(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    """Names of all functions called anywhere in the function body.

    Only `ast.Name` call targets are recorded. Attribute calls
    (`obj.method()`) are skipped: resolving them requires knowing the
    receiver's type, which is out of scope for a control-flow graph.
    This is a deliberate under-approximation.

    Nested functions are included in the walk: a helper defined inside
    a handler is recorded under its own name, and its calls are part
    of the handler's reachability set as well.
    """
    calls: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.add(node.func.id)
    return calls

# ============================================================
# Module imports
# ============================================================

def _collect_module_imports(tree: ast.Module) -> list[str]:
    """Dotted module names imported by a file.

    Handles:
        import tools                  → "tools"
        import mcp.servers.foo        → "mcp.servers.foo"
        from tools import X           → "tools"
        from mcp.servers.foo import X → "mcp.servers.foo"

    Relative imports are skipped: resolving them requires a package
    root, which the manifest does not provide.
    """
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue
            if node.module:
                out.append(node.module)
    return list(dict.fromkeys(out))


# ============================================================
# CLI entry point
# ============================================================

def _detect_cli_entry(tree: ast.Module, file_path: str) -> EntryPoint | None:
    """Detect `if __name__ == "__main__":` at module level.

    Returns an EntryPoint with method="CLI" and path set to the
    file's relative path. The handler is the file itself (there is
    no function name), recorded as "<module>" so downstream
    components can distinguish it.

    Only module-level `if` blocks are considered. A nested
    `if __name__ == "__main__":` inside a function is unusual and
    is not treated as a script entry.
    """
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        if _is_name_main_guard(node):
            return EntryPoint(
                method="CLI",
                path=file_path,
                handler="<module>",
                file_path=file_path,
                line=node.lineno,
            )
    return None


def _is_name_main_guard(node: ast.If) -> bool:
    """True if the `if` test is `__name__ == "__main__"`.

    Matches both orders:
        __name__ == "__main__"
        "__main__" == __name__
    """
    test = node.test
    if not isinstance(test, ast.Compare):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]

    def is_name_main(n: ast.expr) -> bool:
        return isinstance(n, ast.Name) and n.id == "__name__"

    def is_str_main(n: ast.expr) -> bool:
        return isinstance(n, ast.Constant) and n.value == "__main__"

    return (is_name_main(left) and is_str_main(right)) or (
        is_str_main(left) and is_name_main(right)
    )


# ============================================================
# Streamlit entry points
# ============================================================

# Streamlit calls that represent user-facing entry points.
# `st.chat_input` — chat prompt submission.
# `st.form_submit_button` — form submission.
# `st.button` — button press.
# `st.file_uploader` — file upload.
STREAMLIT_ENTRY_CALLS: frozenset[str] = frozenset({
    "chat_input",
    "form_submit_button",
    "button",
    "file_uploader",
})


def _detect_streamlit_entries(
    tree: ast.Module,
    file_path: str,
) -> list[EntryPoint]:
    """Detect Streamlit user-facing entry points.

    Returns one EntryPoint per distinct `st.<entry_call>(...)` found
    at module level. Method is "STREAMLIT", path is the call name
    (e.g. "chat_input"), handler is "<module>".

    Only detected if the file imports `streamlit` (as `st` or as
    `streamlit`), to avoid false positives on unrelated `.chat_input`
    attributes.
    """
    if not _has_streamlit_import(tree):
        return []

    found: list[EntryPoint] = []
    seen: set[str] = set()

    for node in tree.body:
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if not isinstance(child.func, ast.Attribute):
                continue
            if not isinstance(child.func.value, ast.Name):
                continue
            if child.func.value.id not in ("st", "streamlit"):
                continue
            if child.func.attr not in STREAMLIT_ENTRY_CALLS:
                continue
            if child.func.attr in seen:
                continue
            seen.add(child.func.attr)
            found.append(EntryPoint(
                method="STREAMLIT",
                path=child.func.attr,
                handler="<module>",
                file_path=file_path,
                line=child.lineno,
            ))

    return found


def _has_streamlit_import(tree: ast.Module) -> bool:
    """True if the file imports streamlit (any form)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "streamlit":
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "streamlit":
                return True
    return False