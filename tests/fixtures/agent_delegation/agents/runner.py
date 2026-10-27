"""The runner module — where the one hop from a call site lands.

`run_worker_agent` names `WorkerAgent` in its own body, so a caller one
file away resolves to the agent by following its import and reading one
hop. `run_agent_with_retry` names `WorkerAgent` two hops away, which
`MAX_REACHABILITY_HOPS = 1` does not examine — the fixture keeps that
shape so the bound has something to hold back.

`WorkerAgent` calls `self.llm.invoke(...)`: `CALLS_LLM_RULE` matches the
receiver `llm`, not `llm_client` (BACKLOG B.10), so an agent body that
is to count has to spell the handle the way the rule reads it.
"""


class WorkerAgent:
    """A worker agent — an agent because its body calls an LLM."""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def process(self, plan: str) -> dict:
        summary = self.llm.invoke(f"Execute this plan: {plan}")
        return {"status": "ok", "summary": summary}


async def run_worker_agent(plan: str) -> dict:
    """One hop from the orchestrator: names the agent in its own body."""
    return await run_agent_with_retry(WorkerAgent, plan)


async def run_agent_with_retry(agent_class, plan: str) -> dict:
    """Two hops from the orchestrator: not examined."""
    return await agent_class().process(plan)
