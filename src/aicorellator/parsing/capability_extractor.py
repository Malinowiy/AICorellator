"""Capability extractor — derives capabilities from Python AST.

ADR-0009: capabilities are derived from AST calls, not from names.
A class that calls `subprocess.run` has `executes_code`, regardless
of what it is named.

Design constraints:

- Best-effort. False positives are accepted; false negatives are
  worse. The LLM in Phase 3 filters noise.
- Python only. Other languages are parsed by TSA, but capability
  extraction relies on Python's `ast` module. For non-Python
  sources, capabilities are left empty.
- Call-site based. We do not do dataflow. We look at the dotted
  name of every Call node and check whether it matches a known
  pattern.

The extractor produces a mapping from symbol (class or function)
to the set of capabilities its body exercises.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..patterns import (
    CAPABILITY_CALLS_LLM,
    CAPABILITY_EXECUTES_CODE,
    CAPABILITY_EXECUTES_SHELL_EXPLICIT,
    CAPABILITY_EXECUTES_SHELL_IMPLICIT,
    CAPABILITY_EXECUTES_SHELL,
    CAPABILITY_HTTP_EGRESS,
    CAPABILITY_INVOKES_MCP_TOOL,
    CAPABILITY_READS_DB,
    CAPABILITY_READS_ENV,
    CAPABILITY_READS_FILESYSTEM,
    CAPABILITY_WRITES_DB,
    CAPABILITY_WRITES_FILESYSTEM,
    SECRET_ENV_KEY_SUBSTRINGS,
    SQL_READ_KEYWORDS,
    SQL_WRITE_KEYWORDS,
)


log = logging.getLogger(__name__)


# ------------------------------------------------------------
# Data structures
# ------------------------------------------------------------

@dataclass
class SymbolCapabilities:
    """Capabilities exercised by one symbol in a file.

    A symbol is either a class or a module-level function.
    Method-level capabilities are folded into the parent class.
    """

    symbol_name: str                # class or function name
    file_path: str                  # relative to target root
    capabilities: set[str] = field(default_factory=set)


@dataclass
class FileCapabilities:
    """All capabilities extracted from one file."""

    file_path: str
    symbols: list[SymbolCapabilities] = field(default_factory=list)
    module_capabilities: set[str] = field(default_factory=set)


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def extract(
    file_path: Path,
    relative_path: str,
) -> FileCapabilities:
    """Parse a Python file and extract capabilities per symbol.

    For each class, capabilities are recorded for:
      - the class as a whole (union of all methods)
      - each method separately (its own capabilities)
    For module-level functions, one entry per function.
    """
    result = FileCapabilities(file_path=relative_path)

    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning("cannot read %s: %s", file_path, e)
        return result

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        log.warning("syntax error in %s: %s", file_path, e)
        return result

    # Module-level statements
    for node in tree.body:
        if isinstance(node, ast.Expr):
            result.module_capabilities.update(_scan_node(node))

     # Classes — class symbol first, then its methods.
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_caps: set[str] = set()
            method_entries: list[SymbolCapabilities] = []

            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_caps = _scan_function(item)
                    class_caps.update(method_caps)
                    if method_caps:
                        method_entries.append(SymbolCapabilities(
                            symbol_name=item.name,
                            file_path=relative_path,
                            capabilities=method_caps,
                        ))
                else:
                    class_caps.update(_scan_node(item))

            # Class first.
            result.symbols.append(SymbolCapabilities(
                symbol_name=node.name,
                file_path=relative_path,
                capabilities=class_caps,
            ))
            # Then methods.
            result.symbols.extend(method_entries)
            
    # Module-level functions
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            caps = _scan_function(node)
            if caps:
                result.symbols.append(SymbolCapabilities(
                    symbol_name=node.name,
                    file_path=relative_path,
                    capabilities=caps,
                ))

    # Nested functions (closures, factory-registered tools).
    # ast.walk visits all FunctionDef nodes regardless of depth.
    # Tools registered via @mcp.tool inside create_*_server are
    # nested functions; the top-level pass above does not see them.
    seen_names: set[str] = {s.symbol_name for s in result.symbols}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in seen_names:
            continue
        caps = _scan_function(node)
        if caps:
            result.symbols.append(SymbolCapabilities(
                symbol_name=node.name,
                file_path=relative_path,
                capabilities=caps,
            ))
            seen_names.add(node.name)

    return result

# ------------------------------------------------------------
# Scanning
# ------------------------------------------------------------

def _scan_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Capabilities exercised by the body of a function."""
    caps: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            caps.update(_classify_call(node))
    return caps


def _scan_node(node: ast.AST) -> set[str]:
    """Capabilities exercised by any node (for module-level scans)."""
    caps: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            caps.update(_classify_call(child))
    return caps


