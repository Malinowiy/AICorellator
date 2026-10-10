"""Parsing — AST extraction via TSA (Phase 1) and SCIP (Phase 2, conditional).

Exports:
  run_tsa           — run TSA worker on a FileManifest
  normalize         — turn TSA output into Node/Edge
  write_graph       — write GraphDelta into GraphStore
  set_phase1_done   — set phase1_done flag for a host
  TSASymbol         — single symbol found by TSA
  TSAOutput         — full TSA result
  GraphDelta        — nodes and edges produced by normalization
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
from .writer import set_phase1_done, write as write_graph

__all__ = [
    "TSASymbol",
    "TSAOutput",
    "run_tsa",
    "GraphDelta",
    "normalize",
    "write_graph",
    "set_phase1_done",
    "Classification",
    "classify_class",
    "classify_file",
    "classify_method",
]