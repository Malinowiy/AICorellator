"""Framework detectors runner.

Runs all enabled framework detectors across a file manifest and
merges their results into a single DetectionResult.

The runner is independent from ast_normalizer. It is invoked by
run_scan before normalize; its output is passed to normalize as
a separate parameter.
"""

from __future__ import annotations

import ast
import logging

from ..ingestion.discovery import FileManifest
from .framework_detectors import DetectionResult, FrameworkDetector
from .framework_detectors.registry import resolve


log = logging.getLogger(__name__)


def run(
    manifest: FileManifest,
    enabled: list[str] | None = None,
    host_id: str = "localhost",
) -> DetectionResult:
    """Run all enabled detectors across Python files in the manifest.

    Args:
        manifest: from ingestion.discover().
        enabled: names of detectors to run ("langchain", "langgraph", ...).
                 If None, runs all registered detectors.
        host_id: host identifier for node IDs.

    Returns:
        A single DetectionResult with all nodes and edges merged.
    """
    if enabled is None:
        from .framework_detectors.registry import all_names
        enabled = all_names()

    detectors = resolve(enabled)
    if not detectors:
        return DetectionResult(framework="")

    merged = DetectionResult(framework=",".join(d.name for d in detectors))

    for entry in manifest.files:
        if entry.language != "python":
            continue

        try:
            source = entry.abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning("cannot read %s: %s", entry.path, e)
            continue

        try:
            tree = ast.parse(source, filename=str(entry.abs_path))
        except SyntaxError as e:
            log.warning("syntax error in %s: %s", entry.path, e)
            continue

        for detector in detectors:
            try:
                result = detector.detect(entry.path, tree, source, host_id=host_id)
            except Exception:
                log.exception(
                    "detector %s failed on %s", detector.name, entry.path,
                )
                continue

            if result.nodes:
                merged.nodes.extend(result.nodes)
            if result.edges:
                merged.edges.extend(result.edges)

    return merged