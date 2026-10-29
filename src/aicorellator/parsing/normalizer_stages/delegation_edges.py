"""DelegationEdgeStage — `delegates_to` and agent → server.

Run order: 8 of 20 (see `normalizer_stages.STAGES`).

C4: "**Delegation edges** — entry point to agent, agent to agent, agent
to MCP server."

Three shapes, one question — who hands control to whom:

    entry_point → agent        a route handler calls an agent
    agent → agent              an orchestrator delegates to a worker
    agent → mcp_server         an agent module holds an MCP client

The agent indexes are built here (they need every agent node, which the
class- and method-node stages produced) and stored on the context: later
stages and the MCP entry synthesis read them.

**A call site never names an agent** (ADR-0016 §Decision 7, BACKLOG
A.3b). `entry_points.py` records raw call sites — `SymbolCall(handler,
callee, file_path, line)` — and this stage decides which of them is a
delegation, by resolving the callee against agent nodes that already
exist. Every name below is a lookup key, never a claim about a kind: a
key that opens nothing produces no edge, and a call site into a file
with no agent node produces no edge either.

Which shape an edge takes is decided by the source node, not by a path:
the enclosing function is an entry handler → `entry → agent`; otherwise
the call lives in a file that holds an agent node → `agent → agent`.

**Hop 1 reads invocations, not mentions.** A name counts when the body
invokes it or hands it to a call the project defines; a name in any other
position is a mention, not a delegation — `logger.info("Using %s",
InvoiceAgent)` names the agent and produces no edge. Two shapes are
deliberately out of scope: a keyword argument (`run(agent=WorkerAgent)`)
and a method call whose argument names an agent
(`self.run_agent(WorkerAgent)`) — the latter needs a decision about
whether a call on `self` is a delegation at all.

Side effects: fills `ctx.agent_node_by_name` and `ctx.agent_node_by_file`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from ...graph.models import Edge, Node
from ..entry_points import EntryPointOutput, MCPUsage, SymbolCall
from ..normalizer_context import FunctionNode, NormalizeContext
from ..normalizer_helpers import CONTROL_FLOW, edge_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta
    from ..models import TSAOutput

# ============================================================
# Symbol-call resolution (ADR-0016 §Decision 7)
# ============================================================

# How far the resolver follows a callee away from the call site.
#
# One hop is the defining symbol's own body: `run_worker_agent` in
# `agents/runner.py` is one hop from the route that calls it. A second
# hop would be interprocedural reachability — what `run_worker_agent`
# itself calls — and the AST has no call resolution to support it
# (ADR-0001), so a second hop is name matching with nothing behind it.
# The bound is a constant, not a parameter, because widening it is a
# decision about the resolver's evidence, not a tuning knob.
MAX_REACHABILITY_HOPS = 1


@dataclass(frozen=True)
class ResolvedDelegation:
    """One call site that resolved to an agent node."""

    source_id: str
    target_id: str
    source_kind: str        # "entry_point" | "agent"
    evidence: str           # handler → callee [→ agent], names only


def _iter_definition(
    functions: dict[tuple[str, int], FunctionNode], name: str
) -> FunctionNode | None:
    """First definition of `name` in a file's function index.

    Keyed by `(name, line_start)`, so a name defined twice (two classes
    with a method of the same name) resolves to the first in source
    order — the same tie-break `index_functions` documents.
    """
    for (func_name, _line), node in functions.items():
        if func_name == name:
            return node
    return None


def _called_names(func: ast.expr) -> set[str]:
    """The base name of a call target.

    `WorkerAgent()` and `OrchestratorAgent().handle(request)` name the
    same class: attribute access and nested calls are unwrapped down to
    the `ast.Name` at the bottom.
    """
    while isinstance(func, ast.Attribute):
        func = func.value
    if isinstance(func, ast.Call):
        return _called_names(func.func)
    if isinstance(func, ast.Name):
        return {func.id}
    return set()


def _referenced_names(
    node: ast.AST, is_project_symbol: Callable[[str], bool]
) -> set[str]:
    """The names a body invokes, or hands to a project-defined function.

    Two shapes count, and only two:

        WorkerAgent()                          the call target's base name
        run_agent_with_retry(WorkerAgent, task)  an argument of a bare-name
                                                 call the project defines

    The second shape is why the callee has to be a project symbol: a name
    handed to project code is evidence, the same name handed to a library
    call is a mention. `logger.info("Using %s", InvoiceAgent)` names the
    agent and is not a delegation — reading every `ast.Name` in the body
    (the pre-fix rule) turned that mention into an edge.

    Not covered here, on purpose: keyword arguments
    (`run(agent=WorkerAgent)`) and method calls whose argument names an
    agent (`self.run_agent(WorkerAgent)`) — see the module docstring.
    """
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        names |= _called_names(child.func)
        if isinstance(child.func, ast.Name) and is_project_symbol(
            child.func.id
        ):
            for arg in child.args:
                names |= {
                    n.id for n in ast.walk(arg) if isinstance(n, ast.Name)
                }
    return names


def _module_matches_file(module: str, file_path: str) -> bool:
    """True if a dotted module name can be this repo-relative file.

    The manifest carries no package root, so the two spellings of one
    module differ by an unknown prefix: `app.agents.runner` and
    `agents/runner.py` are the same file, as are `runner` and
    `agents/runner.py`. Comparison is therefore on trailing path
    segments, and the shorter side may be a suffix of the longer.
    `__init__.py` is dropped, so `from app.services import x` matches
    `app/services/__init__.py`.

    Deliberately permissive: a wrong match costs one candidate file to
    examine, and the candidate still has to define the symbol and reach
    an agent node (ADR-0009 accepts the trade).
    """
    module_segments = [s for s in module.split(".") if s]
    if not module_segments:
        return False
    if file_path.endswith(".py"):
        file_segments = file_path[:-3].split("/")
    else:
        file_segments = file_path.split("/")
    file_segments = [s for s in file_segments if s]
    if file_segments and file_segments[-1] == "__init__":
        file_segments = file_segments[:-1]
    if not file_segments:
        return False
    depth = min(len(module_segments), len(file_segments))
    return module_segments[-depth:] == file_segments[-depth:]


class SymbolCallResolver:
    """Resolve raw `SymbolCall` records against the agent nodes in a delta.

    Built once per run, from what the scan already produced. Nothing here
    asserts that a call *is* a delegation: it looks for an agent node the
    normalizer has already decided on, and links the call site to it.

        agent nodes      `(name, source_file)` → id
        TSA symbols      `(file, name)` → the class the symbol sits in
        the call site    its file's imports, and the files they name
        a file           its functions, for the one-hop body read
    """

    def __init__(
        self,
        *,
        nodes: list[Node],
        files: list[str],
        entry_points: EntryPointOutput,
        tsa_output: "TSAOutput",
        entry_node_by_handler: dict[str, str],
        functions_for: Callable[[str], dict[tuple[str, int], FunctionNode]],
    ) -> None:
        self.agent_node_by_name: dict[str, str] = {}
        self.agent_node_by_file: dict[str, str] = {}
        self._agent_by_location: dict[tuple[str, str], str] = {}
        self._agent_name_by_id: dict[str, str] = {}
        self._agent_file_by_id: dict[str, str] = {}
        for node in nodes:
            if node.kind != "agent":
                continue
            source_file = node.attributes.get("source_file", "")
            self.agent_node_by_name[node.name] = node.id
            self.agent_node_by_file[source_file] = node.id
            self._agent_by_location[(node.name, source_file)] = node.id
            self._agent_name_by_id[node.id] = node.name
            self._agent_file_by_id[node.id] = source_file

        self._entry_points = entry_points
        # (file, handler) → entry node. Keyed by location, not by handler
        # alone: two files may define a `chat`, and only one of them is a
        # route (the pre-A.3b lookup matched on the bare name).
        self._entry_by_location: dict[tuple[str, str], str] = {}
        for ep in entry_points.entry_points:
            node_id = entry_node_by_handler.get(ep.handler)
            if node_id is not None:
                self._entry_by_location[(ep.file_path, ep.handler)] = node_id

        # (file, name) → the class a method sits in. TSA reports it; a
        # module-level function has none.
        self._class_by_location: dict[tuple[str, str], str] = {}
        self._symbol_locations: set[tuple[str, str]] = set()
        for symbol in tsa_output.symbols:
            self._symbol_locations.add((symbol.file_path, symbol.name))
            if symbol.class_name:
                key = (symbol.file_path, symbol.name)
                self._class_by_location[key] = symbol.class_name

        # The names the project defines, for the hop-1 rule: an agent name
        # handed to a project function is evidence, the same name handed
        # to a library call is a mention. A set, because the rule asks
        # "does the project define this name", not "where".
        self._symbol_names: set[str] = {
            name for _file, name in self._symbol_locations
        }

        self._files = files
        self._imports = entry_points.imports
        self._functions_for = functions_for
        # Module name → files. Memoised because the same few modules are
        # imported from hundreds of call sites and the match is a scan
        # over every manifest file.
        self._module_files: dict[str, list[str]] = {}

    @classmethod
    def for_context(
        cls, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "SymbolCallResolver":
        """Build the resolver for one normalizer run."""
        files = [
            entry.path for entry in ctx.manifest.files
            if entry.language == "python"
        ]
        return cls(
            nodes=delta.nodes,
            files=files,
            entry_points=ctx.entry_points,
            tsa_output=ctx.tsa_output,
            entry_node_by_handler=ctx.entry_node_by_handler,
            functions_for=ctx.functions_for,
        )

    # --------------------------------------------------------
    # The resolution
    # --------------------------------------------------------

    def resolve_all(self) -> list[ResolvedDelegation]:
        """Every symbol call that resolves to an agent node.

        Order follows `ctx.entry_points.symbol_calls`, which follows
        manifest order, so a run is reproducible.
        """
        resolved: list[ResolvedDelegation] = []
        for call in self._entry_points.symbol_calls:
            delegation = self.resolve(call)
            if delegation is not None:
                resolved.append(delegation)
        return resolved

    def resolve(self, call: SymbolCall) -> ResolvedDelegation | None:
        """Resolve one call site, or return None.

        No match is a normal outcome, not an error: most calls name no
        agent, and a call from a file with no agent node in it is not a
        delegation whatever it calls.
        """
        target_id, evidence = self._target_for(call)
        if target_id is None:
            return None
        source_id, source_kind = self._source_for(call)
        if source_id is None or source_id == target_id:
            return None
        return ResolvedDelegation(
            source_id=source_id,
            target_id=target_id,
            source_kind=source_kind,
            evidence=evidence,
        )

    def agent_for_mcp_usage(self, usage: MCPUsage) -> str | None:
        """The agent node an `MCPUsage` belongs to, or None.

        The enclosing class first — a file may hold several classes, and
        only one of them is the agent — then the file's agent node. Both
        are node lookups: the path of the file is not read as evidence
        (the pre-A.3b gate was `"agents" in file_path`).
        """
        class_name = self._class_by_location.get(
            (usage.file_path, usage.handler)
        )
        if class_name is not None:
            agent_id = self._agent_by_location.get(
                (class_name, usage.file_path)
            )
            if agent_id is not None:
                return agent_id
        return self.agent_node_by_file.get(usage.file_path)

    # --------------------------------------------------------
    # Source and target
    # --------------------------------------------------------

    def _source_for(self, call: SymbolCall) -> tuple[str | None, str]:
        """The node the call leaves from: an entry point or an agent."""
        entry_id = self._entry_by_location.get(
            (call.file_path, call.handler)
        )
        if entry_id is not None:
            return entry_id, "entry_point"
        agent_id = self.agent_node_by_file.get(call.file_path)
        if agent_id is not None:
            return agent_id, "agent"
        return None, ""

    def _target_for(self, call: SymbolCall) -> tuple[str | None, str]:
        """The agent node the call reaches, and the names that showed it."""
        callee_name = call.callee.split(".")[-1]
        if not callee_name:
            return None, ""

        # 1. An agent node in the caller's own file: the name is used
        #    there, so no import stands between the call and the node.
        local = self._agent_by_location.get((callee_name, call.file_path))
        if local is not None:
            return local, f"{call.handler} → {call.callee}"

        # 2. The name comes from another file. Follow the import to the
        #    file it names, and look there.
        for module in self._imports.get(call.file_path, ()):
            for other in self._files_for_module(module):
                if other == call.file_path:
                    continue
                # 2a. The imported name is itself the agent node: the
                #     call constructs (or calls into) the agent.
                imported = self._agent_by_location.get(
                    (callee_name, other)
                )
                if imported is not None:
                    return imported, f"{call.handler} → {module}.{callee_name}"
                # 2b. The imported name is a function the file defines.
                #     One hop into its body, and no further.
                if (other, callee_name) not in self._symbol_locations:
                    continue
                reached = self._agent_reached_from(other, callee_name)
                if reached is None:
                    continue
                reached_name = self._agent_name_by_id.get(reached, "")
                return reached, (
                    f"{call.handler} → {call.callee} → {reached_name}"
                )
        return None, ""


    def _files_for_module(self, module: str) -> list[str]:
        """Manifest files the dotted module name could be."""
        if module not in self._module_files:
            self._module_files[module] = [
                f for f in self._files if _module_matches_file(module, f)
            ]
        return self._module_files[module]

    def _imports_file(self, file_path: str, other: str) -> bool:
        """True if `file_path` imports a module that can be `other`.

        The import table is the evidence behind a name used in a body: a
        name that means an agent node in another file is only reachable
        from here if a module this file imports *is* that file.

        `_module_matches_file` is used directly rather than
        `_files_for_module`, which answers the same question the long way
        round — "which manifest files could this module be?" — and so
        needs the agent's file to be in the manifest to find it. The file
        is already known here, and it is the agent node's own
        `source_file`. The comparison is permissive by design
        (ADR-0009), which is why this asks about the import list and not
        about the name.
        """
        if not other:
            return False
        for module in self._imports.get(file_path, ()):
            if _module_matches_file(module, other):
                return True
        return False

    def _project_agent(self, name: str, defining_file: str) -> str | None:
        """The agent node `name` means, if `defining_file` imports it.

        The project-wide lookup is a name match across every file, so it
        needs the import table to stand behind it: the name may be an
        import inside the defining file (`from agents.orchestrator import
        OrchestratorAgent`, called by the runner that the route calls),
        and that is what is checked here. A name the file neither defines
        nor imports means nothing, however the node elsewhere is named.
        """
        agent_id = self.agent_node_by_name.get(name)
        if agent_id is None:
            return None
        if not self._imports_file(
            defining_file, self._agent_file_by_id.get(agent_id, "")
        ):
            return None
        return agent_id

    def _agent_reached_from(
        self, file_path: str, symbol_name: str
    ) -> str | None:
        """The agent node the symbol's body reaches, one hop away.

        A name the body invokes is matched against the agent nodes of the
        defining file first — the file defines the name, so no import
        stands between the two — then against the agent nodes of the
        project, and that second match is gated on the defining file's
        import table (`_project_agent`). Both are lookups on nodes that
        already exist: neither can make an agent, so neither can invent
        an edge on a project whose agents carry no capability evidence.

        A method of an agent counts as well, which is why the symbol's
        own enclosing class is checked last: `run_worker_agent` defined
        as a method of `WorkerAgent` links to `WorkerAgent`.
        """
        for reference in self._names_within_hops(file_path, symbol_name):
            agent_id = self._agent_by_location.get((reference, file_path))
            if agent_id is not None:
                return agent_id
            agent_id = self._project_agent(reference, file_path)
            if agent_id is not None:
                return agent_id

        class_name = self._class_by_location.get((file_path, symbol_name))
        if class_name is None:
            return None
        agent_id = self._agent_by_location.get((class_name, file_path))
        if agent_id is not None:
            return agent_id
        return self._project_agent(class_name, file_path)

    def _is_project_symbol(self, name: str) -> bool:
        """True if any file in the project defines `name`.

        Read by the hop-1 rule: a name handed to project code is
        evidence, the same name handed to a library call is a mention.
        """
        return name in self._symbol_names

    def _names_within_hops(
        self, file_path: str, symbol_name: str
    ) -> set[str]:
        """Names referenced by the symbol and by what it references.

        `MAX_REACHABILITY_HOPS` descents deep. Each descent is one hop
        through the call graph and the frontier is a set, so a cycle
        (`a` calls `b` calls `a`) terminates: a name is expanded once per
        hop at which it is reached, and the hop count is bounded.
        """
        functions = self._functions_for(file_path)
        names: set[str] = set()
        frontier = {symbol_name}
        for _ in range(MAX_REACHABILITY_HOPS):
            next_frontier: set[str] = set()
            for name in frontier:
                definition = _iter_definition(functions, name)
                if definition is None:
                    continue
                for reference in _referenced_names(
                    definition, self._is_project_symbol
                ):
                    names.add(reference)
                    next_frontier.add(reference)
            frontier = next_frontier
        return names



# ============================================================
# The stage
# ============================================================


class DelegationEdgeStage:
    """`delegates_to` (entry → agent, agent → agent) and agent → server."""

    name = "delegation_edges"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        resolver = SymbolCallResolver.for_context(delta, ctx)
        ctx.agent_node_by_name = resolver.agent_node_by_name
        ctx.agent_node_by_file = resolver.agent_node_by_file

        delegations = resolver.resolve_all()
        self._entry_to_agent(delta, ctx, delegations)
        self._agent_to_agent(delta, ctx, delegations)
        self._agent_to_mcp_server(delta, ctx, resolver)
        return delta

    @staticmethod
    def _entry_to_agent(
        delta: "GraphDelta",
        ctx: NormalizeContext,
        delegations: list[ResolvedDelegation],
    ) -> None:
        """A route handler that reaches an agent delegates to it."""
        for delegation in delegations:
            if delegation.source_kind != "entry_point":
                continue
            _append_delegation(delta, ctx, delegation)

    @staticmethod
    def _agent_to_agent(
        delta: "GraphDelta",
        ctx: NormalizeContext,
        delegations: list[ResolvedDelegation],
    ) -> None:
        """A call inside a file that holds an agent node is a delegation.

        The source is the file's agent node, because the call site names
        no agent and the AST cannot say which class a bare call sits in —
        a method of the agent, a helper next to it, the same evidence
        either way.
        """
        for delegation in delegations:
            if delegation.source_kind != "agent":
                continue
            _append_delegation(delta, ctx, delegation)

    @staticmethod
    def _agent_to_mcp_server(
        delta: "GraphDelta",
        ctx: NormalizeContext,
        resolver: SymbolCallResolver,
    ) -> None:
        for usage in ctx.entry_points.mcp_usages:
            agent_id = resolver.agent_for_mcp_usage(usage)
            server_id = ctx.mcp_servers.get(usage.mcp_server_name)
            if agent_id is None or server_id is None:
                continue
            delta.edges.append(Edge(
                id=edge_id(agent_id, server_id, "has_access_to"),
                source_id=agent_id,
                target_id=server_id,
                kind="has_access_to",
                host_id=ctx.host_id,
                # Agent → MCP server is reachability, not a call into a
                # sink (ADR-0015): there is no tool body and no sink
                # call to analyze, so data flow stays unconfirmed.
                **CONTROL_FLOW,
            ))


def _append_delegation(
    delta: "GraphDelta",
    ctx: NormalizeContext,
    delegation: ResolvedDelegation,
) -> None:
    """Append one `delegates_to` edge, carrying the names that resolved it."""
    delta.edges.append(Edge(
        id=edge_id(delegation.source_id, delegation.target_id, "delegates_to"),
        source_id=delegation.source_id,
        target_id=delegation.target_id,
        kind="delegates_to",
        host_id=ctx.host_id,
        # `evidence` is the hop path in names — never source text, and
        # never a name that was not first matched to an existing node.
        attributes={
            "resolved_via": "call_graph",
            "evidence": delegation.evidence,
        },
        **CONTROL_FLOW,
    ))

