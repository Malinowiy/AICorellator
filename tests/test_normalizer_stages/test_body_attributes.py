"""BodyAttributeStage: `returns_data` and `sink_data_flow` backfill."""

from __future__ import annotations

import ast

from aicorellator.graph.models import Node
from aicorellator.parsing.normalizer_stages import BodyAttributeStage

SOURCE = '''\
import os


def read_file(query):
    return search(query)


def delete_file(file_id):
    os.remove(file_id)
'''


def _line_of(name: str) -> int:
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node.lineno
    raise AssertionError(name)


def _node(kind: str, name: str) -> Node:
    return Node(
        id=f"{kind}:{name}", kind=kind, name=name, host_id="localhost",
        attributes={"source_file": "server.py", "line_start": _line_of(name)},
    )


def test_tool_and_sink_get_their_body_attributes(project) -> None:
    """A tool records whether it returns data; a sink how it reaches a call."""
    p = project(files={"server.py": SOURCE})
    tool = _node("tool", "read_file")
    sink = _node("sink", "delete_file")
    p.delta.nodes.extend([tool, sink])

    delta = BodyAttributeStage().apply(p.delta, p.ctx)

    assert tool.attributes["returns_data"] is True
    assert sink.attributes["sink_data_flow"] == "parameter_match"
    assert delta.nodes == [tool, sink]


def test_existing_attributes_are_not_overwritten(project) -> None:
    """A detector-enriched node keeps what the detector decided."""
    p = project(files={"server.py": SOURCE})
    tool = _node("tool", "read_file")
    tool.attributes["returns_data"] = False
    p.delta.nodes.append(tool)

    BodyAttributeStage().apply(p.delta, p.ctx)

    assert tool.attributes["returns_data"] is False
