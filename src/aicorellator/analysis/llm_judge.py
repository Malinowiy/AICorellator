import json
import os
import requests
from dotenv import load_dotenv
from pathlib import Path

from aicorellator.models import Edge

env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv()

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
MODEL = "qwen/qwen3.8-27b"
LM_API_TOKEN = os.environ["LM_API_TOKEN"] 

print(f"TOKEN PREFIX: {LM_API_TOKEN[:15]}")
print(f"URL: {LM_STUDIO_URL}")
print(f"MODEL: {MODEL}")


def ask_llm(prompt: str, temperature: float = 0.1) -> str:
    """Отправляет промпт в LM Studio, возвращает текстовый ответ."""
    response = requests.post(
        LM_STUDIO_URL,
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": 4096,
        },
        timeout=900,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def parse_findings(raw: str) -> list[dict]:
    """Парсит JSON из ответа LLM. Устойчив к markdown-обёрткам."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        data = json.loads(raw)
        return data.get("chains", [])
    except json.JSONDecodeError:
        return []


def verify_chain(chain: list[str], edges: list[Edge]) -> bool:
    """Проверяет, что каждое ребро из chain реально существует в графе."""
    for i in range(0, len(chain) - 2, 2):
        src, kind, dst = chain[i], chain[i + 1], chain[i + 2]
        if not any(
            e.source_id == src and e.kind == kind and e.target_id == dst
            for e in edges
        ):
            return False
    return True


def judge(graph_text: str, prompt_template: str, edges: list[Edge]) -> list[dict]:
    """Полный цикл: промпт → LLM → парсинг → верификация."""
    prompt = prompt_template.format(graph_text=graph_text)
    raw = ask_llm(prompt)
    findings = parse_findings(raw)

    verified = []
    for f in findings:
        chain = f.get("chain", [])
        if chain and verify_chain(chain, edges):
            f["verified"] = True
            verified.append(f)
        elif chain:
            f["verified"] = False

    return verified