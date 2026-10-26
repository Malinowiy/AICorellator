"""Patterns — a single registry of everything AICorellator knows about AI systems.

This module is the canonical source for:

  - Node and edge kinds (mirrors graph/schema.sql and graph/models.py)
  - File path segments that identify entity types (agents/, mcp/servers/, ...)
  - File discovery filters (excluded dirs, extensions, language map)
  - Class name exclusions (*Detector, *Schema, BaseAgent, ...)
  - HTTP/WebSocket route decorators
  - Function name hints (create_mcp_server)
  - Config key patterns for LLM extraction (LLM_PROVIDER, OLLAMA_*, ...)

The class-A name heuristics are gone (ADR-0016, decision 7): no class
name, method name, or provider name in this file decides a node kind.
Kinds come from capabilities (`CAPABILITY_*` below, via
`parsing/capability_extractor.py`) and from framework provenance
(`parsing/framework_detectors/`). The name tables that remain here are
exclusions — they suppress false positives, they never classify.

Centralizing these gives a single overview of what the system can
recognize. Adding a new language, framework, or AI provider means
touching exactly one file.

Per-module logic (parsing, classification, matching) stays in the
respective modules. This file contains only data: every call pattern
below is a `CallPattern` and every capability entry is a
`CapabilityRule`. The types and the matching rules live in
`patterns_registry.py`.

The two exceptions are predicates that belong beside their table rather
than in a consumer: `untrusted_source_match` with the
`UNTRUSTED_SOURCE_*` tables, and `lethal_trifecta_caps` with the
`LETHAL_TRIFECTA_*` sets. Both have more than one caller, and a
definition shared by the graph and the score cannot drift.

Each rule carries one sentence explaining why it exists
(`CapabilityRule.reason`) — the registries are what a reviewer reads to
decide whether a new pattern belongs, and a pattern without a reason is
a pattern nobody can audit. The `CAPABILITY_*` call-pattern names below
are the rendered text of the rules (`CallPattern.text`), derived and
never hand-maintained.
"""

from __future__ import annotations

from .patterns_registry import CallPattern, CapabilityRule


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
    # The anchor for attacker-influenced data (1.3a, ADR-0010). A
    # `untrusted_source` node is not an actor and not an action: it is
    # the place untrusted content is read from — a retrieval corpus, an
    # MCP tool catalogue, a mailbox. Chain groups hang off it, which is
    # why it is a node kind and not a node attribute.
    "untrusted_source",
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
    # A writer produces an untrusted source (1.3a.1, ADR-0010).
    "writes_to",
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

