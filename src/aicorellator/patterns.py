"""Patterns — a single registry of everything AICorellator knows about AI systems.

This module is the canonical source for:

  - Node and edge kinds (mirrors graph/schema.sql and graph/models.py)
  - File path segments that identify entity types (agents/, mcp/servers/, ...)
  - File discovery filters (excluded dirs, extensions, language map)
  - Class name suffixes and exact names (Agent, LLMClient, ...)
  - Method name prefixes and exact names (get_, update_, execute_script, ...)
  - HTTP/WebSocket route decorators
  - Function name hints (create_mcp_server, delegate_to_*, run_*_agent)
  - Config key patterns for LLM extraction (LLM_PROVIDER, OLLAMA_*, ...)
  - Provider hints from class names (openai, anthropic, ollama, ...)

Centralizing these gives a single overview of what the system can
recognize. Adding a new language, framework, or AI provider means
touching exactly one file.

Per-module logic (parsing, classification, matching) stays in the
respective modules. This file contains only data.
"""

from __future__ import annotations


# ============================================================
# Node kinds
# ============================================================
# Mirrors the CHECK constraint on nodes.kind in graph/schema.sql
# and VALID_NODE_KINDS in graph/models.py.

NODE_KINDS: frozenset[str] = frozenset({
    "agent",
    "llm_endpoint",
    "mcp_server",
    "tool",
    "credential",
    "sink",
    "entry_point",
    "guardrail",
})

# Mirrors the CHECK constraint on edges.kind in graph/schema.sql
# and VALID_EDGE_KINDS in graph/models.py.
# Note: reaches_sink and called_by are derived, not stored.
EDGE_KINDS: frozenset[str] = frozenset({
    "uses_model",
    "provides_tool",
    "delegates_to",
    "has_access_to",
    "guards",
})


# ============================================================
# File discovery — which files to read
# ============================================================

# Directories never descended into. Matched by name at any depth.
EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv", "venv", "env",
    "dist", "build", ".eggs",
    ".idea", ".vscode",
    ".ast-cache",
    ".tree-sitter-cache",
})

# File extensions we never read.
EXCLUDE_EXTENSIONS: frozenset[str] = frozenset({
    # Binaries and compiled artifacts
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".o", ".a",
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".bmp", ".tiff",
    # Audio / Video
    ".mp3", ".mp4", ".mov", ".avi", ".webm", ".wav",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    # Documents
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # Fonts
    ".woff", ".woff2", ".ttf", ".eot",
    # Databases
    ".db", ".sqlite", ".sqlite3",
    # Web templates and styles — not relevant for the AI graph
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    # Lock files — dependency pins, not source
    ".lock",
})

# Extension → language hint.
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    # Source code
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    # Shell — may contain curl to LLM, MCP startup
    ".sh": "shell",
    ".bash": "shell",
    # Migration templates
    ".mako": "mako",
    # Config / data — used by LLM Extractor
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".ini": "ini",
    ".env": "dotenv",
    ".sql": "sql",
    ".md": "markdown",
}

# Files without extension, matched by exact name.
FILENAME_LANGUAGE: dict[str, str] = {
    "Dockerfile": "dockerfile",
    "dockerfile": "dockerfile",
    "Makefile": "makefile",
    "makefile": "makefile",
    ".env": "dotenv",
    ".gitignore": "gitignore",
    ".dockerignore": "dockerignore",
    "README": "markdown",
}

# Hidden files we DO want to process, despite leading dot.
ALLOWED_HIDDEN_FILES: frozenset[str] = frozenset({
    ".env",
    ".gitignore",
    ".dockerignore",
})

# Max file size to hash and parse (10 MiB).
MAX_FILE_SIZE = 10 * 1024 * 1024


# ============================================================
# File path segments — where entity types live
# ============================================================

# Path segment → node kind. Matched by _has_path_segment().
PATH_SEGMENT_KINDS: tuple[tuple[str, str], ...] = (
    ("agents", "agent"),
    ("guardrails", "guardrail"),
    ("llm", "llm_endpoint"),       # core/llm/ — LLM clients
    ("tools", "tool"),             # tools/data/*.py
)

# Path segments for MCP server name extraction.
MCP_DIR_SEGMENT = "mcp"
MCP_SERVERS_SEGMENT = "servers"


# ============================================================
# Class name patterns
# ============================================================

# Class name suffix → node kind.
# Order matters: most specific first.
CLASS_SUFFIX_KINDS: tuple[tuple[str, str], ...] = (
    ("Orchestrator", "agent"),
    ("Agent", "agent"),
    ("LLMClient", "llm_endpoint"),
    ("LLMJudge", "llm_endpoint"),
    ("GuardrailHookService", "guardrail"),
    ("GuardrailService", "guardrail"),
    ("HookService", "guardrail"),
    ("Guardrail", "guardrail"),
    ("Server", "mcp_server"),      # only inside mcp/servers/
    ("Service", "mcp_server"),     # only inside mcp/servers/
)

