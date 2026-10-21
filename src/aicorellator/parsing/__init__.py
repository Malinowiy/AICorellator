"""Parsing — AST extraction via TSA (Phase 1) and SCIP (Phase 2, conditional).

Exports:
  run_tsa           — run TSA worker on a FileManifest
  make_context      — build a NormalizeContext for a normalizer run
  normalize         — run the stage pipeline → Node/Edge
  write_graph       — write GraphDelta into GraphStore
  set_phase1_done   — set phase1_done flag for a host
  TSASymbol         — single symbol found by TSA
  TSAOutput         — full TSA result
  GraphDelta        — nodes and edges produced by normalization
  NormalizeContext  — inputs, caches and derived state for a run
  Classification    — classification result
  classify_class    — classify a class symbol
  classify_method   — classify a method symbol
  classify_file     — classify a whole file (mcp_server)
"""

from .models import TSASymbol, TSAOutput
from .tsa_worker import run as run_tsa
from .classification import (
    Classification,
    classify_class,
    classify_file,
    classify_method,
)
from .ast_normalizer import GraphDelta, normalize
from .normalizer_context import NormalizeContext, make_context
from .writer import set_phase1_done, write as write_graph
from .entry_points import run as run_entry_points
from .llm_extractor import run as run_llm_extractor
from .capability_extractor import (
    FileCapabilities,
    SymbolCapabilities,
    extract as extract_capabilities,
)

__all__ = [
    "TSASymbol",
    "TSAOutput",
    "run_tsa",
    "GraphDelta",
    "NormalizeContext",
    "make_context",
    "normalize",
    "write_graph",
    "run_llm_extractor",
    "set_phase1_done",
    "Classification",
    "classify_class",
    "classify_file",
    "classify_method",
    "run_entry_points",
    "FileCapabilities",
    "SymbolCapabilities",
    "extract_capabilities",
]