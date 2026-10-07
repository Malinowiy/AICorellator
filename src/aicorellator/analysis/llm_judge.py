import json
import os
import requests
from dotenv import load_dotenv
from pathlib import Path

from aicorellator.models import Edge

env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(env_path)

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
MODEL = "qwen3.6-35b-a3b"
#MODEL = "mistral-small-3.2-24b-instruct-2506-mlx"

def _auth_headers() -> dict[str, str]:
    """Builds the Authorization header if LM_API_TOKEN is set."""
    token = os.environ.get("LM_API_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def ask_llm(prompt: str, temperature: float = 0.1) -> str:
    """Sends a prompt to LM Studio and returns the text response."""
    response = requests.post(
        LM_STUDIO_URL,
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": 8192,
        },
        headers=_auth_headers(),
        timeout=900,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def parse_findings(raw: str) -> tuple[list[dict], str | None]:
    """Parses JSON from the LLM response; tolerant of markdown code fences.

    Returns (chains, error). error is None on success, otherwise a
    description of the failure — callers must surface it instead of
    treating a failed parse as "no findings".
    """
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [], f"invalid JSON from LLM: {e}"
    if not isinstance(data, dict):
        return [], f"expected a JSON object with a 'chains' key, got {type(data).__name__}"
    chains = data.get("chains", [])
    if not isinstance(chains, list):
        return [], f"'chains' must be a list, got {type(chains).__name__}"
    return chains, None


def verify_chain(chain: list[str], edges: list[Edge]) -> bool:
    if not isinstance(chain, list) or len(chain) < 3 or len(chain) % 2 == 0:
        return False
    for i in range(0, len(chain) - 2, 2):
        src, kind, dst = chain[i], chain[i + 1], chain[i + 2]
        # Прямое направление
        forward = any(
            e.source_id == src and e.kind == kind and e.target_id == dst
            for e in edges
        )
        # Обратное направление (для provides_tool, где граф хранит tool → agent)
        backward = any(
            e.source_id == dst and e.kind == kind and e.target_id == src
            for e in edges
        )
        if not (forward or backward):
            return False
    return True


def judge(graph_text: str, prompt_template: str, edges: list[Edge]) -> dict:
    """Full pipeline: prompt -> LLM -> parse -> verify.

    Returns {"findings": [...], "parse_error": None | str}.
    """
    prompt = prompt_template.format(graph_text=graph_text)
    raw = ask_llm(prompt)
    findings, parse_error = parse_findings(raw)

    verified = []
    for f in findings:
        chain = f.get("chain", []) if isinstance(f, dict) else []
        if chain and verify_chain(chain, edges):
            f["verified"] = True
            verified.append(f)
        elif chain:
            f["verified"] = False

    return {"findings": verified, "parse_error": parse_error}