# Class name suffixes explicitly excluded from classification.
EXCLUDED_CLASS_SUFFIXES: tuple[str, ...] = (
    "Detector",     # CTF detectors (RCEDetector, ...)
    "Evaluator",    # CTF evaluators
    "Schema",       # pydantic schemas
    "Result",       # internal result types
    "Error",        # exceptions
    "Exception",
    "Kind",         # HookKind
    "Envelope",     # HookEnvelope
    "Outcome",      # HookOutcome
    "Verdict",      # WebhookVerdict
)

# Base classes to exclude — abstract, not real components.
EXCLUDED_CLASSES: frozenset[str] = frozenset({
    "BaseAgent",
    "BaseDetector",
    "BaseEvaluator",
    "BaseRepository",
})

# Classes that are agents despite not ending in `*Agent`.
EXTRA_AGENT_CLASSES: frozenset[str] = frozenset({
    "CoPilotAssistant",
})

# Classes that are LLM clients despite not ending in `*Client`.
EXTRA_LLM_CLASSES: frozenset[str] = frozenset({
    "LLMJudge",
})

# Provider hints from class names (lowercase substring → provider).
# Checked in order.
PROVIDER_HINTS: tuple[tuple[str, str], ...] = (
    ("openai", "openai"),
    ("anthropic", "anthropic"),
    ("ollama", "ollama"),
    ("contextual", "contextual"),
    ("mock", "mock"),
    ("judge", "judge"),
)


# ============================================================
# Method name patterns
# ============================================================

# Method names that indicate a dangerous sink.
SINK_METHOD_NAMES: frozenset[str] = frozenset({
    "execute_script",
    "run_script",
    "run_command",
    "network_request",
    "upload_file",
    "delete_file",
    "download_file",
    "make_request",
    "send_request",
})

# Sink category by substring in method name.
# Checked in order.
SINK_CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("script", "code_execution"),
    ("command", "code_execution"),
    ("network", "network"),
    ("request", "network"),
    ("file", "filesystem"),
)

# Method name prefixes that indicate a tool (inside tools/ or MCP servers).
TOOL_METHOD_PREFIXES: tuple[str, ...] = (
    "get_", "update_", "create_", "delete_", "process_",
    "handle_", "call_", "fetch_", "send_", "list_",
)

# Method names to skip even if they look like tools.
SKIP_METHOD_NAMES: frozenset[str] = frozenset({
    "to_dict",
    "to_summary_dict",
    "to_json",
    "to_yaml",
})


# ============================================================
# Route decorators
# ============================================================

# HTTP methods recognized as route decorators.
HTTP_METHODS: frozenset[str] = frozenset({
    "get", "post", "put", "delete", "patch", "options", "head",
})

# WebSocket decorator attribute name.
WEBSOCKET_METHOD = "websocket"


# ============================================================
# Function name hints
# ============================================================

# Function name that creates an MCP client connection.
MCP_CONNECT_FUNCTION = "create_mcp_server"

# Prefix for orchestrator delegation methods.
DELEGATE_METHOD_PREFIX = "delegate_to_"

# Runner function name → agent class name.
RUNNER_TO_AGENT: dict[str, str] = {
    "run_orchestrator_agent": "OrchestratorAgent",
    "run_invoice_agent": "InvoiceAgent",
    "run_payments_agent": "PaymentsAgent",
    "run_fraud_agent": "FraudComplianceAgent",
    "run_onboarding_agent": "VendorOnboardingAgent",
    "run_communication_agent": "CommunicationAgent",
}


# ============================================================
# LLM config key patterns
# ============================================================
# These describe what LLM configuration keys look like in
# .env / .py / .yaml / .toml / .json files.

# Exact keys that signal an LLM provider.
PROVIDER_KEYS: frozenset[str] = frozenset({
    "LLM_PROVIDER",
    "LLM_DEFAULT_PROVIDER",
})

# Exact keys that signal a default model.
MODEL_KEYS: frozenset[str] = frozenset({
    "LLM_DEFAULT_MODEL",
    "LLM_MODEL",
    "LLM_MODEL_NAME",
    "OPENAI_MODEL",
    "ANTHROPIC_MODEL",
    "OLLAMA_MODEL",
    "DEFAULT_MODEL",
})

# Exact keys that signal a base URL / endpoint.
BASE_URL_KEYS: frozenset[str] = frozenset({
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

# Suffixes that signal a model key (any provider).
MODEL_KEY_SUFFIXES: tuple[str, ...] = (
    "_MODEL",
    "_MODEL_NAME",
)

# Suffixes that signal a base URL key (any provider).
BASE_URL_KEY_SUFFIXES: tuple[str, ...] = (
    "_BASE_URL",
    "_API_BASE",
    "_ENDPOINT",
)

# Key name → provider. Used to infer provider from key alone.
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


# ============================================================
# Language support
# ============================================================

# Languages TSA can parse with --structure.
TSA_SUPPORTED_LANGUAGES: frozenset[str] = frozenset({
    "python", "typescript", "javascript",
    "go", "java", "kotlin", "rust",
    "c", "cpp", "csharp", "ruby", "php", "swift",
})