# Edge kinds that carry flow_kind = "control" and no confirmed data flow
# (ADR-0015; ADR-0016, decision 3).
#
# `writes_to` (1.3a.1) is here for the same reason `provides_tool` is:
# "this server produces this catalogue" is a declaration, and the
# producer never traced data into the source — it matched a server to a
# source by file. A traced write (RAG `add_documents`, an email send)
# would carry its own value when it lands.
CONTROL_EDGE_KINDS: frozenset[str] = frozenset({
    "uses_model", "uses_tool", "provides_tool", "delegates_to",
    "writes_to",
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

# Class name suffix → node kind is *not* here any more. `*Agent`,
# `*Orchestrator`, `*LLMClient`, `*Guardrail*`, `*Server`/`*Service`
# under mcp/servers/ were the class-A heuristics of ADR-0016 decision 7;
# they classified FinBot and nothing else. A class becomes an `agent`,
# `llm_endpoint`, `mcp_server` or `guardrail` node when its body carries
# the capability, or when a framework detector recognized it. Adding a
# project detector: a file in `parsing/framework_detectors/` plus a line
# in `registry.py` (ADR-0016, decision 5).

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

# `EXTRA_AGENT_CLASSES` ("CoPilotAssistant"), `EXTRA_LLM_CLASSES`
# ("LLMJudge") and `PROVIDER_HINTS` (a class-name substring → provider)
# were class-A heuristics and are removed (ADR-0016, decision 7). The
# `provider` attribute on an `llm_endpoint` node now comes from the
# configuration that declares the endpoint (`parsing/llm_extractor.py`)
# or from the framework detector that recognized the client class.

# ============================================================
# Capabilities (ADR-0009)
# ============================================================
# Capabilities are derived from AST calls, not from names. Each rule
# below pairs a capability with the call shapes that evidence it and a
# one-sentence reason; the shapes are `CallPattern` objects, matched by
# `patterns_registry.CallPattern.matches` against:
#   - dotted call names:  subprocess.run, os.system, open
#   - attribute access:   request.body, cursor.execute
#
# Matching is intentionally simple — false positives are accepted
# (see ADR-0009). The LLM in Phase 3 filters noise.
#
# A pattern is a qualifier plus a verb. The qualifier may be absent (a
# builtin like `open`) or several segments deep (`urllib.request.urlopen`);
# the verb is matched on the final segment, and a qualifier on the
# segments before it. `verb_family=True` relaxes the verb to a prefix
# (1.2a.3b), and leading underscores on the receiver are ignored
# (1.2a.3e) — both are flags on the pattern now, not text conventions.

# --- The capability vocabulary ---
# Every capability a *rule may credit* is declared here, and
# `capability_extractor._credit` refuses anything else. The refusal is
# the point: a typo in a rule's `capability` used to widen the graph's
# vocabulary silently, and a node capability is read by classification
# (SINK_CAPABILITIES below), by the prompt and by the trifecta voter. An
# unknown string there is a bug that shows up as a missing finding,
# weeks later, not as an error.
#
# This is not the set of capabilities a rule produces today — a
# declared-but-uncredited name (`http_ingress`) is legitimate, because
# its rule exists and the extractor simply finds nothing to credit.
#
# Routing capabilities (`db_statement`, `commit`) are deliberately
# absent: `patterns_registry.ROUTING_CAPABILITIES` is the list of
# capabilities that never land on a node, and `_credit` is the function
# that would otherwise write them.
VALID_CAPABILITIES: frozenset[str] = frozenset({
    "executes_code",
    "executes_shell",
    "reads_filesystem",
    "writes_filesystem",
    "reads_db",
    "writes_db",
    "http_egress",
    "http_ingress",
    "reads_env",
    "reads_secrets",
    "calls_llm",
    "invokes_mcp_tool",
    "reads_untrusted",
})

# --- Adapter attribute patterns (1.2a.3i) ---
# Some calls go through a wrapper object instead of a module: an
# adapter, an HTTP client, a shell handle, an LLM handle. There is no
# module name to match — `self.adapter.execute(...)` is a method on a
# variable — so these patterns name a *qualifier plus verb* and lean on
# the trailing-segment rule (patterns_registry.CallPattern):
# `adapter.execute` matches `self.adapter.execute` and
# `self.vendor.adapter.execute`, but neither `self.execute` nor a bare
# `execute()`. The 1.2a.3e underscore rule applies unchanged, so
# `self._adapter.execute` matches too.
#
# Two segments, never one. A bare `run`, `execute`, `exec`, `post`,
# `put`, `patch`, `delete`, `request`, `invoke`, `chat`, `complete`,
# `generate` or `spawn` is far too common in unrelated code — test
# runners, blog code, arbitrary objects — and a one-segment pattern is
# matched on the final segment alone (ADR-0009 keeps the
# false-positive rate controlled).
#
# `self.<verb>` is deliberately not covered: the receiver is the class
# itself, not an adapter, and `self.execute()` is a plain method call.
# The qualifier is a hint, not a licence — `adapter.<verb>` and
# `client.<verb>` are matched only for the verbs listed below.
#
# `client.get` / `session.get` are *not* listed (1.2a.3i decision): a
# GET is overwhelmingly a read of a remote resource, and the module
# forms (`requests.get`, `httpx.get`) already cover the fetch idiom.
# Crediting every `self.client.get(...)` with `http_egress` would make
# each such call a dangerous call and each containing symbol a sink,
# which is a much larger claim than the wrapper patterns above make.

# --- Node capabilities ---

EXECUTES_CODE_RULE = CapabilityRule(
    capability="executes_code",
    patterns=(
        CallPattern("subprocess", "run"),
        CallPattern("subprocess", "call"),
        CallPattern("subprocess", "Popen"),
        CallPattern("subprocess", "check_output"),
        CallPattern("subprocess", "check_call"),
        CallPattern("os", "system"),
        CallPattern("os", "popen"),
        CallPattern("os", "execv"),
        CallPattern("os", "execve"),
        CallPattern("os", "spawnv"),
        CallPattern(None, "eval"),
        CallPattern(None, "exec"),
        CallPattern(None, "compile"),
        CallPattern("pickle", "loads"),
        CallPattern("marshal", "loads"),
        CallPattern("yaml", "unsafe_load"),
        # Adapter wrappers around a shell / process (1.2a.3i). See the
        # adapter note above. `shell.execute` and `adapter.execute` are
        # distinct from the cursor `*.execute` statement sites, which
        # are matched by the statement rule under their own qualifiers
        # (`cur`, `cursor`).
        CallPattern("shell", "run"),
        CallPattern("shell", "execute"),
        CallPattern("shell", "exec"),
        CallPattern("adapter", "run"),
        CallPattern("adapter", "execute"),
        CallPattern("adapter", "exec"),
        CallPattern("adapter", "spawn"),
    ),
    reason="the call evaluates code or starts a process in this program",
)

# Calls that ALWAYS execute a shell — no keyword required.
EXECUTES_SHELL_IMPLICIT_RULE = CapabilityRule(
    capability="executes_shell",
    patterns=(
        CallPattern("os", "system"),
        CallPattern("os", "popen"),
    ),
    reason="the call is a shell invocation by definition (no shell=True)",
)

# Calls that execute a shell only when shell=True is passed.
EXECUTES_SHELL_EXPLICIT_RULE = CapabilityRule(
    capability="executes_shell",
    patterns=(
        CallPattern("subprocess", "run"),
        CallPattern("subprocess", "Popen"),
        CallPattern("subprocess", "call"),
    ),
    reason="the call runs through the shell when `shell=True` is passed",
)

# The "any shell capability" view is rendered with the other views below.

READS_FILESYSTEM_RULE = CapabilityRule(
    capability="reads_filesystem",
    patterns=(
        CallPattern(None, "open"),
        CallPattern("io", "open"),
        CallPattern("pathlib.Path", "read_text"),
        CallPattern("pathlib.Path", "read_bytes"),
        CallPattern("pathlib.Path", "open"),
        CallPattern("os", "read"),
        CallPattern("shutil", "copy"),
        CallPattern("shutil", "copytree"),
    ),
    reason="the call opens or copies a file for reading",
)

WRITES_FILESYSTEM_RULE = CapabilityRule(
    capability="writes_filesystem",
    patterns=(
        CallPattern("pathlib.Path", "write_text"),
        CallPattern("pathlib.Path", "write_bytes"),
        CallPattern("os", "write"),
        CallPattern("shutil", "move"),
        CallPattern("shutil", "rmtree"),
        CallPattern("os", "remove"),
        CallPattern("os", "unlink"),
        CallPattern("os", "rmdir"),
        CallPattern("os", "makedirs"),
    ),
    reason="the call creates, moves or deletes a file or directory",
)

# --- Database call sites ---
# Two families, with different evidence for read vs write:
#
#   1. Statement call sites — the SQL text is the first string
#      argument, so the SQL keyword (SELECT / INSERT / UPDATE /
#      DELETE / ...) decides. See SQL_READ_KEYWORDS and
#      SQL_WRITE_KEYWORDS.
#   2. ORM session and repository methods — there is no SQL text
#      at all; the method name itself decides (`db.delete` is a
#      write, `db.query` is a read). Added by 1.2a.3.
#
# Every pattern is matched with trailing-segment logic
# (patterns_registry.CallPattern), so `db.delete` matches
# `self.db.delete` and `session.db.delete`, but not `self.delete`.
#
# Leading underscores on the receiver are ignored (1.2a.3e): `_db`,
# `_session`, `_conn` and `_cur` match `db`, `session`, `conn` and
# `cur`. A private attribute is the same object as a public one, and
# mcploitable writes `_conn.commit()` / `_cur.execute(...)`
# throughout. The verb is never normalized — `db._delete` is a
# private method, not the public ORM `delete`. See
# patterns_registry.CallPattern._qualifier_matches.
#
# The two-segment shape is deliberate: a bare `delete`, `add`,
# `save`, `get`, or `query` is far too common in unrelated code
# (ADR-0009 — keep the false-positive rate controlled).
#
# Verb families (1.2a.3b). Repository APIs name methods
# `<verb>_<noun>` — `repo.get_file`, `repo.delete_transactions` —
# so the exact two-segment patterns miss every one of them and the
# call sites stayed `unknown`. Such a pattern is declared
# `verb_family=True`: the qualifier segments still have to match
# exactly, and the final segment only has to *start with* the verb.
# `repo.get` as a family covers `repo.get`, `repo.get_file` and
# `repo.get_all_files`; `db.get` as a family covers
# `self.db.get_user` — but neither matches a bare `get_file`,
# because the qualifier is what makes it a database call.
#
# The verb sets are closed (reads: get / find / list / filter / all,
# writes: add / delete / save / create / update) and the noun is
# unbounded. The cost is a name like `repo.get_or_create`, which
# lands in reads rather than writes: a plausible-but-wrong
# capability is the security analyst's to filter, an `unknown` on
# every repository access is not (ADR-0009). Verb families are used
# here and nowhere else, and `CallPattern.__post_init__` rejects a
# family without a qualifier, so a one-word family cannot be added by
# accident.

DB_STATEMENT_RULE = CapabilityRule(
    capability="db_statement",
    patterns=(
        CallPattern("cursor", "execute"),
        CallPattern("connection", "execute"),
        CallPattern("session", "execute"),
        CallPattern("engine", "execute"),
        # `cur` — the usual cursor abbreviation (1.2a.3e); same
        # statement-call shape.
        CallPattern("cur", "execute"),
        CallPattern("cur", "executemany"),
        # SQLAlchemy.
        CallPattern(None, "text"),
    ),
    reason=(
        "statement call sites carry the SQL text, so the SQL keyword "
        "decides read vs write — this rule routes, it does not conclude"
    ),
)

ORM_READS_RULE = CapabilityRule(
    capability="reads_db",
    patterns=(
        CallPattern("db", "query"),
        CallPattern("db", "get"),
        CallPattern("session", "query"),
        CallPattern("session", "get"),
        CallPattern("repository", "get"),
        CallPattern("repository", "find"),
        CallPattern("repository", "list"),
        CallPattern("repository", "filter"),
        CallPattern("repository", "all"),
        CallPattern("repo", "get"),
        CallPattern("repo", "find"),
        CallPattern("repo", "list"),
        CallPattern("repo", "filter"),
        CallPattern("repo", "all"),
        # Cursor fetches (1.2a.3h). A fetch is the read half of a
        # statement call: `cur.execute("SELECT ...")` runs the query and
        # `cur.fetchone()` collects the rows, so the fetch is where the
        # data actually arrives. It cannot live in the statement rule:
        # that rule is routed through the SQL-keyword branch, and a
        # fetch has no SQL string to inspect (the query was executed
        # earlier) — the method name is the only evidence there is,
        # which is exactly what this rule carries. `cursor` is spelled
        # out because a cursor is stored on `self.cursor` as often as in
        # a local `cur`.
        CallPattern("cur", "fetchone"),
        CallPattern("cur", "fetchall"),
        CallPattern("cur", "fetchmany"),
        CallPattern("cursor", "fetchone"),
        CallPattern("cursor", "fetchall"),
        CallPattern("cursor", "fetchmany"),
        # Verb families (1.2a.3b) — a qualifier plus a read verb, any
        # noun.
        CallPattern("db", "get", verb_family=True),
        CallPattern("db", "find", verb_family=True),
        CallPattern("db", "list", verb_family=True),
        CallPattern("db", "filter", verb_family=True),
        CallPattern("db", "all", verb_family=True),
        CallPattern("session", "get", verb_family=True),
        CallPattern("session", "find", verb_family=True),
        CallPattern("session", "list", verb_family=True),
        CallPattern("session", "filter", verb_family=True),
        CallPattern("session", "all", verb_family=True),
        CallPattern("repository", "get", verb_family=True),
        CallPattern("repository", "find", verb_family=True),
        CallPattern("repository", "list", verb_family=True),
        CallPattern("repository", "filter", verb_family=True),
        CallPattern("repository", "all", verb_family=True),
        CallPattern("repo", "get", verb_family=True),
        CallPattern("repo", "find", verb_family=True),
        CallPattern("repo", "list", verb_family=True),
        CallPattern("repo", "filter", verb_family=True),
        CallPattern("repo", "all", verb_family=True),
    ),
    reason=(
        "an ORM session or repository read method is read evidence when "
        "there is no SQL text to inspect (1.2a.3)"
    ),
)

ORM_WRITES_RULE = CapabilityRule(
    capability="writes_db",
    patterns=(
        CallPattern("db", "add"),
        CallPattern("db", "add_all"),
        CallPattern("db", "delete"),
        CallPattern("db", "commit"),
        CallPattern("db", "flush"),
        CallPattern("db", "merge"),
        CallPattern("db", "bulk_save_objects"),
        CallPattern("db", "bulk_insert_mappings"),
        CallPattern("session", "add"),
        CallPattern("session", "add_all"),
        CallPattern("session", "delete"),
        CallPattern("session", "commit"),
        CallPattern("session", "flush"),
        CallPattern("session", "merge"),
        CallPattern("session", "bulk_save_objects"),
        CallPattern("session", "bulk_insert_mappings"),
        CallPattern("repository", "add"),
        CallPattern("repository", "delete"),
        CallPattern("repository", "save"),
        CallPattern("repository", "create"),
        CallPattern("repository", "update"),
        CallPattern("repo", "add"),
        CallPattern("repo", "delete"),
        CallPattern("repo", "save"),
        CallPattern("repo", "create"),
        CallPattern("repo", "update"),
        # Verb families (1.2a.3b) — a qualifier plus a write verb, any
        # noun.
        CallPattern("db", "add", verb_family=True),
        CallPattern("db", "delete", verb_family=True),
        CallPattern("db", "save", verb_family=True),
        CallPattern("db", "create", verb_family=True),
        CallPattern("db", "update", verb_family=True),
        CallPattern("session", "add", verb_family=True),
        CallPattern("session", "delete", verb_family=True),
        CallPattern("session", "save", verb_family=True),
        CallPattern("session", "create", verb_family=True),
        CallPattern("session", "update", verb_family=True),
        CallPattern("repository", "add", verb_family=True),
        CallPattern("repository", "delete", verb_family=True),
        CallPattern("repository", "save", verb_family=True),
        CallPattern("repository", "create", verb_family=True),
        CallPattern("repository", "update", verb_family=True),
        CallPattern("repo", "add", verb_family=True),
        CallPattern("repo", "delete", verb_family=True),
        CallPattern("repo", "save", verb_family=True),
        CallPattern("repo", "create", verb_family=True),
        CallPattern("repo", "update", verb_family=True),
    ),
    reason=(
        "an ORM session or repository write method is write evidence when "
        "there is no SQL text to inspect (1.2a.3)"
    ),
)

# Commit calls (1.2a.3c). A commit is the persistence step of an ORM
# session: `db.commit()` writes whatever the session accumulated, so
# the data arrives through the session's state rather than through the
# call's own arguments. The Flow Analyzer consults this rule to make
# the single deliberate exception to its no-argument rule — a bare
# `db.commit()` contributes `parameter_match` — and for nothing else.
#
# Its capability, `commit`, is a *routing* capability
# (patterns_registry.ROUTING_CAPABILITIES): it is never written to a
# node. Which calls make a symbol a sink is a capability question
# (ADR-0009), and `repo.commit` / `conn.commit` are not evidence of a
# write on their own; which sink bodies can carry data is an evidence
# question. `db.commit` and `session.commit` are in both rules — the
# write rule because they are ORM writes, this one because they persist
# a session.
#
# Matched with the same trailing-segment rule as every other call-site
# pattern (patterns_registry.CallPattern), so `self.db.commit` matches
# `db.commit` but a bare `commit()` matches nothing.
COMMIT_RULE = CapabilityRule(
    capability="commit",
    patterns=(
        CallPattern("db", "commit"),
        CallPattern("session", "commit"),
        CallPattern("repository", "commit"),
        CallPattern("repo", "commit"),
        CallPattern("transaction", "commit"),
        CallPattern("connection", "commit"),
        CallPattern("conn", "commit"),
    ),
    reason=(
        "a commit persists the session's accumulated state, so a sink can "
        "carry data through it (1.2a.3c) — not a write capability alone"
    ),
)

HTTP_EGRESS_RULE = CapabilityRule(
    capability="http_egress",
    patterns=(
        CallPattern("requests", "get"),
        CallPattern("requests", "post"),
        CallPattern("requests", "put"),
        CallPattern("requests", "patch"),
        CallPattern("requests", "delete"),
        CallPattern("requests", "head"),
        CallPattern("requests", "request"),
        CallPattern("httpx", "get"),
        CallPattern("httpx", "post"),
        CallPattern("httpx", "put"),
        CallPattern("httpx", "delete"),
        CallPattern("httpx", "request"),
        CallPattern("aiohttp", "ClientSession"),
        CallPattern("urllib.request", "urlopen"),
        CallPattern("urllib.request", "Request"),
        # Adapter / client wrappers (1.2a.3i). See the adapter note above.
        # Write verbs only: these are the calls that send a body or a
        # command to a remote endpoint. `client.get` / `session.get` /
        # `adapter.get` are deliberately absent.
        CallPattern("client", "post"),
        CallPattern("client", "put"),
        CallPattern("client", "patch"),
        CallPattern("client", "delete"),
        CallPattern("adapter", "post"),
        CallPattern("adapter", "put"),
        CallPattern("adapter", "patch"),
        CallPattern("session", "post"),
        CallPattern("session", "put"),
        CallPattern("session", "patch"),
        CallPattern("session", "request"),
    ),
    reason=(
        "the call sends a request to a remote endpoint; only verbs that "
        "carry a body — a GET is a read (1.2a.3i)"
    ),
)

HTTP_INGRESS_RULE = CapabilityRule(
    capability="http_ingress",
    patterns=(),
    reason=(
        "entry points are found by the entry-point detector, not by a "
        "call shape: the extractor deliberately flags nothing here"
    ),
)

READS_ENV_RULE = CapabilityRule(
    capability="reads_env",
    patterns=(
        CallPattern("os", "environ"),
        CallPattern("os", "getenv"),
        CallPattern("os.environ", "get"),
        CallPattern("os", "environb"),
    ),
    reason=(
        "the call reads the process environment; a secret-looking key "
        "also yields reads_secrets (SECRET_ENV_KEY_SUBSTRINGS)"
    ),
)

CALLS_LLM_RULE = CapabilityRule(
    capability="calls_llm",
    patterns=(
        CallPattern("openai.chat.completions", "create"),
        CallPattern("openai.Completion", "create"),
        CallPattern("anthropic.messages", "create"),
        CallPattern("ollama", "chat"),
        CallPattern("ollama", "generate"),
        CallPattern("litellm", "completion"),
        CallPattern("litellm", "acompletion"),
        CallPattern("langchain.chat_models", "ChatOpenAI"),
        CallPattern("langchain.llms", "Ollama"),
        CallPattern("llama_cpp", "Llama"),
        CallPattern("vllm", "LLM"),
        # Adapter / handle wrappers (1.2a.3i). See the adapter note above.
        # `chat` and `generate` are already in the list under `ollama.*`;
        # these are the same verbs on a self-held handle, plus the
        # framework-neutral `invoke` / `complete`.
        CallPattern("llm", "invoke"),
        CallPattern("llm", "chat"),
        CallPattern("llm", "complete"),
        CallPattern("llm", "generate"),
        CallPattern("model", "invoke"),
        CallPattern("model", "chat"),
        CallPattern("model", "complete"),
    ),
    reason="the call invokes an LLM provider or a model handle",
)

INVOKES_MCP_TOOL_RULE = CapabilityRule(
    capability="invokes_mcp_tool",
    patterns=(
        CallPattern("session", "call_tool"),
        CallPattern("client", "call_tool"),
        CallPattern("mcp_client", "call_tool"),
        CallPattern("server", "call_tool"),
        CallPattern("fastmcp.client", "call_tool"),
    ),
    reason="the call invokes a tool on an MCP server or client",
)

# --- Untrusted sources (1.3a, ADR-0010) ---
# Three families, one capability: `reads_untrusted`. Each rule is one
# *source type* — where the attacker-influenced data lives — and the
# detector in `parsing/untrusted_sources.py` turns a matching call into
# an `untrusted_source` node of that type. The capability and the node
# come from the same table, so a name the extractor credits can never be
# a name the detector ignores.
#
# What makes a call a source: it pulls content the operator does not
# control out of a store an attacker can write to. Everything below is
# that shape, and the set is deliberately narrow — ADR-0009 accepts
# false positives, but an `untrusted_source` node is a chain-group
# anchor (1.3b), so a wrong one is not noise the LLM filters out, it is
# a wrong group.
#
# Deliberately out of scope in 1.3a:
#
#   - **Web scrapes.** `requests.get` / `urllib.request.urlopen` are
#     already `http_egress` (HTTP_EGRESS_RULE), and one call is one
#     fact: re-crediting it as `reads_untrusted` would double-count the
#     same evidence on the same node. A scrape is also a *direct* read
#     of a live response, not a read of a store an earlier write
#     populated, which is the shape chain groups model (ADR-0010).
#
#   - **User uploads.** An upload is a structural signal — a route
#     handler that writes and later reads a directory — not a call
#     shape: the same `open()` is an upload sink in one file and a
#     config read in the next, and the route is not visible to a
#     call-site detector. Deferred rather than guessed at.
#
#   - **`session.list_tools`.** The exception inside the MCP family:
#     ORM_READS_RULE owns a `session.list*` verb family, so
#     `session.list_tools` would be credited `reads_db` *and*
#     `reads_untrusted`. The MCP SDK's `ClientSession` spells it that
#     way, but every corpus call site in 1.3a goes through a
#     `Client` / `FastMCP` handle (`client.` / `server.` / `mcp.`), and
#     a spurious `reads_db` on an MCP session is worse than the miss.
#     Revisit if a real target uses `ClientSession` directly.
#
# rag_corpus — a retrieval corpus read. Vector stores and retrievers
# spell their read end explicitly; the constructors (`chromadb.Client`,
# `FAISS.from_documents`, `Chroma(...)`) are deliberately absent — a
# handle is not a read, and `db.query` (plain SQLAlchemy) is the
# dominant `.query(` in the reference corpora, which is why `query` is
# only ever matched under a store-specific qualifier.
READS_UNTRUSTED_RAG_RULE = CapabilityRule(
    capability="reads_untrusted",
    patterns=(
        CallPattern("retriever", "get_relevant_documents"),
        CallPattern("retriever", "invoke"),
        CallPattern("vectorstore", "similarity_search"),
        CallPattern("vectorstore", "similarity_search_with_score"),
        CallPattern("vector_store", "similarity_search"),
        CallPattern("collection", "query"),
        CallPattern("collection", "search"),
    ),
    reason=(
        "the call reads retrieved chunks back from a corpus an "
        "attacker can seed (indirect prompt injection through RAG)"
    ),
)

# mcp_tool_description — the tool catalogue. `list_tools()` returns
# server-supplied name/description/schema, and the description is
# attacker-controlled text that reaches the model's context
# (tool-poisoning / the MCP rug pull). The qualifiers are the client
# handles the SDK and the frameworks expose; see the `session` note
# above.
READS_UNTRUSTED_MCP_DESCRIPTION_RULE = CapabilityRule(
    capability="reads_untrusted",
    patterns=(
        CallPattern("client", "list_tools"),
        CallPattern("server", "list_tools"),
        CallPattern("mcp", "list_tools"),
        CallPattern("mcp_client", "list_tools"),
    ),
    reason=(
        "the call reads MCP tool descriptions, which the server "
        "supplies and the model then trusts (tool poisoning)"
    ),
)

# email_inbox — a mailbox read. `imaplib.IMAP4*` is the one
# constructor credited as a source rather than as a handle: unlike
# `chromadb.Client()`, the name is mailbox-protocol-specific, so
# building the connection is evidence of mailbox access, and the read
# verbs that follow are matched on the connection object's name
# (`imap`, the conventional local name for an `IMAP4` instance).
READS_UNTRUSTED_EMAIL_RULE = CapabilityRule(
    capability="reads_untrusted",
    patterns=(
        CallPattern("imaplib", "IMAP4"),
        CallPattern("imaplib", "IMAP4_SSL"),
        CallPattern("imaplib", "IMAP4_stream"),
        CallPattern("imap", "search"),
        CallPattern("imap", "fetch"),
        CallPattern("imap", "uid"),
        CallPattern("mailbox", "get_message"),
        CallPattern("mailbox", "get_messages"),
    ),
    reason=(
        "the call reads a mailbox, the original unbounded "
        "attacker-writable channel (phishing / spam poisoning)"
    ),
)

# --- The registry ---
# Every call-pattern rule in this file, in declaration order. The order
# that matters for classification lives in capability_extractor; this
# tuple is what a reviewer (or a test) iterates to see the whole set.

CALL_RULES: tuple[CapabilityRule, ...] = (
    EXECUTES_CODE_RULE,
    EXECUTES_SHELL_IMPLICIT_RULE,
    EXECUTES_SHELL_EXPLICIT_RULE,
    READS_FILESYSTEM_RULE,
    WRITES_FILESYSTEM_RULE,
    DB_STATEMENT_RULE,
    ORM_READS_RULE,
    ORM_WRITES_RULE,
    COMMIT_RULE,
    HTTP_EGRESS_RULE,
    HTTP_INGRESS_RULE,
    READS_ENV_RULE,
    CALLS_LLM_RULE,
    INVOKES_MCP_TOOL_RULE,
    READS_UNTRUSTED_RAG_RULE,
    READS_UNTRUSTED_MCP_DESCRIPTION_RULE,
    READS_UNTRUSTED_EMAIL_RULE,
)

# --- Source-type table (1.3a) ---
# Source type → rule. A tuple of pairs, not a dict: the declaration
# order is the call order in the detector and the extractor, and a
# `*_RULE` name plus this table is the whole definition of a family.
#
# The source type is a data value and the node *prefix* — the node names
# are `rag_corpus:<target>`, `mcp_tool_description:<target>`,
# `email_inbox:<target>` — so `source_type` on the node carries the same
# string.
#
# The name ends in `RULES`, not `_RULE`, which is what keeps it out of
# `tests/test_patterns_registry.py::test_no_rule_is_missing_from_the_registry`
# (that test collects `*_RULE` names from this module): the values are
# rules, but this is a table of them, and each owner is registered in
# `CALL_RULES` above.
UNTRUSTED_SOURCE_RULES: tuple[tuple[str, CapabilityRule], ...] = (
    ("rag_corpus", READS_UNTRUSTED_RAG_RULE),
    ("mcp_tool_description", READS_UNTRUSTED_MCP_DESCRIPTION_RULE),
    ("email_inbox", READS_UNTRUSTED_EMAIL_RULE),
)


def untrusted_source_match(
    call_target: str,
) -> tuple[str, CapabilityRule] | None:
    """The `(source_type, rule)` whose rule matches `call_target`.

    Returns None when the call is not a read of a known source. The pair,
    not just the type, because the two consumers want different halves of
    it: `capability_extractor` credits the capability and logs the rule's
    `reason`, `parsing/untrusted_sources.py` names the node after the
    source type. One table means the two can never disagree about a name
    — a divergence would show up as a capability with no node, or a node
    with no capability, and neither is a state the graph should be able
    to hold.

    A call matches at most one family today; the first match wins, so a
    future overlap resolves by declaration order (the `session` note
    above is what a declared overlap would look like).
    """
    for source_type, rule in UNTRUSTED_SOURCE_RULES:
        if rule.matches(call_target):
            return source_type, rule
    return None


# --- Deliberate capability overlaps ---
# A call can evidence more than one capability, and two rules may
# therefore match the same name. Only these pairs are intentional:
#
#   executes_code + executes_shell   `os.system` and `subprocess.run`
#                                    are both (they are shell calls
#                                    *and* process execution)
#   writes_db + commit               `db.commit` is an ORM write and
#                                    the persistence step the Flow
#                                    Analyzer treats specially
#                                    (1.2a.3c)
#
# Both capabilities are added; nothing is shadowed. Any *other* pair is
# a typo or a shadowed pattern, and
# `tests/test_patterns_registry.py` fails on it.
INTENTIONAL_OVERLAPS: frozenset[frozenset[str]] = frozenset({
    frozenset({"executes_code", "executes_shell"}),
    frozenset({"writes_db", "commit"}),
})

# --- Rendered views ---
# The pattern text of each rule (`CallPattern.text`), derived from the
# rules above and never hand-maintained. They exist so pattern text can
# be printed, documented and asserted (`"repo.get*" in
# CAPABILITY_READS_DB`), and so every name that used to point at a list
# of strings still resolves. Matching goes through the rules; a string
# is only ever the rendering of one.

CAPABILITY_EXECUTES_CODE = EXECUTES_CODE_RULE.text_patterns
CAPABILITY_EXECUTES_SHELL_IMPLICIT = EXECUTES_SHELL_IMPLICIT_RULE.text_patterns
CAPABILITY_EXECUTES_SHELL_EXPLICIT = EXECUTES_SHELL_EXPLICIT_RULE.text_patterns
CAPABILITY_EXECUTES_SHELL = (
    EXECUTES_SHELL_IMPLICIT_RULE.text_patterns
    + EXECUTES_SHELL_EXPLICIT_RULE.text_patterns
)
CAPABILITY_READS_FILESYSTEM = READS_FILESYSTEM_RULE.text_patterns
CAPABILITY_WRITES_FILESYSTEM = WRITES_FILESYSTEM_RULE.text_patterns
CAPABILITY_DB_STATEMENT_CALLS = DB_STATEMENT_RULE.text_patterns
CAPABILITY_ORM_READS_DB = ORM_READS_RULE.text_patterns
CAPABILITY_ORM_WRITES_DB = ORM_WRITES_RULE.text_patterns
ORM_COMMIT_CALLS = COMMIT_RULE.text_patterns
CAPABILITY_READS_DB = CAPABILITY_DB_STATEMENT_CALLS + CAPABILITY_ORM_READS_DB
CAPABILITY_WRITES_DB = (
    CAPABILITY_DB_STATEMENT_CALLS + CAPABILITY_ORM_WRITES_DB
)
CAPABILITY_HTTP_EGRESS = HTTP_EGRESS_RULE.text_patterns
CAPABILITY_HTTP_INGRESS = HTTP_INGRESS_RULE.text_patterns
CAPABILITY_READS_ENV = READS_ENV_RULE.text_patterns
CAPABILITY_CALLS_LLM = CALLS_LLM_RULE.text_patterns
CAPABILITY_INVOKES_MCP_TOOL = INVOKES_MCP_TOOL_RULE.text_patterns
# The union of the three source families: one capability, so one view.
# Per-family text is on the rules themselves (`READS_UNTRUSTED_RAG_RULE
# .text_patterns`), and `UNTRUSTED_SOURCE_RULES` is what maps a family
# to its rule; this is the flat list a caller wants when it does not
# care which family matched.
CAPABILITY_READS_UNTRUSTED = (
    READS_UNTRUSTED_RAG_RULE.text_patterns
    + READS_UNTRUSTED_MCP_DESCRIPTION_RULE.text_patterns
    + READS_UNTRUSTED_EMAIL_RULE.text_patterns
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


def lethal_trifecta_caps(capabilities: list[str]) -> tuple[str, ...]:
    """The capabilities that put a node in ADR-0009's lethal trifecta.

    Non-empty exactly when the node reads untrusted input, can read
    secrets, and can get data out — the three sets above. Sorted, so a
    reason string built from it is deterministic across runs.

    The definition lives here, next to the sets, for the same reason
    `untrusted_source_match` lives next to its table: it has two
    consumers — `normalizer_stages/lethal_trifecta.py`, which records
    the answer as a node attribute (1.3d), and `graph/query.py`, which
    scores it +5 (ADR-0012) — and one definition is what keeps the graph
    and the score from disagreeing about the same node.

    The return value is the matched capabilities, not a boolean, because
    the scorer quotes them in `score_components["reasons"]`. Empty tuple
    means "not a trifecta", so a caller can read it as a truth value.
    """
    matched: list[str] = []
    for wanted in (
        LETHAL_TRIFECTA_READ_CAPS,
        LETHAL_TRIFECTA_SECRET_CAPS,
        LETHAL_TRIFECTA_EGRESS_CAPS,
    ):
        hits = wanted.intersection(capabilities)
        if not hits:
            return ()
        matched.extend(hits)
    return tuple(sorted(matched))


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
# Method name patterns — removed (ADR-0016, decision 7)
# ============================================================
# `SINK_METHOD_NAMES` (execute_script, network_request, upload_file, ...),
# `SINK_CATEGORY_HINTS` (a name substring → sink category),
# `TOOL_METHOD_PREFIXES` (get_, update_, ...) and `SKIP_METHOD_NAMES`
# (to_dict, to_json, ...) were the class-A method heuristics. A method is
# a sink because its body executes, writes or sends (`SINK_CAPABILITIES`);
# it is a tool because a framework detector declared it, or because it is
# a public method of an MCP server. Serialization helpers are not tools
# for the same reason they are not anything else: no capability, no node.


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

# `DELEGATE_METHOD_PREFIX` ("delegate_to_") and `RUNNER_TO_AGENT`
# (`run_invoice_agent` → `InvoiceAgent`) were the class-A delegation
# heuristics and are removed (ADR-0016, decision 7). A call is a
# delegation because the callee (or the argument passed to it) is an
# `agent` node in the graph — a node a framework detector, a capability
# or a user detector produced — not because its name matches a table.


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