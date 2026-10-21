"""CrossFileResolutionStage: `uses_tool` across an import."""

from __future__ import annotations

from aicorellator.graph.models import Node
from aicorellator.parsing.framework_detectors import DetectionResult
from aicorellator.parsing.normalizer_stages import CrossFileResolutionStage

AGENT = '''\
from tools import USER_TOOLS
from langchain.agents import create_agent


def get_agent():
    return create_agent(tools=USER_TOOLS)
'''

TOOLS = '''\
from langchain.tools import tool


@tool
def search(query: str) -> str:
    return ""
'''


def test_agent_module_links_to_imported_tools(project) -> None:
    """The agent in agent.py imports tools.py, so it can call its tools."""
    detection = DetectionResult(framework="langchain")
    detection.nodes = [
        Node(
            id="agent:1", kind="agent", name="create_agent",
            host_id="localhost",
            attributes={"source_file": "agent.py", "framework": "langchain"},
        ),
        Node(
            id="tool:1", kind="tool", name="search",
            host_id="localhost",
            attributes={"source_file": "tools.py", "framework": "langchain"},
        ),
    ]
    p = project(
        files={"agent.py": AGENT, "tools.py": TOOLS},
        detection=detection,
    )
    p.delta.nodes.extend(detection.nodes)

    delta = CrossFileResolutionStage().apply(p.delta, p.ctx)

    uses_tool = [e for e in delta.edges if e.kind == "uses_tool"]
    assert [(e.source_id, e.target_id) for e in uses_tool] == [
        ("agent:1", "tool:1"),
    ]
    assert uses_tool[0].attributes["resolved_via"] == "cross_file_import"
    assert uses_tool[0].flow_kind == "control"
