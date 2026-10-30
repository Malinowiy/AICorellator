"""`NormalizeContext` — everything the normalizer stages share.

Before step 4 the normalizer's 13 steps were one function, and the state
they passed to each other lived in five closures over a local scope:
the per-scan module/function/class caches, and the indexes built along
the way (`mcp_servers`, `tools_by_file`, `sinks_by_file`,
`entry_node_by_handler`, the agent indexes). A stage could not be read
or tested without reading the whole function, because half of its
inputs were invisible.

`NormalizeContext` makes that state explicit:

    inputs      what the scan produced — manifest, TSA symbols, entry
                points, LLM configs, capabilities, detector output
    caches      parsed modules and their indexes, per file, per scan
    derived     what earlier stages discovered and later stages need

A stage takes the context and a `GraphDelta`, and returns the delta.
Nothing else is shared, and every cache is keyed by relative file path —
the same spelling the manifest and TSA use.

`make_context` is the public constructor: it fills the optional inputs
with empty objects, which is what `normalize()` used to do inline, and
it is the one place callers touch. It is deliberately *not* a
`normalize(manifest, tsa, ...)` shim — the signature change is the point
of step 4.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ingestion.discovery import FileManifest
    from ..scan_config import ScanConfig
    from .capability_extractor import FileCapabilities
    from .entry_points import EntryPointOutput
    from .framework_detectors import DetectionResult
    from .llm_extractor import LLMExtractorOutput
    from .models import TSAOutput


log = logging.getLogger(__name__)


# A parsed function or class definition, as the file indexes store them.
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass
class NormalizeContext:
    """Inputs, caches and derived state for one normalizer run."""

    manifest: "FileManifest"
    tsa_output: "TSAOutput"
    entry_points: "EntryPointOutput"
    llm_output: "LLMExtractorOutput"
    capabilities: dict[str, "FileCapabilities"]
    framework_detection: "DetectionResult"
    host_id: str = "localhost"

    # --- The store-identity LLM fallback (B.3b-llm_fallback, ADR-0023) ---
    # Three things the store resolver stage has no other way to reach: the
    # scan policy (`llm_fallback.mode` decides whether the layer runs) and
    # the two paths it reads — the config file, for the
    # `llm_fallback_model` section, and D.2's cache file, which the
    # fallback shares. `None` for any of them means "this scan did not
    # offer the fallback", and the stage's third step is then a no-op —
    # which is what every caller that does not pass them gets, the
    # pipeline tests among them. The policy is carried rather than
    # re-read, so a scan reads its config once (`run_scan.py`).
    scan_config: "ScanConfig | None" = None
    config_path: Path | None = None
    cache_path: Path | None = None

    # --- Caches (was: closures in normalize()) ---
    # Three consumers read source beyond the TSA index: the Flow Analyzer
    # for `has_access_to`, the tool/sink body analyzers (1.2a.1), and the
    # `untrusted_source` reader index (1.3a). All go through this cache, so
    # a file is read and parsed at most once per scan. A missing file, an
    # unreadable file or a syntax error yields None — unit tests build
    # manifests whose abs_path does not exist on disk, so the fallback is a
    # normal path, not an error path.
    module_cache: dict[str, ast.Module | None] = field(default_factory=dict)
    functions_cache: dict[
        str, dict[tuple[str, int], FunctionNode]
    ] = field(default_factory=dict)
    classes_cache: dict[
        str, dict[tuple[str, int], ast.ClassDef]
    ] = field(default_factory=dict)
    abs_path_by_path: dict[str, Path] = field(default_factory=dict)

    # --- Derived state (was: local variables in normalize()) ---
    mcp_servers: dict[str, str] = field(default_factory=dict)
    tools_by_file: dict[str, list] = field(default_factory=dict)
    sinks_by_file: dict[str, list] = field(default_factory=dict)
    entry_node_by_handler: dict[str, str] = field(default_factory=dict)
    agent_node_by_name: dict[str, str] = field(default_factory=dict)
    agent_node_by_file: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.abs_path_by_path:
            self.abs_path_by_path = {
                entry.path: entry.abs_path for entry in self.manifest.files
            }

    # --------------------------------------------------------
    # Source access (cached per scan)
    # --------------------------------------------------------

    def module_for(self, file_path: str) -> ast.Module | None:
        """Parsed module for a manifest file, cached per scan."""
        if file_path in self.module_cache:
            return self.module_cache[file_path]
        from .normalizer_helpers import parse_python_file

        module = parse_python_file(self.abs_path_by_path.get(file_path))
        self.module_cache[file_path] = module
        return module

    def functions_for(
        self, file_path: str
    ) -> dict[tuple[str, int], FunctionNode]:
        """Function index for a manifest file, cached per scan.

        Keyed by `(name, line_start)` (1.2a.3f) — see
        `normalizer_helpers.index_functions`.
        """
        if file_path in self.functions_cache:
            return self.functions_cache[file_path]
        from .normalizer_helpers import index_functions

        module = self.module_for(file_path)
        functions = index_functions(module) if module is not None else {}
        self.functions_cache[file_path] = functions
        return functions

    def classes_for(
        self, file_path: str
    ) -> dict[tuple[str, int], ast.ClassDef]:
        """Class index for a manifest file, cached per scan.

        Keyed by `(name, line_start)` (1.2a.3f) — see
        `normalizer_helpers.index_classes`.
        """
        if file_path in self.classes_cache:
            return self.classes_cache[file_path]
        from .normalizer_helpers import index_classes

        module = self.module_for(file_path)
        classes = index_classes(module) if module is not None else {}
        self.classes_cache[file_path] = classes
        return classes


def make_context(
    manifest: "FileManifest",
    tsa_output: "TSAOutput",
    entry_points: "EntryPointOutput | None" = None,
    llm_output: "LLMExtractorOutput | None" = None,
    capabilities: dict[str, "FileCapabilities"] | None = None,
    framework_detection: "DetectionResult | None" = None,
    host_id: str = "localhost",
    scan_config: "ScanConfig | None" = None,
    config_path: Path | None = None,
    cache_path: Path | None = None,
) -> NormalizeContext:
    """Build a context, filling the optional inputs with empty objects.

    The public constructor for a normalizer run. The empty-object
    defaults are what `normalize()` used to apply inline: a missing
    input means "this scan did not run that detector", not "skip the
    stages that consume it".

    `scan_config`, `config_path` and `cache_path` are the store-identity
    fallback's (ADR-0023) and default to `None`, which is "no fallback".
    A caller that does not offer the layer passes nothing.
    """
    from .entry_points import EntryPointOutput
    from .framework_detectors import DetectionResult
    from .llm_extractor import LLMExtractorOutput

    return NormalizeContext(
        manifest=manifest,
        tsa_output=tsa_output,
        entry_points=entry_points or EntryPointOutput(),
        llm_output=llm_output or LLMExtractorOutput(),
        capabilities=capabilities or {},
        framework_detection=framework_detection or DetectionResult(),
        host_id=host_id,
        scan_config=scan_config,
        config_path=config_path,
        cache_path=cache_path,
    )
