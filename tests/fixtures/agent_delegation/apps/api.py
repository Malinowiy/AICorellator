"""The HTTP API — the entry point of the fixture.

The handler calls `run_orchestrator_agent`, imported from
`agents/orchestrator.py`. The edge to the agent is not the name of the
runner: it is the import followed to the file that defines it, and the
agent named in that runner's body (ADR-0016, decision 7).
"""

from fastapi import APIRouter

from agents.orchestrator import run_orchestrator_agent

router = APIRouter()


@router.post("/tasks")
async def create_task(request):
    """Hand a task to the orchestrator."""
    return await run_orchestrator_agent(request)
