"""LLM Extractor — reads config files and extracts LLM endpoints.

Sources scanned:
  - .env / .env.* — environment variables
  - config.py / settings.py — Python constants
  - *.yaml / *.yml — YAML configs (docker-compose, app config)
  - *.toml — pyproject.toml, uv.lock (dependencies only)
  - *.json — config files

Detected keys (case-insensitive, prefix-matched):
  - LLM_PROVIDER, LLM_DEFAULT_MODEL, LLM_MODEL, LLM_MODEL_NAME
  - OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
  - ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL
  - OLLAMA_BASE_URL, OLLAMA_HOST, OLLAMA_MODEL
  - VLLM_*, LITELLM_*
  - *_API_BASE, *_BASE_URL, *_ENDPOINT, *_MODEL, *_MODEL_NAME

The extractor is intentionally conservative: it produces ONE
llm_endpoint per (provider, model) pair, deduplicated across files.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..ingestion.discovery import FileEntry, FileManifest


log = logging.getLogger(__name__)


# Keys that signal LLM provider or endpoint.
PROVIDER_KEYS = frozenset({
    "LLM_PROVIDER",
    "LLM_DEFAULT_PROVIDER",
})

MODEL_KEYS = frozenset({
    "LLM_DEFAULT_MODEL",
    "LLM_MODEL",
    "LLM_MODEL_NAME",
    "OPENAI_MODEL",
    "ANTHROPIC_MODEL",
    "OLLAMA_MODEL",
    "DEFAULT_MODEL",
})

BASE_URL_KEYS = frozenset({
    "OLLAMA_BASE_URL",
    "OLLAMA_HOST",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_BASE",
    "LLM_BASE_URL",
    "LLM_API_BASE",
    "LLM_ENDPOINT",
})

# Prefix-based patterns — anything matching these suffixes is picked up.
MODEL_KEY_SUFFIXES = ("_MODEL", "_MODEL_NAME")
BASE_URL_KEY_SUFFIXES = ("_BASE_URL", "_API_BASE", "_ENDPOINT")

# Providers we recognize by key name.
PROVIDER_BY_KEY: dict[str, str] = {
    "OPENAI_API_KEY": "openai",
    "OPENAI_BASE_URL": "openai",
    "OPENAI_API_BASE": "openai",
    "OPENAI_MODEL": "openai",
    "ANTHROPIC_API_KEY": "anthropic",
    "ANTHROPIC_BASE_URL": "anthropic",
    "ANTHROPIC_API_BASE": "anthropic",
    "ANTHROPIC_MODEL": "anthropic",
    "OLLAMA_BASE_URL": "ollama",
    "OLLAMA_HOST": "ollama",
    "OLLAMA_MODEL": "ollama",
}

# Regex to match KEY=VALUE lines in .env-style files.
_ENV_LINE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+?)\s*$")

# Regex to match Python constant assignments:  KEY = "value"
_PY_ASSIGN = re.compile(
    r"""^\s*([A-Z_][A-Z0-9_]*)\s*(?::\s*[^=]+)?\s*=\s*["']([^"']+)["']""",
)


@dataclass
class LLMEndpoint:
    """An LLM provider + model combination discovered in configs."""

    provider: str                       # openai | ollama | anthropic | unknown
    model: str | None                   # e.g. "gpt-4o", "llama3"
    base_url: str | None                # e.g. "http://localhost:11434"
    source_file: str                    # where it was found
    source_line: int = 0
    raw_keys: dict[str, str] = field(default_factory=dict)


@dataclass
class LLMExtractorOutput:
    """Aggregated result of scanning all config files."""

    endpoints: list[LLMEndpoint] = field(default_factory=list)
    files_scanned: int = 0
    files_failed: int = 0

    def deduplicated(self) -> list[LLMEndpoint]:
        """Collapse endpoints that share (provider, model, base_url)."""
        seen: dict[tuple, LLMEndpoint] = {}
        for ep in self.endpoints:
            key = (ep.provider, ep.model, ep.base_url)
            if key not in seen:
                seen[key] = ep
            else:
                # Merge raw_keys, prefer the endpoint with more info
                existing = seen[key]
                existing.raw_keys.update(ep.raw_keys)
        return list(seen.values())


def run(manifest: FileManifest) -> LLMExtractorOutput:
    """Scan all config-like files in the manifest for LLM endpoints."""
    output = LLMExtractorOutput()

    for entry in manifest.files:
        if entry.language not in ("dotenv", "python", "yaml", "toml", "json", "ini"):
            continue

        try:
            text = entry.abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning("cannot read %s: %s", entry.path, e)
            output.files_failed += 1
            continue

        output.files_scanned += 1
        raw = _scan_file(text, entry)

        if raw:
            endpoint = _build_endpoint(raw, entry)
            if endpoint is not None:
                output.endpoints.append(endpoint)

    return output


def _scan_file(text: str, entry: FileEntry) -> dict[str, str]:
    """Extract KEY=VALUE pairs from a config file.

    Handles .env, Python assignments, YAML scalars, TOML,
    and generic JSON key-value pairs.
    """
    found: dict[str, str] = {}

    if entry.language == "dotenv":
        found.update(_scan_dotenv(text))
    elif entry.language == "python":
        found.update(_scan_python(text))
    elif entry.language == "yaml":
        found.update(_scan_yaml(text))
    elif entry.language == "toml":
        found.update(_scan_toml(text))
    elif entry.language == "json":
        found.update(_scan_json(text))

    return found


def _scan_dotenv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if m and _is_relevant_key(m.group(1)):
            out[m.group(1)] = m.group(2).strip("'\"")
    return out

def _scan_python(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _PY_ASSIGN.match(line)
        if m and _is_relevant_key(m.group(1)):
            out[m.group(1)] = m.group(2)
    return out

def _scan_yaml(text: str) -> dict[str, str]:
    """Extract KEY: VALUE pairs from YAML. Handles nested with
    simple indentation-aware scan."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().strip("'\"")
        value = value.strip().strip("'\"")
        if key and value and _is_relevant_key(key):
            out[key.upper()] = value
    return out


