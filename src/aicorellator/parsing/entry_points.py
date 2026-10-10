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


log = logging.getLogger(__name__)


# HTTP methods recognized as route decorators.
HTTP_METHODS = frozenset({
    "get", "post", "put", "delete", "patch", "options", "head",
})

# WebSocket decorator attribute name.
WEBSOCKET_METHOD = "websocket"

# Map runner function names to agent class names.
# Used when a handler calls `run_invoice_agent(...)`.
RUNNER_TO_AGENT: dict[str, str] = {
    "run_orchestrator_agent": "OrchestratorAgent",
    "run_invoice_agent": "InvoiceAgent",
    "run_payments_agent": "PaymentsAgent",
    "run_fraud_agent": "FraudComplianceAgent",
    "run_onboarding_agent": "VendorOnboardingAgent",
    "run_communication_agent": "CommunicationAgent",
}

# Agent classes that are not named `*Agent` but are agents.
EXTRA_AGENT_CLASSES: frozenset[str] = frozenset({
    "CoPilotAssistant",
})

# Function name that indicates connecting to an MCP server.
MCP_CONNECT_FUNCTION = "create_mcp_server"


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
    """Walk a module: detect routes, agent calls, and MCP usages."""
    is_agent_module = _is_agent_module(file_path)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
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