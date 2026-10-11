"""LLM client — OpenAI-compatible HTTP client for LMStudio / Ollama / vLLM.

No external LLM API. All calls go to localhost or a configurable
local endpoint. The endpoint must speak the OpenAI Chat Completions
API (LMStudio, Ollama, vLLM, llama.cpp all do).

Response parsing:
  - Non-JSON output: raises LLMResponseError.
  - Missing required fields: raises LLMResponseError.
  - Timeout: returns None and logs a warning.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import requests


log = logging.getLogger(__name__)


class LLMResponseError(Exception):
    """Raised when the LLM returns malformed output."""


@dataclass
class LLMRequest:
    """One completion request."""

    model: str
    system: str
    user: str
    temperature: float = 0.0          # deterministic for security analysis
    max_tokens: int = 8000
    timeout: int = 800                # seconds
    reasoning_effort: str | None = None


@dataclass
class LLMResponse:
    """One completion response."""

    text: str                          # raw assistant message content
    model: str                         # model that answered
    finish_reason: str | None = None   # "stop" | "length" | None
    usage: dict[str, Any] | None = None


def complete(
    base_url: str,
    request: LLMRequest,
    api_key: str = "not-needed",
) -> LLMResponse | None:
    """Send a chat completion request to an OpenAI-compatible endpoint.

    Args:
        base_url: e.g. "http://localhost:1234/v1"
        request: the request payload
        api_key: LMStudio and Ollama ignore this; kept for compatibility

    Returns:
        LLMResponse on success, None on timeout or transport error.
        Parse errors raise LLMResponseError (they indicate a bad model
        configuration, not a transient failure).
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": request.model,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "stream": False,
    }
    if request.reasoning_effort is not None:
        payload["reasoning_effort"] = request.reasoning_effort
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=request.timeout,
        )
    except requests.Timeout:
        log.warning("LLM timeout on %s after %ds", base_url, request.timeout)
        return None
    except requests.RequestException as e:
        log.error("LLM request failed: %s", e)
        return None

    if response.status_code != 200:
        log.error(
            "LLM HTTP %d: %s",
            response.status_code,
            response.text[:200],
        )
        return None

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise LLMResponseError(f"non-JSON HTTP response: {e}") from e

    return _parse_completion(data)


def _parse_completion(data: dict) -> LLMResponse:
    """Extract the assistant message from an OpenAI-compatible response."""
    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        raise LLMResponseError("response missing 'choices'")

    first = choices[0]
    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMResponseError("response missing 'message'")

    content = message.get("content")
    if not isinstance(content, str):
        raise LLMResponseError("response 'content' is not a string")

    return LLMResponse(
        text=content,
        model=data.get("model", ""),
        finish_reason=first.get("finish_reason"),
        usage=data.get("usage"),
    )


def parse_json_response(text: str) -> dict:
    """Parse the assistant's text as JSON.

    Handles common cases:
      - clean JSON: {"key": "value"}
      - JSON wrapped in markdown code fences: ```json\n{...}\n```
      - JSON with surrounding prose: "Here is the answer: {...}"

    Raises LLMResponseError if nothing parseable is found.
    """
    text = text.strip()

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        # remove opening fence (may be ```json or just ```)
        lines = lines[1:]
        # remove closing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: find first '{' and last '}'
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise LLMResponseError(
        f"could not extract JSON from response (first 200 chars): {text[:200]!r}"
    )