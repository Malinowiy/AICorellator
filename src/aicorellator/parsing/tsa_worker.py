"""TSA Worker — runs tree-sitter-analyzer as a subprocess.

TSA is a CLI-first tool. We invoke it via uvx, parse the JSON output,
and normalize into TSASymbol. No Python API is used: TSA's public
interface is the CLI, and running it in isolation avoids pulling its
69 dependencies into our venv.

Only languages TSA actually supports are processed. Other files are
silently skipped.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from ..ingestion.discovery import FileManifest
from ..patterns import TSA_SUPPORTED_LANGUAGES
from .models import TSASymbol, TSAOutput


log = logging.getLogger(__name__)

# Per-file timeout. TSA on a single file should be quick; if it hangs,
# something is wrong.
PER_FILE_TIMEOUT_SECONDS = 60


def run(manifest: FileManifest, root: Path | None = None) -> TSAOutput:
    """Run TSA --structure on every supported file in the manifest.

    Args:
        manifest: file list from ingestion.discover()
        root: target root path. If provided, passed to TSA as
              --project-root so it accepts absolute paths outside
              the current working directory.
    """
    output = TSAOutput()
    project_root = Path(root).resolve() if root is not None else None

    for entry in manifest.files:
        if entry.language not in TSA_SUPPORTED_LANGUAGES:
            continue

        data = _run_structure(entry.abs_path, project_root)
        if data is None:
            output.files_failed += 1
            continue

        output.files_analyzed += 1
        output.symbols.extend(_parse_symbols(data, entry.path))

    return output


def _run_structure(file_path: Path, project_root: Path | None = None) -> dict | None:
    """Run TSA --structure on one file. Returns parsed JSON or None.

    TSA requires --project-root when the file path is absolute and
    outside the current working directory. Pass project_root to
    satisfy this requirement.
    """
    """cmd = [
        "uvx", "--from", "tree-sitter-analyzer",
        "tree-sitter-analyzer",
        str(file_path),
        "--structure", "--format", "json",
    ]"""
    # `uvx` uses the newest available Python by default. As of 2026-10,
    # tree-sitter-analyzer 2.2.0 depends on cachebox 5.2.3, which is a
    # PyO3 extension that does not support Python 3.15. Pin to 3.14.
    # If a future tree-sitter-analyzer release lifts this, remove the pin.
    cmd = [
        "uvx", "--python", "3.14",
        "--from", "tree-sitter-analyzer",
        "tree-sitter-analyzer",
        str(file_path),
        "--structure", "--format", "json",
    ]

    if project_root is not None:
        cmd.extend(["--project-root", str(project_root)])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=PER_FILE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("TSA timeout on %s", file_path)
        return None
    except FileNotFoundError:
        log.error("uvx not found — cannot run TSA")
        return None

    if proc.returncode != 0:
        log.warning(
            "TSA failed on %s (rc=%d): %s",
            file_path, proc.returncode, proc.stderr.strip()[:200],
        )
        return None

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        log.warning("TSA produced non-JSON output on %s", file_path)
        return None


def _parse_symbols(data: dict, file_path: str) -> list[TSASymbol]:
    """Extract classes and methods from TSA's --structure output."""
    symbols: list[TSASymbol] = []

    for cls in data.get("classes", []) or []:
        line_range = cls.get("line_range") or [0, 0]
        symbols.append(TSASymbol(
            name=cls["name"],
            kind="class",
            file_path=file_path,
            line_start=line_range[0],
            line_end=line_range[1],
        ))

    for method in data.get("methods", []) or []:
        line_range = method.get("line_range") or [0, 0]
        symbols.append(TSASymbol(
            name=method["name"],
            kind="method",
            file_path=file_path,
            line_start=line_range[0],
            line_end=line_range[1],
            class_name=method.get("class_name"),
        ))

    return symbols