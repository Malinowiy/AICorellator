"""EntryPointStage — `entry_point` nodes.

Run order: 5 of 20 (see `normalizer_stages.STAGES`).

C4: "**Entry point nodes** — from the entry points detector, including
HTTP, CLI, and Streamlit."

The id derivation differs per method because the identity differs: an
HTTP route is `(file, method, path)` — or `(file, method, handler)` when
the route has no path, which is every Django route, whose pattern lives
in `urls.py` (`normalizer_helpers.entry_point_id` states it) — a CLI entry
is the file itself (one per script), and a Streamlit entry is
`(file, call)`. The `entry_kind` attribute records which, so downstream
consumers can tell an MCP entry from a CLI one without re-deriving it.

A path-less route's **name** is `"<METHOD> <handler>"` for the same
reason: `"POST "` names nothing, and a Django route is named by the view
that handles it, not by a path the AST never held.

Side effects: fills `ctx.entry_node_by_handler` (handler → node id),
which the delegation and reachability stages read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Node
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import (
    HOST_ID,
    cli_entry_point_id,
    entry_point_id,
    streamlit_entry_point_id,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class EntryPointStage:
    """One node per detected entry point."""

    name = "entry_points"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        for ep in ctx.entry_points.entry_points:
            if ep.method == "CLI":
                node_id = cli_entry_point_id(ep.file_path)
                attrs = {
                    "source_file": ep.file_path,
                    "line_start": ep.line,
                    "method": "CLI",
                    "entry_kind": "cli",
                    "path": ep.path,
                }
                name = f"CLI:{ep.file_path}"
            elif ep.method == "STREAMLIT":
                node_id = streamlit_entry_point_id(ep.file_path, ep.path)
                attrs = {
                    "source_file": ep.file_path,
                    "line_start": ep.line,
                    "method": "STREAMLIT",
                    "entry_kind": "streamlit",
                    "path": ep.path,
                }
                name = f"Streamlit:{ep.file_path}:{ep.path}"
            else:
                node_id = entry_point_id(ep)
                attrs = {
                    "source_file": ep.file_path,
                    "line_start": ep.line,
                    "method": ep.method,
                    "path": ep.path,
                    "handler": ep.handler,
                }
                name = f"{ep.method} {ep.path or ep.handler}"

            ctx.entry_node_by_handler[ep.handler] = node_id
            delta.nodes.append(Node(
                id=node_id,
                kind="entry_point",
                name=name,
                host_id=HOST_ID,
                attributes=attrs,
            ))
        return delta
