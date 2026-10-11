"""File Discovery — walks a target, applies filters, hashes files.

Produces a FileManifest consumed by TSA, SCIP, and LLM Extractor.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..patterns import (
    ALLOWED_HIDDEN_FILES,
    EXCLUDE_DIRS,
    EXCLUDE_EXTENSIONS,
    FILENAME_LANGUAGE,
    LANGUAGE_BY_EXTENSION,
    MAX_FILE_SIZE,
)
from .target import Target


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