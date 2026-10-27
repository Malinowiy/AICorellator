"""The orchestrator agent — delegates to a worker agent.

Two things make the delegations in this fixture resolvable, and neither
is a name (ADR-0016, decision 7):

- `OrchestratorAgent` is an agent because its body calls an LLM
  (`self.llm.invoke(...)` — `CALLS_LLM_RULE` matches the receiver `llm`).
- The route in `apps/api.py` calls `run_orchestrator_agent`, defined
  here, and that runner's body names `OrchestratorAgent`: one hop.
"""

from agents.runner import run_worker_agent


class OrchestratorAgent:
    """Routes a task to a worker agent."""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def handle(self, task: str) -> dict:
        """`self.llm.invoke` → `calls_llm`: the agent trigger."""
        plan = self.llm.invoke(f"Plan this task: {task}")
        return await self.delegate_to_worker(plan)

    async def delegate_to_worker(self, plan: str) -> dict:
        """The delegation: a runner imported from `agents/runner.py`."""
        return await run_worker_agent(plan)


async def run_orchestrator_agent(request: str) -> dict:
    """The name the route calls; the body names the agent."""
    return await OrchestratorAgent().handle(request)
