"""Entry point detector — finds HTTP routes and agent calls in Python code.

Uses the standard library `ast` module. No external dependencies.

Detects three things from a single parse of each file:

1. Route decorators (FastAPI, Flask, Starlette) — HTTP and WebSocket:
     @router.get("/path")
     @app.post("/path")
     @router.websocket("/connect")

2. Calls to known agents inside handler bodies. Four heuristics:
   a. `orchestrator.delegate_to_invoice(...)` — suffix → InvoiceAgent
   b. `InvoiceAgent.process(...)`             — direct class call
   c. `run_invoice_agent(...)`                — runner function mapping
   d. `CoPilotAssistant(...)`                 — extra agent class

3. MCP usages: calls to `create_mcp_server("name", ...)` — links
   the enclosing agent to the named MCP server.

Agent calls inside agent modules (e.g. orchestrator invoking
run_invoice_agent) are recorded with `is_agent_to_agent=True`, so
the normalizer emits `delegates_to` edges between agents instead
of from an entry point.
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


# ============================================================
# Class-A name tables — on loan, pending BACKLOG A.3 (entry points)
# ============================================================
# These two tables are the last surviving class-A name heuristics.
# `patterns.py` no longer holds them (ADR-0016, decision 7): the
# classification layer classifies by capability and framework
# provenance, and the pattern registry holds no name that grants a
# kind. They are still read by the four agent-call heuristics below,
# which the A.3 work replaces with entry-point resolution over
# framework-detector provenance; the replacement is pending review.
# Until then they live here, next to their only reader, and they die
# with it.

# Classes that are agents despite not ending in `*Agent`.
EXTRA_AGENT_CLASSES: frozenset[str] = frozenset({
    "CoPilotAssistant",
})

# Runner function name → agent class name.
RUNNER_TO_AGENT: dict[str, str] = {
    "run_orchestrator_agent": "OrchestratorAgent",
    "run_invoice_agent": "InvoiceAgent",
    "run_payments_agent": "PaymentsAgent",
    "run_fraud_agent": "FraudComplianceAgent",
    "run_onboarding_agent": "VendorOnboardingAgent",
    "run_communication_agent": "CommunicationAgent",
}


# Module names that contain agent classes. Used to detect agent-to-agent calls.

@dataclass
class EntryPoint:
    """A single HTTP or WebSocket route."""

    method: str                 # GET | POST | PUT | DELETE | PATCH | WS
    path: str                   # /api/chat
    handler: str                # function name
    file_path: str
    line: int


@dataclass
class AgentCall:
    """A call inside a function body that references an agent.

    `agent_name` is the class name (e.g. InvoiceAgent).
    If `is_agent_to_agent` is True, the enclosing function lives in
    an agent module (e.g. orchestrator.py), meaning this is an
    agent-to-agent delegation rather than an entry→agent edge.
    """

    handler: str                # enclosing function name
    agent_name: str             # e.g. "InvoiceAgent"
    file_path: str
    line: int
    is_agent_to_agent: bool = False


@dataclass
class MCPUsage:
    """A call to create_mcp_server(name) inside a function body.

    Indicates that the enclosing agent (or handler) uses the named
    MCP server.
    """

    handler: str                # enclosing function name
    mcp_server_name: str        # "findrive", "systemutils", etc.
    file_path: str
    line: int
    is_agent_module: bool = False


@dataclass
class EntryPointOutput:
    """Aggregated result of parsing all files."""

    entry_points: list[EntryPoint] = field(default_factory=list)
    agent_calls: list[AgentCall] = field(default_factory=list)
    mcp_usages: list[MCPUsage] = field(default_factory=list)
    call_graph: dict[str, set[str]] = field(default_factory=dict)
    imports: dict[str, list[str]] = field(default_factory=dict)
    files_parsed: int = 0
    files_failed: int = 0



def run(manifest, known_agents: set[str] | None = None) -> EntryPointOutput:
    """Parse every Python file in the manifest for routes and agent calls.

    Args:
        manifest: FileManifest from ingestion.discover()
        known_agents: optional set of agent class names. If provided,
            agent calls are filtered to only those matching a known agent.

    Returns:
        EntryPointOutput with all entry points, agent calls, and MCP
        usages found.
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
        _walk_module(tree, entry.path, output, known_agents)

    return output


def _walk_module(
    tree: ast.Module,
    file_path: str,
    output: EntryPointOutput,
    known_agents: set[str] | None,
) -> None:
    """Walk a module: detect routes, agent calls, MCP usages, CLI entries,
    Streamlit entries; build a per-file call graph and import table."""
    is_agent_module = _is_agent_module(file_path)

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


            _process_function(node, file_path, output, known_agents, is_agent_module)

