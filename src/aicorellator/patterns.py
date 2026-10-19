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
    "uses_tool",
    "provides_tool",
    "delegates_to",
    "has_access_to",
    "guards",
})

# Mirrors the CHECK constraint on edges.flow_kind in graph/schema.sql
# and VALID_FLOW_KINDS in graph/models.py (ADR-0015).
#   control    — X calls Y, or X can reach Y through calls.
#   data       — data from X reaches Y (data flow confirmed).
#   structural — a declaration, not a path (guards).
FLOW_KINDS: frozenset[str] = frozenset({"control", "data", "structural"})

# Edge kinds that describe declarations, not execution paths (ADR-0015).
# These carry flow_kind = "structural" and are excluded from find_chains.
#
# `provides_tool` is deliberately NOT here. From an attack-path
# standpoint, "the agent can call the tool" is equivalent to "the agent
# calls the tool under attack": the access path is the candidate attack
# path until runtime monitoring (Phase 2) can tell the two apart.
STRUCTURAL_EDGE_KINDS: frozenset[str] = frozenset({"guards"})

# Edge kinds that carry flow_kind = "control" and data_flow_confirmed
# = "unknown" unless a framework detector enriches them (ADR-0015).
CONTROL_EDGE_KINDS: frozenset[str] = frozenset({
    "uses_model", "uses_tool", "provides_tool", "delegates_to",
})

# Flow kinds that find_chains traverses (ADR-0015, decision 5).
# `structural` edges are declarations, not paths: a guardrail "guards"
# an action but is not something execution flows along. Skipping them
# is a semantic rule of the traversal, not a caller-facing filter
# option.
TRAVERSABLE_FLOW_KINDS: frozenset[str] = frozenset({"control", "data"})


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
# Capabilities (ADR-0009)
# ============================================================
# Capabilities are derived from AST calls, not from names.
# Each entry maps a capability to the call patterns that trigger
# it. Patterns are matched against:
#   - dotted call names:  subprocess.run, os.system, open
#   - attribute access:   request.body, cursor.execute
#
# Matching is intentionally simple — false positives are accepted
# (see ADR-0009). The LLM in Phase 3 filters noise.

# --- Node capabilities ---

CAPABILITY_EXECUTES_CODE: tuple[str, ...] = (
    "subprocess.run",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.check_output",
    "subprocess.check_call",
    "os.system",
    "os.popen",
    "os.execv",
    "os.execve",
    "os.spawnv",
    "eval",
    "exec",
    "compile",
    "pickle.loads",
    "marshal.loads",
    "yaml.unsafe_load",
)

# Calls that ALWAYS execute a shell — no keyword required.
CAPABILITY_EXECUTES_SHELL_IMPLICIT: tuple[str, ...] = (
    "os.system",
    "os.popen",
)

# Calls that execute a shell only when shell=True is passed.
CAPABILITY_EXECUTES_SHELL_EXPLICIT: tuple[str, ...] = (
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
)

# Union — for tests that just want "any shell capability".
CAPABILITY_EXECUTES_SHELL: tuple[str, ...] = (
    CAPABILITY_EXECUTES_SHELL_IMPLICIT + CAPABILITY_EXECUTES_SHELL_EXPLICIT
)

CAPABILITY_READS_FILESYSTEM: tuple[str, ...] = (
    "open",
    "io.open",
    "pathlib.Path.read_text",
    "pathlib.Path.read_bytes",
    "pathlib.Path.open",
    "os.read",
    "shutil.copy",
    "shutil.copytree",
)

CAPABILITY_WRITES_FILESYSTEM: tuple[str, ...] = (
    "pathlib.Path.write_text",
    "pathlib.Path.write_bytes",
    "os.write",
    "shutil.move",
    "shutil.rmtree",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.makedirs",
)

CAPABILITY_READS_DB: tuple[str, ...] = (
    "cursor.execute",       # requires SQL prefix check
    "connection.execute",
    "session.execute",
    "session.query",
    "engine.execute",
    "text",                 # SQLAlchemy
)

CAPABILITY_WRITES_DB: tuple[str, ...] = (
    # Same call sites as reads_db; the distinction is the SQL
    # keyword (INSERT / UPDATE / DELETE / DROP). The extractor
    # inspects the first string argument.
    "cursor.execute",
    "connection.execute",
    "session.execute",
)

CAPABILITY_HTTP_EGRESS: tuple[str, ...] = (
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.delete",
    "requests.head",
    "requests.request",
    "httpx.get",
    "httpx.post",
    "httpx.put",
    "httpx.delete",
    "httpx.request",
    "aiohttp.ClientSession",
    "urllib.request.urlopen",
    "urllib.request.Request",
)