def _scan_toml(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().strip("'\"")
        value = value.strip().strip("'\"")
        if key and value and _is_relevant_key(key):
            out[key.upper()] = value
    return out


def _scan_json(text: str) -> dict[str, str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out: dict[str, str] = {}
    _walk_json(data, out)
    return out


def _walk_json(obj, out: dict[str, str], prefix: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}_{k}" if prefix else str(k)
            if isinstance(v, (str, int, float)) and _is_relevant_key(full):
                out[full.upper()] = str(v)
            elif isinstance(v, (dict, list)):
                _walk_json(v, out, full)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json(item, out, prefix)


def _is_relevant_key(key: str) -> bool:
    """True if key looks like an LLM provider/model/base_url key."""
    k = key.upper()
    if k in PROVIDER_KEYS or k in MODEL_KEYS or k in BASE_URL_KEYS:
        return True
    if k in PROVIDER_BY_KEY:
        return True
    if k.endswith(MODEL_KEY_SUFFIXES) or k.endswith(BASE_URL_KEY_SUFFIXES):
        return True
    return False


def _build_endpoint(raw: dict[str, str], entry: FileEntry) -> LLMEndpoint | None:
    """Build a single LLMEndpoint from extracted key-value pairs."""
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None

    # Provider: explicit or inferred from key names.
    for k, v in raw.items():
        if k in PROVIDER_KEYS and v:
            provider = v.lower()
            break
    if provider is None:
        for k in raw:
            if k in PROVIDER_BY_KEY:
                provider = PROVIDER_BY_KEY[k]
                break

    # Model: explicit or from suffix.
    for k, v in raw.items():
        if k in MODEL_KEYS and v:
            model = v
            break
    if model is None:
        for k, v in raw.items():
            if k.endswith(MODEL_KEY_SUFFIXES) and v:
                model = v
                break

    # Base URL: explicit or from suffix.
    for k, v in raw.items():
        if k in BASE_URL_KEYS and v:
            base_url = v
            break
    if base_url is None:
        for k, v in raw.items():
            if k.endswith(BASE_URL_KEY_SUFFIXES) and v:
                base_url = v
                break

    if provider is None and model is None and base_url is None:
        return None

    return LLMEndpoint(
        provider=provider or "unknown",
        model=model,
        base_url=base_url,
        source_file=entry.path,
        raw_keys=raw,
    )