def _is_agent_module(file_path: str) -> bool:
    """True if the file lives under agents/ — agent-to-agent calls."""
    parts = file_path.split("/")
    return "agents" in parts


def _process_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
    output: EntryPointOutput,
    known_agents: set[str] | None,
    is_agent_module: bool,
) -> None:
    """Handle one function: extract routes, then scan body for
    agent calls and MCP usages."""
    is_route = False

    for decorator in func.decorator_list:
        route = _extract_route(decorator, func, file_path)
        if route is not None:
            output.entry_points.append(route)
            is_route = True

    # Scan for MCP usages regardless of route status. An agent's
    # helper method may create MCP servers, and it lives in a
    # non-route function.
    for child in ast.walk(func):
        usage = _extract_mcp_usage(child, func, file_path, is_agent_module)
        if usage is not None:
            output.mcp_usages.append(usage)

    # Scan for agent calls in route handlers and agent modules only.
    if not is_route and not is_agent_module:
        return

    for child in ast.walk(func):
        call = _extract_agent_call(
            child, func, file_path, known_agents, is_agent_module,
        )
        if call is not None:
            output.agent_calls.append(call)


def _extract_route(
    decorator: ast.expr,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> EntryPoint | None:
    """If the decorator is a route (HTTP or WebSocket), return an EntryPoint."""
    if not isinstance(decorator, ast.Call):
        return None
    if not isinstance(decorator.func, ast.Attribute):
        return None

    attr = decorator.func.attr

    if attr in HTTP_METHODS:
        method = attr.upper()
    elif attr == WEBSOCKET_METHOD:
        method = "WS"
    else:
        return None

    if not decorator.args:
        return None

    path_arg = decorator.args[0]
    if not isinstance(path_arg, ast.Constant) or not isinstance(path_arg.value, str):
        return None

    return EntryPoint(
        method=method,
        path=path_arg.value,
        handler=func.name,
        file_path=file_path,
        line=func.lineno,
    )


def _extract_agent_call(
    node: ast.AST,
    enclosing: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
    known_agents: set[str] | None,
    is_agent_module: bool,
) -> AgentCall | None:
    """If the node is a call that references an agent, return an AgentCall.

    Heuristics (checked in this order):
      1. `orchestrator.delegate_to_invoice(...)` → InvoiceAgent
      2. `run_invoice_agent(...)`                → InvoiceAgent
      3. `CoPilotAssistant(...)`                 → CoPilotAssistant
      4. `InvoiceAgent.process(...)`             → InvoiceAgent
    """
    if not isinstance(node, ast.Call):
        return None

    target_name = _resolve_call_target(node.func)
    if target_name is None:
        return None

    last = target_name.split(".")[-1]
    agent_name: str | None = None

    # Heuristic 1: delegate_to_<suffix> → <Suffix>Agent
    if last.startswith("delegate_to_"):
        suffix = last[len("delegate_to_"):]
        agent_name = _agent_name_from_suffix(suffix)

    # Heuristic 2: runner function — run_invoice_agent(...)
    elif last in RUNNER_TO_AGENT:
        agent_name = RUNNER_TO_AGENT[last]

    # Heuristic 3: extra agent classes — CoPilotAssistant(...)
    elif last in EXTRA_AGENT_CLASSES:
        agent_name = last

    # Heuristic 4: direct class call — InvoiceAgent(...) or
    # InvoiceAgent.process(...). Check the FIRST segment.
    else:
        first = target_name.split(".")[0]
        if first.endswith("Agent"):
            agent_name = first
    # Heuristic 5: agent function passed as argument to another call.
    # Example:
    #     background_tasks.add_task(run_orchestrator_agent, ...)
    if agent_name is None:
        for arg in node.args:
            if isinstance(arg, ast.Name) and arg.id in RUNNER_TO_AGENT:
                agent_name = RUNNER_TO_AGENT[arg.id]
                break

    if agent_name is None:
        return None
    
    if agent_name is None:
        return None

    if known_agents is not None and agent_name not in known_agents:
        return None

    return AgentCall(
        handler=enclosing.name,
        agent_name=agent_name,
        file_path=file_path,
        line=node.lineno,
        is_agent_to_agent=is_agent_module,
    )


def _extract_mcp_usage(
    node: ast.AST,
    enclosing: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
    is_agent_module: bool,
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
        is_agent_module=is_agent_module,
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


def _agent_name_from_suffix(suffix: str) -> str | None:
    """Map delegate_to_<suffix> to a plausible agent class name.

    'invoice'    → 'InvoiceAgent'
    'payments'   → 'PaymentsAgent'
    'onboarding' → 'OnboardingAgent'
    """
    if not suffix:
        return None
    return suffix.capitalize() + "Agent"

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