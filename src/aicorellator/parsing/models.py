"""Data models for TSA output.

These are intermediate — they get normalized into Node/Edge later
by ast_normalizer. Kept separate for testing and clarity.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TSASymbol:
    """A class, method, or function found by TSA in a single file."""

    name: str
    kind: str                       # "class" | "method" | "function"
    file_path: str                  # relative to target root
    line_start: int
    line_end: int
    class_name: str | None = None   # for methods


@dataclass
class TSAOutput:
    """Everything TSA found across the manifest."""

    symbols: list[TSASymbol] = field(default_factory=list)
    precision: str = "syntactic"
    files_analyzed: int = 0
    files_failed: int = 0

    def classes(self) -> list[TSASymbol]:
        return [s for s in self.symbols if s.kind == "class"]

    def methods(self) -> list[TSASymbol]:
        return [s for s in self.symbols if s.kind == "method"]