def _classify_call(call: ast.Call) -> set[str]:
    """Return the set of capabilities implied by a single Call node."""
    target = _dotted_name(call.func)
    if target is None:
        return set()

    caps: set[str] = set()

    # executes_code / executes_shell
    if _matches_any(target, CAPABILITY_EXECUTES_CODE):
        caps.add("executes_code")
    if _matches_any(target, CAPABILITY_EXECUTES_SHELL_IMPLICIT):
        caps.add("executes_shell")
    elif _matches_any(target, CAPABILITY_EXECUTES_SHELL_EXPLICIT):
        if _has_shell_keyword(call):
         caps.add("executes_shell")

    # filesystem
    if _matches_any(target, CAPABILITY_READS_FILESYSTEM):
        # `open(path, 'w')` is a write, not a read — check mode arg.
        if target == "open" and _has_write_mode(call):
            caps.add("writes_filesystem")
        else:
            caps.add("reads_filesystem")
    if _matches_any(target, CAPABILITY_WRITES_FILESYSTEM):
        caps.add("writes_filesystem")

    # database
    if _matches_any(target, CAPABILITY_READS_DB):
        sql = _first_string_arg(call)
        if sql is not None:
            upper = sql.upper()
            if any(kw in upper for kw in SQL_WRITE_KEYWORDS):
                caps.add("writes_db")
            elif any(kw in upper for kw in SQL_READ_KEYWORDS):
                caps.add("reads_db")
            else:
                # Unknown SQL — assume read (safer default)
                caps.add("reads_db")

    # network
    if _matches_any(target, CAPABILITY_HTTP_EGRESS):
        caps.add("http_egress")

    # env / secrets
    if _matches_any(target, CAPABILITY_READS_ENV):
        caps.add("reads_env")
        # Check whether the accessed key looks like a secret.
        key = _env_key_from_call(call)
        if key is not None and _looks_like_secret(key):
            caps.add("reads_secrets")

    # LLM
    if _matches_any(target, CAPABILITY_CALLS_LLM):
        caps.add("calls_llm")

    # MCP
    if _matches_any(target, CAPABILITY_INVOKES_MCP_TOOL):
        caps.add("invokes_mcp_tool")

    return caps


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _dotted_name(node: ast.expr) -> str | None:
    """Return the dotted name of a call target, or None.

    subprocess.run          → "subprocess.run"
    self.conn.execute       → "self.conn.execute"  (prefix trimmed below)
    open                    → "open"
    """
    parts: list[str] = []
    current: ast.expr = node

    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value

    if isinstance(current, ast.Name):
        parts.append(current.id)
        parts.reverse()
        return ".".join(parts)

    return None


def _matches_any(dotted: str, patterns: tuple[str, ...]) -> bool:
    """True if `dotted` ends with any of the patterns.

    Handles prefixes like `self.` or `obj.`:
        self.conn.execute  matches  connection.execute  → False (no)
        subprocess.run     matches  subprocess.run      → True
        os.path.abspath(...).open  matches open         → True (ends with open)

    To avoid matching too broadly, patterns longer than one segment
    are matched on the trailing segments.
    """
    if dotted in patterns:
        return True
    for p in patterns:
        if dotted.endswith("." + p):
            return True
        # Single-segment pattern: match the last segment only.
        if "." not in p and dotted.endswith("." + p):
            return True
    return False


def _has_shell_keyword(call: ast.Call) -> bool:
    """True if the call has `shell=True` as a keyword."""
    for kw in call.keywords:
        if kw.arg == "shell":
            if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                return True
    return False


def _has_write_mode(call: ast.Call) -> bool:
    """True if `open(...)` is called with a write mode string."""
    for arg in call.args[1:2]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            mode = arg.value
            if any(c in mode for c in ("w", "a", "x", "+")):
                return True
    for kw in call.keywords:
        if kw.arg == "mode":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                mode = kw.value.value
                if any(c in mode for c in ("w", "a", "x", "+")):
                    return True
    return False


def _first_string_arg(call: ast.Call) -> str | None:
    """Return the first string literal argument, or None."""
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _env_key_from_call(call: ast.Call) -> str | None:
    """Extract the env var name from `os.environ.get('KEY')` style calls."""
    # os.environ['KEY']  → not a Call, handled separately (not here)
    # os.getenv('KEY')   → Call with first string arg
    # os.environ.get('KEY') → Call with first string arg
    return _first_string_arg(call)


def _looks_like_secret(key: str) -> bool:
    upper = key.upper()
    return any(sub in upper for sub in SECRET_ENV_KEY_SUBSTRINGS)