CAPABILITY_HTTP_INGRESS: tuple[str, ...] = (
    # Entry points — detected by the entry-point detector, not by
    # the capability extractor. Listed here for completeness.
    # The extractor does not flag route decorators as a capability.
)

CAPABILITY_READS_ENV: tuple[str, ...] = (
    "os.environ",
    "os.getenv",
    "os.environ.get",
    "os.environb",
)

CAPABILITY_CALLS_LLM: tuple[str, ...] = (
    "openai.chat.completions.create",
    "openai.Completion.create",
    "anthropic.messages.create",
    "ollama.chat",
    "ollama.generate",
    "litellm.completion",
    "litellm.acompletion",
    "langchain.chat_models.ChatOpenAI",
    "langchain.llms.Ollama",
    "llama_cpp.Llama",
    "vllm.LLM",
)

CAPABILITY_INVOKES_MCP_TOOL: tuple[str, ...] = (
    "session.call_tool",
    "client.call_tool",
    "mcp_client.call_tool",
    "server.call_tool",
    "fastmcp.client.call_tool",
)

# --- Secret-like env variable names ---
# Used to distinguish reads_env from reads_secrets.

SECRET_ENV_KEY_SUBSTRINGS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "API_KEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
)

# --- SQL keyword → capability ---

SQL_READ_KEYWORDS: tuple[str, ...] = (
    "SELECT",
    "PRAGMA",
    "EXPLAIN",
)

SQL_WRITE_KEYWORDS: tuple[str, ...] = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "REPLACE",
)

# --- Lethal trifecta combinations (ADR-0009) ---

LETHAL_TRIFECTA_READ_CAPS: frozenset[str] = frozenset({
    "reads_untrusted",
})

LETHAL_TRIFECTA_SECRET_CAPS: frozenset[str] = frozenset({
    "reads_secrets",
    "reads_env",
})

LETHAL_TRIFECTA_EGRESS_CAPS: frozenset[str] = frozenset({
    "http_egress",
    "writes_filesystem",
    "executes_shell",
    "executes_code",
})

# --- Edge-level capabilities (ADR-0009) ---

EDGE_CAPABILITY_DATA_FLOWS_FORWARD = "data_flows_forward"
EDGE_CAPABILITY_CONTROL_FLOWS_FORWARD = "control_flows_forward"
EDGE_CAPABILITY_UNTRUSTED_PROPAGATES = "untrusted_propagates"

# ============================================================
# Capability sets for classification and chain enumeration
# ============================================================
# These are data, not logic. classification.py uses them to
# decide what kind a symbol is; query.py uses SINK_CAPABILITIES
# to decide which nodes terminate a chain (ADR-0009).
#
# Keeping them here — not in classification.py — means there is
# a single source of truth. Adding a new capability means
# touching this file only, not two.

# Capabilities that make a symbol a sink, regardless of name.
# A node of any kind that exercises one of these is a valid
# chain endpoint for find_chains.
SINK_CAPABILITIES: frozenset[str] = frozenset({
    "executes_code",
    "executes_shell",
    "writes_filesystem",
    "writes_db",
    "http_egress",
})

# Capabilities that make a symbol an agent, regardless of name.
AGENT_CAPABILITIES: frozenset[str] = frozenset({
    "calls_llm",
    "invokes_mcp_tool",
})

# Capabilities that make a symbol an LLM endpoint, regardless of name.
LLM_ENDPOINT_CAPABILITIES: frozenset[str] = frozenset({
    "calls_llm",
})

# Capabilities that make a symbol a tool, regardless of name.
TOOL_CAPABILITIES: frozenset[str] = frozenset({
    "invokes_mcp_tool",
})

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
# Model name filters
# ============================================================

# Substrings that identify non-chat models. Any model whose ID
# contains one of these (case-insensitive) is excluded from the
# voter list — embeddings and similar models do not respond to
# chat completions and would time out.
EXCLUDED_MODEL_SUBSTRINGS: tuple[str, ...] = (
    "embedding",
    "embed-",
    "-embed",
    "reranker",
    "rerank",
    "whisper",
    "tts",
    "audio",
    "vision-only",
)

# ============================================================
# Language support
# ============================================================

# Languages TSA can parse with --structure.
TSA_SUPPORTED_LANGUAGES: frozenset[str] = frozenset({
    "python", "typescript", "javascript",
    "go", "java", "kotlin", "rust",
    "c", "cpp", "csharp", "ruby", "php", "swift",
})