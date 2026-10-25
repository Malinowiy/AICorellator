"""DetectorMergeStage: detector nodes authoritative, ids remapped."""

from __future__ import annotations

from aicorellator.graph.models import Edge, Node
from aicorellator.parsing.framework_detectors import DetectionResult
from aicorellator.parsing.normalizer_helpers import edge_id
from aicorellator.parsing.normalizer_stages import DetectorMergeStage


def test_detector_node_replaces_tsa_node_and_repoints_edges(project) -> None:
    """Same (name, file) → one node, capabilities kept, edge re-pointed."""
    tsa_tool = Node(
        id="tool:tsa", kind="tool", name="search",
        host_id="localhost",
        attributes={"source_file": "server.py", "line_start": 10},
        capabilities=["executes_code"],
    )
    sink = Node(
        id="sink:1", kind="sink", name="delete_file",
        host_id="localhost", attributes={"source_file": "server.py"},
    )
    detector_tool = Node(
        id="tool:detector", kind="tool", name="search",
        host_id="localhost",
        attributes={"source_file": "server.py", "parent_server": "srv"},
    )
    detection = DetectionResult(framework="fastmcp")
    detection.nodes = [detector_tool]
    # The detector's own edge carries the id the re-pointed TSA edge will
    # compute, which is the case the dedup exists for.
    detection.edges = [Edge(
        id=edge_id("tool:detector", "sink:1", "has_access_to"),
        source_id="tool:detector", target_id="sink:1",
        kind="has_access_to", host_id="localhost",
    )]

    p = project(files={"server.py": ""}, detection=detection)
    p.delta.nodes.extend([tsa_tool, sink])
    p.delta.edges.append(Edge(
        id="edge:tsa", source_id="tool:tsa", target_id="sink:1",
        kind="has_access_to", host_id="localhost",
        data_flow_confirmed="parameter_match", flow_kind="data",
        # The rebuild must not carry the verdict: no AST edge has one
        # (ADR-0016). `flow_kind` is re-derived, so it is safe to copy.
    ))

    delta = DetectorMergeStage().apply(p.delta, p.ctx)

    tools = [n for n in delta.nodes if n.kind == "tool"]
    assert len(tools) == 1
    assert tools[0].id == "tool:detector"
    # Capabilities survive the replacement.
    assert tools[0].capabilities == ["executes_code"]
    # The edge follows the node, not the old id.
    access = [e for e in delta.edges if e.kind == "has_access_to"]
    assert len(access) == 1
    assert access[0].source_id == "tool:detector"
    assert access[0].data_flow_confirmed is None
    assert access[0].flow_kind == "control"
