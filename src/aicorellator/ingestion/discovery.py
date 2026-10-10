"""File Discovery — walks a target, applies filters, hashes files.

Produces a FileManifest consumed by TSA, SCIP, and LLM Extractor.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from .target import Target


# Directories never descended into. Matched by name at any depth.
EXCLUDE_DIRS = frozenset({
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
})

# File extensions we do not read.
EXCLUDE_EXTENSIONS = frozenset({
    # Binaries and compiled artifacts
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".o", ".a",
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".bmp", ".tiff",
    # Audio/Video
    ".mp3", ".mp4", ".mov", ".avi", ".webm", ".wav",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    # Documents
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # Fonts
    ".woff", ".woff2", ".ttf", ".eot",
    # Databases
    ".db", ".sqlite", ".sqlite3",
    # Web templates and styles — not relevant for AI graph
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    # Lock files — dependency pins, not source
    ".lock",
})

# Extension → language hint. Used by TSA worker and classification heuristics.
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
    "README": "markdown",
    # Files with no extension or with hidden-name-as-extension
    ".env": "dotenv",
    ".gitignore": "gitignore",
    ".dockerignore": "dockerignore",
}

# Hidden files we DO want to process, despite leading dot.
ALLOWED_HIDDEN_FILES = frozenset({
    ".env",
    ".gitignore",
    ".dockerignore",
})

# Max file size to hash and parse (10 MiB). Larger files are skipped.
MAX_FILE_SIZE = 10 * 1024 * 1024


@dataclass
class FileEntry:
    path: str                       # relative to target root, posix separators
    abs_path: Path
    language: str | None            # None if extension not recognized
    content_hash: str               # sha256 hex
    size: int


@dataclass
class FileManifest:
    target: Target
    files: list[FileEntry] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    # skipped: (path, reason)

    def by_language(self, lang: str) -> list[FileEntry]:
        return [f for f in self.files if f.language == lang]

    def count(self) -> int:
        return len(self.files)


def discover(target: Target) -> FileManifest:
    """Walk the target tree and build a FileManifest.

    Symlinks are not followed — avoids loops and escaping the target.
    Files larger than MAX_FILE_SIZE are skipped with a reason.
    Hidden files are skipped except a small allowlist.
    """
    manifest = FileManifest(target=target)
    root = target.root

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Prune excluded directories in-place (os.walk respects this)
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDE_DIRS and not d.startswith(".")
        ]

        for filename in filenames:
            # Skip hidden files (macOS .DS_Store, editor swap files).
            # Keep a small allowlist of hidden files we actually need.
            if filename.startswith(".") and filename not in ALLOWED_HIDDEN_FILES:
                rel_path = (Path(dirpath) / filename).relative_to(root).as_posix()
                manifest.skipped.append((rel_path, "hidden file"))
                continue

            abs_path = Path(dirpath) / filename
            rel_path = abs_path.relative_to(root).as_posix()

            ext = abs_path.suffix.lower()

            if ext in EXCLUDE_EXTENSIONS:
                manifest.skipped.append((rel_path, f"excluded extension: {ext}"))
                continue

            try:
                size = abs_path.stat().st_size
            except OSError as e:
                manifest.skipped.append((rel_path, f"stat failed: {e}"))
                continue

            if size > MAX_FILE_SIZE:
                manifest.skipped.append(
                    (rel_path, f"file too large: {size} bytes")
                )
                continue

            try:
                content_hash = _hash_file(abs_path)
            except OSError as e:
                manifest.skipped.append((rel_path, f"read failed: {e}"))
                continue

            # Determine language: by extension first, then by exact filename
            # (for files without extension, like Dockerfile).
            lang = LANGUAGE_BY_EXTENSION.get(ext)
            if lang is None:
                lang = FILENAME_LANGUAGE.get(filename)

            manifest.files.append(FileEntry(
                path=rel_path,
                abs_path=abs_path,
                language=lang,
                content_hash=content_hash,
                size=size,
            ))

    manifest.files.sort(key=lambda f: f.path)
    return manifest


def _hash_file(path: Path, chunk_size: int = 65536) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()